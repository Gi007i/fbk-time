"""Generic application task scheduler.

A daemon thread runs in every Gunicorn worker; a process-bound advisory lock
elects one leader so tasks run once, and another worker takes over within
one tick if the leader exits. Settings changes (``cache_version`` bumps)
re-register the tasks without a restart.

Schedules are process-local. A daily slot passed within ``CATCH_UP_GRACE``
still runs after a restart, takeover or reload, so daily tasks must be
idempotent per slot; interval timing across a leader change is best-effort.
"""

import fcntl
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable, List, Optional

from core.timezone import get_app_timezone

# A daily slot that passed within this window is still run when a task is
# registered or a worker takes over, so a restart or leader change right
# after the slot does not skip the day.
CATCH_UP_GRACE = timedelta(minutes=30)


@dataclass
class _Task:
    name: str
    func: Callable
    interval_hours: float
    daily_at: Optional[str] = None  # local "HH:MM" pinning a wall-clock task
    timezone_key: Optional[str] = None  # zone daily_at was resolved in
    next_run: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def due(self, now: datetime) -> bool:
        return now >= self.next_run

    @property
    def spec(self) -> tuple:
        return (self.interval_hours, self.daily_at, self.timezone_key)

    def reschedule(self, now: datetime) -> None:
        """Compute the next run time.

        Daily tasks recompute their next occurrence, so the time stays
        pinned across DST changes and never drifts over days. Interval
        tasks advance by their fixed interval.
        """
        if self.daily_at is not None:
            self.next_run = _next_daily_run(self.daily_at)
        else:
            self.next_run = now + timedelta(hours=self.interval_hours)


