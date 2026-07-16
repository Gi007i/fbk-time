"""Generic application task scheduler.

Runs periodic background tasks (cleanup, backup) in a daemon thread that
is started in every Gunicorn worker. A process-bound advisory lock elects
a single leader so tasks execute exactly once; workers that do not hold
the lock keep polling and take over within one tick if the leader exits
(e.g. on worker recycling), so scheduling survives the loss of any worker.

The leader polls ``cache_version`` once per tick. When another worker
commits a settings change (which always bumps the version), the leader
re-registers its tasks — so admin changes in the UI take effect within at
most ``_POLL_INTERVAL`` seconds without a restart.

Task schedules are process-local and not persisted: each worker registers
its tasks from its own start time, and a new leader re-anchors wall-clock
tasks on takeover. Interval tasks therefore offer best-effort timing across
a leader change rather than a hard guarantee — acceptable here because the
worker recycle interval stays well above the task intervals under the
expected low-throughput, offline workload.
"""

import fcntl
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable, List, Optional

from core.timezone import get_app_timezone


@dataclass
class _Task:
    name: str
    func: Callable
    interval_hours: float
    next_run: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    anchor: Optional[Callable[[], datetime]] = None

    def due(self, now: datetime) -> bool:
        return now >= self.next_run

    def reschedule(self, now: datetime) -> None:
        """Compute the next run time.

        Anchored (wall-clock) tasks recompute their next occurrence, so a
        daily time stays pinned across DST changes and never drifts over
        days. Interval tasks advance by their fixed interval.
        """
        if self.anchor is not None:
            self.next_run = self.anchor()
        else:
            self.next_run = now + timedelta(hours=self.interval_hours)


class AppScheduler:
    """Thread-based scheduler for periodic application tasks.

    A process-bound advisory lock elects a single leader among the workers
    so tasks run exactly once. Tasks are added before start() and executed
    in a daemon thread. The leader detects settings changes via
    cache_version and rebuilds its task list without a service restart;
    if the leader exits, another worker acquires the lock and takes over.
    """

    # Tick interval for task firing and settings-change detection.
    # A wall-clock-anchored task fires within this many seconds after its
    # scheduled time, but its schedule does not drift over days.
    _POLL_INTERVAL = 60

    def __init__(self, app=None):
        self.app = app
        self._tasks: List[_Task] = []
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock_fd = None
        self._is_leader = False
        self._stop_event = threading.Event()
        self._last_seen_cache_version = 0

        if app is not None:
            self.init_app(app)

    def init_app(self, app) -> None:
        self.app = app

    def add_task(self, name: str, func: Callable, interval_hours: float,
                 delay_hours: Optional[float] = None,
                 anchor: Optional[Callable[[], datetime]] = None) -> None:
        """Register a periodic task.

        Args:
            name: Unique task identifier used in log messages.
            func: Callable executed within the Flask app context.
            interval_hours: Interval between executions (used when no anchor).
            delay_hours: Initial delay before first run (defaults to interval).
            anchor: Optional callable returning the next UTC run time for a
                wall-clock-pinned task; overrides interval-based scheduling.
                Evaluated here and on every reschedule, so it must run within
                an app context.
        """
        if anchor is not None:
            next_run = anchor()
        else:
            initial_delay = delay_hours if delay_hours is not None else interval_hours
            next_run = datetime.now(timezone.utc) + timedelta(hours=initial_delay)
        self._tasks.append(_Task(name=name, func=func, interval_hours=interval_hours,
                                 next_run=next_run, anchor=anchor))

    def clear_tasks(self) -> None:
        """Drop the current task list (used before re-registration)."""
        self._tasks = []

    def start(self) -> None:
        """Start the scheduler thread.

        The thread runs in every worker, but only the worker that holds
        the advisory lock executes tasks. The others poll for the lock
        once per tick and take over within one interval if the current
        holder exits (e.g. on worker recycling), so scheduling survives
        the loss of any single worker without a service restart.
        """
        if not self._tasks:
            return

        if self._thread and self._thread.is_alive():
            if self.app:
                self.app.logger.warning("Scheduler already running")
            return

        self._last_seen_cache_version = self._read_cache_version()
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

        if self.app:
            task_names = ', '.join(t.name for t in self._tasks)
            self.app.logger.info(f"Scheduler thread started; tasks: {task_names}")

    def _acquire_lock(self) -> bool:
        """Try to become the single task-executing worker.

        Returns True if this process already holds or just acquired the
        process-bound advisory lock. Uses ``fcntl.lockf`` (process-bound),
        not ``flock`` (OFD-bound): the lock is released cleanly when the
        worker exits and is never inherited across a later fork, so a
        surviving worker can take over after the holder recycles.
        """
        if self._lock_fd is not None:
            return True

        lock_file = Path(self.app.config['RUNTIME_DIR']) / 'scheduler.lock'
        fd = open(lock_file, 'w')
        try:
            fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fd.close()
            return False
        except Exception as e:
            fd.close()
            if self.app:
                self.app.logger.error(f"Failed to acquire scheduler lock: {e}")
            return False

        self._lock_fd = fd
        if self.app:
            self.app.logger.info(f"Worker {os.getpid()} acquired scheduler lock")
        return True

    def stop(self) -> None:
        """Signal the scheduler thread to exit and wait for it.

        The file lock is retained so the same worker can resume scheduling
        after re-registering tasks (used by hot reload).
        """
        if not self._running:
            return

        self._running = False
        self._stop_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        self._thread = None
        self._stop_event.clear()

    def _loop(self) -> None:
        while self._running:
            try:
                if not self._acquire_lock():
                    if self._stop_event.wait(self._POLL_INTERVAL):
                        break
                    continue

                if not self._is_leader:
                    # First tick after acquiring the lock. Re-anchor
                    # wall-clock tasks so a daily time that elapsed while
                    # this worker was a follower pins to its next real
                    # occurrence instead of firing stale. Interval tasks keep
                    # the schedule set at startup, so a takeover never
                    # postpones them — which could otherwise starve a
                    # frequently recycling fleet.
                    self._is_leader = True
                    self._reanchor()

                now = datetime.now(timezone.utc)

                current_version = self._read_cache_version()
                if current_version != self._last_seen_cache_version:
                    self._last_seen_cache_version = current_version
                    self._reload_tasks()

                for task in self._tasks:
                    if task.due(now):
                        self._run_task(task, now)

                if self._stop_event.wait(self._POLL_INTERVAL):
                    break
            except Exception as e:
                if self.app:
                    self.app.logger.error(f"Scheduler loop error: {e}")
                if self._stop_event.wait(self._POLL_INTERVAL * 3):
                    break

    def _read_cache_version(self) -> int:
        """Read cache_version directly from the database.

        Bypasses the settings_manager cache so a change committed by
        another worker is visible immediately on the next tick.
        """
        if self.app is None:
            return self._last_seen_cache_version

        try:
            with self.app.app_context():
                from core.extensions import db
                from modules.settings.models import Setting
                setting = db.session.get(Setting, 'cache_version')
                return setting.get_typed_value() if setting else 0
        except Exception as e:
            if self.app:
                self.app.logger.error(f"Failed to read cache_version: {e}")
            return self._last_seen_cache_version

    def _reload_tasks(self) -> None:
        """Rebuild the task list from current settings.

        Called from the scheduler thread when a settings change is
        detected. Existing task schedules are discarded; new tasks
        start from their initial-delay window.
        """
        self._tasks = []
        try:
            with self.app.app_context():
                _register_tasks(self.app)
        except Exception as e:
            if self.app:
                self.app.logger.error(f"Scheduler task reload failed: {e}")
            return

        if self.app:
            task_names = ', '.join(t.name for t in self._tasks) or '(none)'
            self.app.logger.info(f"Scheduler reloaded — active tasks: {task_names}")

    def _reanchor(self) -> None:
        """Re-evaluate wall-clock anchors after acquiring leadership.

        A follower's anchored tasks were pinned at this worker's startup;
        by the time it becomes leader that time may have passed. Re-anchoring
        moves them to their next real occurrence so they do not fire stale.
        Interval-only tasks are left untouched.
        """
        try:
            with self.app.app_context():
                for task in self._tasks:
                    if task.anchor is not None:
                        task.next_run = task.anchor()
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
            # Reschedule inside an app context: an anchored task derives its
            # next run from settings (timezone, backup time), which may read
            # the database. Guard it so a transient read failure cannot leave
            # next_run in the past and refire the task on every tick; fall
            # back to the fixed interval until the next successful reschedule.
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

    Backup creation and retention cleanup run together: cleanup only
    happens after a successful backup, so a failed run never deletes
    archives without a fresh replacement.
    """
    from core.backup import backup_manager
    from flask import current_app

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
            _seconds_until(backup_time)
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
                anchor=lambda t=backup_time: _next_daily_run(t)
            )


def start_scheduler(app) -> None:
    """Register all enabled tasks and start the scheduler.

    Task registration runs inside an app context because it reads settings
    from the database; the callers (Gunicorn ``post_fork`` and the
    standalone entry point) do not provide one.
    """
    app_scheduler.init_app(app)
    app_scheduler.clear_tasks()
    with app.app_context():
        _register_tasks(app)
    app_scheduler.start()


def _next_daily_run(time_str: str) -> datetime:
    """Return the next UTC datetime matching a daily local time of day.

    Used as a task anchor so the scheduled backup re-pins to its configured
    wall-clock time on every reschedule, immune to DST shifts and tick drift.
    Must be called within an app context (reads the timezone setting).
    """
    return datetime.now(timezone.utc) + timedelta(seconds=_seconds_until(time_str))


def _seconds_until(time_str: str) -> int:
    """Calculate seconds until the next occurrence of a daily time of day.

    The configured time is interpreted in the active application
    timezone (admin setting ``app_timezone``). DST transitions are
    handled explicitly:

    * Spring forward (non-existent local time): the candidate is moved
      forward day by day until it falls outside the DST gap. For a
      daily-recurring 02:30 backup with a 02:00→03:00 jump, this skips
      the affected day and resumes on the following one.
    * Fall back (ambiguous local time): ``fold=0`` selects the earlier
      occurrence (still DST), so the backup runs once on the transition
      day rather than twice.

    Delta is computed via UTC timestamps so wall-clock additions cannot
    drift across DST boundaries.

    Args:
        time_str: Local time in "HH:MM" format.

    Returns:
        Seconds until the next occurrence (always >= 1).

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

    tz = get_app_timezone()
    now_local = datetime.now(tz)

    candidate = _anchor_local(now_local, hour, minute, tz)
    if candidate <= now_local:
        candidate = _anchor_local(candidate + timedelta(days=1), hour, minute, tz)

    delta = candidate.timestamp() - now_local.timestamp()
    return max(int(delta), 1)


def _anchor_local(base, hour: int, minute: int, tz) -> datetime:
    """Return ``base``'s date at hour:minute local time, skipping DST gaps.

    Sets ``fold=0`` so ambiguous fall-back times resolve to the earlier
    (DST) occurrence. Detects spring-forward gaps by round-tripping
    through UTC: when the result does not survive the round trip, the
    requested local time does not exist on that date and the next day is
    tried instead. The loop terminates because DST transitions occur at
    most twice a year.
    """
    candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0, fold=0)
    while candidate.astimezone(timezone.utc).astimezone(tz) != candidate:
        candidate = (candidate + timedelta(days=1)).replace(
            hour=hour, minute=minute, second=0, microsecond=0, fold=0
        )
    return candidate