class AppScheduler:
    """Thread-based scheduler for periodic application tasks."""

    # Tick interval for task firing and settings-change detection.
    # A wall-clock-anchored task fires within this many seconds after its
    # scheduled time, but its schedule does not drift over days.
    _POLL_INTERVAL = 60

    def __init__(self, app=None):
        self.app = app
        self._tasks: List[_Task] = []
        self._thread: Optional[threading.Thread] = None
        self._lock_fd: Optional[int] = None
        self._is_leader = False
        self._last_seen_cache_version = 0

        if app is not None:
            self.init_app(app)

    def init_app(self, app) -> None:
        self.app = app

    def add_task(self, name: str, func: Callable, interval_hours: float,
                 delay_hours: Optional[float] = None,
                 daily_at: Optional[str] = None) -> None:
        """Register a periodic task.

        Args:
            name: Unique task identifier used in log messages.
            func: Callable executed within the Flask app context.
            interval_hours: Interval between executions (used without daily_at).
            delay_hours: Initial delay before first run (defaults to interval).
            daily_at: Local "HH:MM" pinning the task to a wall-clock time;
                overrides interval-based scheduling. Resolved here and on
                every reschedule, so it must run within an app context.
        """
        timezone_key = None
        if daily_at is not None:
            timezone_key = get_app_timezone().key
            next_run = _next_daily_run(daily_at, grace=CATCH_UP_GRACE)
        else:
            initial_delay = delay_hours if delay_hours is not None else interval_hours
            next_run = datetime.now(timezone.utc) + timedelta(hours=initial_delay)
        self._tasks.append(_Task(name=name, func=func, interval_hours=interval_hours,
                                 daily_at=daily_at, timezone_key=timezone_key,
                                 next_run=next_run))

    def clear_tasks(self) -> None:
        """Drop the current task list (used before re-registration)."""
        self._tasks = []

    def start(self) -> None:
        """Start the scheduler thread.

        Starts even without tasks, so enabling one later takes effect on
        reload.
        """
        if self._thread and self._thread.is_alive():
            if self.app:
                self.app.logger.warning("Scheduler already running")
            return

        self._last_seen_cache_version = self._read_cache_version()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

        if self.app:
            task_names = ', '.join(t.name for t in self._tasks) or '(none)'
            self.app.logger.info(f"Scheduler thread started; tasks: {task_names}")

    def _acquire_lock(self) -> bool:
        """Try to become the single task-executing worker.

        ``lockf`` instead of ``flock``: the lock ends with the worker and is
        never inherited across a fork, so a surviving worker can take over.

        Returns:
            True if this process holds the lock.
        """
        if self._lock_fd is not None:
            return True

        lock_file = Path(self.app.config['RUNTIME_DIR']) / 'scheduler.lock'
        try:
            # O_NOFOLLOW: a planted symlink must not redirect the open.
            fd = os.open(lock_file, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        except OSError as e:
            if self.app:
                self.app.logger.error(f"Failed to open scheduler lock: {e}")
            return False
        try:
            fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        except Exception as e:
            os.close(fd)
            if self.app:
                self.app.logger.error(f"Failed to acquire scheduler lock: {e}")
            return False

        self._lock_fd = fd
        if self.app:
            self.app.logger.info(f"Worker {os.getpid()} acquired scheduler lock")
        return True

    def _loop(self) -> None:
        while True:
            try:
                if not self._acquire_lock():
                    time.sleep(self._POLL_INTERVAL)
                    continue

                if not self._is_leader:
                    self._is_leader = True
                    self._reanchor()

                now = datetime.now(timezone.utc)

                current_version = self._read_cache_version()
                if (current_version != self._last_seen_cache_version
                        and self._reload_tasks()):
                    self._last_seen_cache_version = current_version

                for task in self._tasks:
                    if task.due(now):
                        self._run_task(task, now)

                time.sleep(self._POLL_INTERVAL)
            except Exception as e:
                if self.app:
                    self.app.logger.error(f"Scheduler loop error: {e}")
                time.sleep(self._POLL_INTERVAL * 3)

    def _read_cache_version(self) -> int:
        """Read cache_version directly from the database.

        Bypasses the settings_manager cache so a change committed by
        another worker is visible immediately on the next tick.
        """
        if self.app is None:
            return self._last_seen_cache_version

        try:
            with self.app.app_context():
                from core.db import db
                from modules.settings.models import Setting
                setting = db.session.get(Setting, 'cache_version')
                return setting.get_typed_value() if setting else 0
        except Exception as e:
            if self.app:
                self.app.logger.error(f"Failed to read cache_version: {e}")
            return self._last_seen_cache_version

    def _reload_tasks(self) -> bool:
        """Rebuild the task list from current settings.

        Unchanged tasks keep their schedule, so a reload neither restarts an
        interval countdown nor skips a due slot. A failed reload keeps the
        previous task list.

        Returns:
            True if the task list was rebuilt, False if the reload failed
            and should be retried on the next tick.
        """
        from core.settings_manager import settings_manager

        previous = self._tasks
        self._tasks = []
        try:
            with self.app.app_context():
                # The cache of this worker may have been checked within the
                # throttle interval and still hold the values before the change.
                settings_manager.refresh()
                _register_tasks(self.app)
        except Exception as e:
            self._tasks = previous
            if self.app:
                self.app.logger.error(f"Scheduler task reload failed: {e}")
            return False

        previous_by_name = {task.name: task for task in previous}
        for task in self._tasks:
            old = previous_by_name.get(task.name)
            if old is not None and old.spec == task.spec:
                task.next_run = old.next_run

        if self.app:
            task_names = ', '.join(t.name for t in self._tasks) or '(none)'
            self.app.logger.info(f"Scheduler reloaded — active tasks: {task_names}")
        return True

    def _reanchor(self) -> None:
        """Re-pin daily tasks after acquiring leadership.

        A follower's schedule dates from its startup, so the slot it holds
        may be long gone. Interval tasks keep their schedule so a takeover
        never postpones them.
        """
        try:
            with self.app.app_context():
                for task in self._tasks:
                    if task.daily_at is not None:
                        task.next_run = _next_daily_run(task.daily_at, grace=CATCH_UP_GRACE)
        except Exception as e:
            if self.app:
                self.app.logger.error(f"Scheduler re-anchor failed: {e}")

    def _run_task(self, task: _Task, now: datetime) -> None:
        try:
            with self.app.app_context():
                task.func()
            if self.app:
                self.app.logger.debug(f"Scheduler task '{task.name}' completed")
        except Exception as e:
            if self.app:
                self.app.logger.error(f"Scheduler task '{task.name}' failed: {e}")
        finally:
            # Anchored tasks read settings to reschedule; a failed read must
            # not leave next_run in the past and refire on every tick.
            try:
                with self.app.app_context():
                    task.reschedule(now)
            except Exception as e:
                if self.app:
                    self.app.logger.error(
                        f"Scheduler reschedule of '{task.name}' failed: {e}"
                    )
                task.next_run = now + timedelta(hours=task.interval_hours)


app_scheduler = AppScheduler()


def _cleanup_task() -> None:
    """Cleanup expired lockouts and deactivate inactive accounts."""
    from modules.auth.services import cleanup_expired_lockouts, deactivate_inactive_accounts
    from flask import current_app

    lockout_count = cleanup_expired_lockouts()
    inactive_count = deactivate_inactive_accounts()

    if lockout_count > 0:
        current_app.logger.info(
            f"Cleanup: {lockout_count} expired lockout records removed"
        )
    if inactive_count > 0:
        current_app.logger.info(
            f"Cleanup: {inactive_count} inactive accounts disabled"
        )


def _backup_task() -> None:
    """Create a scheduled database backup and prune old archives.

    Cleanup runs only after a successful backup, so a failed run never
    deletes archives without a replacement.
    """
    from core.backup import backup_manager
    from core.settings_manager import settings_manager
    from flask import current_app
    from modules.backup.services import latest_usable_scheduled_backup_at

    # Catch-up after a restart or takeover may hit a slot the previous
    # leader already completed.
    slot = last_daily_run(settings_manager.get('backup_time')).replace(tzinfo=None)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    last_at = latest_usable_scheduled_backup_at(not_after=now)
    if last_at is not None and last_at >= slot:
        current_app.logger.info("Scheduled backup for this slot already exists, skipping")
        return

    record = backup_manager.create_backup(
        description='Scheduled backup',
        backup_type='scheduled'
    )
    if not record:
        return

    current_app.logger.info(f"Scheduled backup created: {record.id}")

    removed = backup_manager.cleanup_old_backups()
    if removed:
        current_app.logger.info(f"Backup retention: {removed} old backups removed")


def _register_tasks(app) -> None:
    """Add all enabled tasks to the scheduler based on current settings."""
    from core.settings_manager import settings_manager

    if settings_manager.get('lockout_cleanup_enabled'):
        interval = settings_manager.get('lockout_cleanup_interval_hours')
        app_scheduler.add_task(
            name='cleanup',
            func=_cleanup_task,
            interval_hours=interval,
            delay_hours=interval
        )

    if settings_manager.get('backup_scheduled_enabled'):
        backup_time = settings_manager.get('backup_time')
        try:
            _next_slot(backup_time)
        except ValueError:
            app.logger.error(
                "backup_time has invalid format (expected HH:MM) — "
                "scheduled backup not registered"
            )
        else:
            app_scheduler.add_task(
                name='backup',
                func=_backup_task,
                interval_hours=24,
                daily_at=backup_time
            )


def start_scheduler(app) -> None:
    """Register all enabled tasks and start the scheduler.

    Opens its own app context, since registration reads settings and the
    callers provide none.
    """
    app_scheduler.init_app(app)
    app_scheduler.clear_tasks()
    with app.app_context():
        _register_tasks(app)
    app_scheduler.start()


def _next_daily_run(time_str: str, grace: timedelta = timedelta(0)) -> datetime:
    """Return the UTC datetime of the next daily slot.

    A slot passed within ``grace`` is returned instead, so registration and
    takeover shortly after the slot still run it. Needs an app context.

    Args:
        time_str: Local time in "HH:MM" format.
        grace: How long a passed slot still counts as pending.

    Raises:
        ValueError: If time_str is not a valid "HH:MM" string.
    """
    now = datetime.now(timezone.utc)
    if grace:
        recent = last_daily_run(time_str)
        if now - recent <= grace:
            return recent
    return _next_slot(time_str)


def last_daily_run(time_str: str) -> datetime:
    """Return the most recent UTC datetime matching a daily local time of day.

    Used to check whether a scheduled task has run. Needs an app context.

    Args:
        time_str: Local time in "HH:MM" format.

    Raises:
        ValueError: If time_str is not a valid "HH:MM" string.
    """
    hour, minute = _parse_hhmm(time_str)
    tz = get_app_timezone()
    now_local = datetime.now(tz)

    # Step the base date back, not the anchored result: a slot in a
    # spring-forward gap is moved behind the gap, and stepping back from that
    # shifted wall-clock time would carry the shift into the previous day.
    # Compare instants, not wall-clock: same-zone comparisons ignore fold.
    base = now_local
    candidate = _anchor_local(base, hour, minute, tz)
    while candidate.timestamp() > now_local.timestamp():
        base -= timedelta(days=1)
        candidate = _anchor_local(base, hour, minute, tz)

    return candidate.astimezone(timezone.utc)


def _parse_hhmm(time_str: str) -> tuple[int, int]:
    """Parse a strict "HH:MM" string into hour and minute.

    Raises:
        ValueError: If time_str is not a valid "HH:MM" string.
    """
    parts = time_str.split(':')
    if len(parts) != 2:
        raise ValueError(f'Ungültiges Zeitformat: {time_str!r}')
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError(f'Ungültiges Zeitformat: {time_str!r}') from exc
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f'Ungültiges Zeitformat: {time_str!r}')
    return hour, minute


def _next_slot(time_str: str) -> datetime:
    """Return the UTC instant of the next occurrence of a daily time of day.

    Interpreted in the app timezone; DST handling see ``_anchor_local``.

    Args:
        time_str: Local time in "HH:MM" format.

    Returns:
        Timezone-aware UTC datetime strictly after now.

    Raises:
        ValueError: If time_str is not a valid "HH:MM" string.
    """
    hour, minute = _parse_hhmm(time_str)

    tz = get_app_timezone()
    now_local = datetime.now(tz)

    # Compare instants, not wall-clock: same-zone comparisons ignore fold.
    candidate = _anchor_local(now_local, hour, minute, tz)
    if candidate.timestamp() <= now_local.timestamp():
        candidate = _anchor_local(now_local + timedelta(days=1), hour, minute, tz)

    return candidate.astimezone(timezone.utc)


def _anchor_local(base, hour: int, minute: int, tz) -> datetime:
    """Return ``base``'s date at hour:minute local time, shifted past DST gaps.

    ``fold=0`` runs an ambiguous fall-back time once, at its earlier
    occurrence. A time in a spring-forward gap fails the UTC round trip and
    moves to the first valid minute after the gap (cron-like).
    """
    candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0, fold=0)
    while candidate.astimezone(timezone.utc).astimezone(tz) != candidate:
        candidate += timedelta(minutes=1)
    return candidate
