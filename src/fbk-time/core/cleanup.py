"""Thread-based automatic cleanup for expired login attempts.

Runs periodically to remove expired lockout records from the database.
"""

import fcntl
import os
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from core.settings_manager import settings_manager


class LoginAttemptCleanupScheduler:
    """Scheduler for automatic login attempt cleanup."""

    def __init__(self, app=None):
        self.app = app
        self.cleanup_thread = None
        self.is_running = False
        self.lock_fd = None
        self.next_cleanup_time = None

        if app is not None:
            self.init_app(app)

    def _get_cleanup_enabled(self):
        """Get cleanup enabled setting."""
        return settings_manager.get('lockout_cleanup_enabled')

    def _get_cleanup_interval_hours(self):
        """Get cleanup interval hours setting."""
        return settings_manager.get('lockout_cleanup_interval_hours')

    def init_app(self, app):
        """Initialize scheduler with Flask app."""
        self.app = app

        if self._get_cleanup_enabled():
            self._setup_scheduler()
            app.logger.info("LoginAttempt-Cleanup-Scheduler initialized")

    def _setup_scheduler(self):
        """Configure next cleanup time."""
        self.next_cleanup_time = datetime.now(timezone.utc) + timedelta(
            hours=self._get_cleanup_interval_hours()
        )

    def start_cleanup_scheduler(self):
        """Start the background cleanup thread with file locking."""
        if not self._get_cleanup_enabled():
            return

        if self.cleanup_thread and self.cleanup_thread.is_alive():
            if self.app:
                self.app.logger.warning("Cleanup scheduler already running")
            return

        if self.lock_fd:
            try:
                self.lock_fd.close()
            except Exception:
                pass

        base_dir = Path(__file__).resolve().parent.parent
        lock_file = base_dir / 'data' / 'cleanup.lock'

        try:
            lock_file.parent.mkdir(parents=True, exist_ok=True)
            self.lock_fd = open(lock_file, 'w')
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

            if self.app:
                self.app.logger.info(
                    f"Worker {os.getpid()} acquired cleanup lock"
                )

            self._scheduled_cleanup()

            self.is_running = True
            self.cleanup_thread = threading.Thread(
                target=self._run_scheduler,
                daemon=True
            )
            self.cleanup_thread.start()

            if self.app:
                self.app.logger.info("Cleanup scheduler started")

        except BlockingIOError:
            if self.app:
                self.app.logger.info("Cleanup already running in another worker")
            return
        except Exception as e:
            if self.app:
                self.app.logger.error(f"Failed to start cleanup scheduler: {e}")
            return

    def stop_cleanup_scheduler(self):
        """Stop the cleanup scheduler and release lock."""
        self.is_running = False
        if self.cleanup_thread and self.cleanup_thread.is_alive():
            self.cleanup_thread.join(timeout=5)

        if self.lock_fd:
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
                self.lock_fd.close()
            except Exception as e:
                if self.app:
                    self.app.logger.warning(f"Error releasing cleanup lock: {e}")

        if self.app:
            self.app.logger.info("Cleanup scheduler stopped")

    def _run_scheduler(self):
        """Background thread loop for scheduled cleanup."""
        while self.is_running:
            try:
                current_time = datetime.now(timezone.utc)

                if current_time >= self.next_cleanup_time:
                    self._scheduled_cleanup()
                    self.next_cleanup_time = current_time + timedelta(
                        hours=self._get_cleanup_interval_hours()
                    )

                time.sleep(300)

            except Exception as e:
                if self.app:
                    self.app.logger.error(f"Error in cleanup scheduler: {e}")
                time.sleep(900)

    def _scheduled_cleanup(self):
        """Execute scheduled cleanup within app context."""
        try:
            with self.app.app_context():
                from modules.auth.services import (
                    cleanup_expired_lockouts,
                    deactivate_inactive_accounts
                )

                lockout_count = cleanup_expired_lockouts()
                inactive_count = deactivate_inactive_accounts()

                if self.app and lockout_count > 0:
                    self.app.logger.info(
                        f"Automatic cleanup: {lockout_count} expired lockout records removed"
                    )

                if self.app and inactive_count > 0:
                    self.app.logger.info(
                        f"Automatic cleanup: {inactive_count} inactive accounts disabled"
                    )

        except Exception as e:
            if self.app:
                self.app.logger.error(f"Automatic cleanup failed: {e}")

    def get_next_cleanup_time(self):
        """Return the next scheduled cleanup time."""
        return self.next_cleanup_time


cleanup_scheduler = LoginAttemptCleanupScheduler()


def schedule_cleanup(app):
    """Initialize and start the cleanup scheduler."""
    cleanup_scheduler.init_app(app)
    cleanup_scheduler.start_cleanup_scheduler()


def stop_cleanup():
    """Stop the cleanup scheduler."""
    cleanup_scheduler.stop_cleanup_scheduler()


def get_cleanup_status():
    """Return current cleanup scheduler status."""
    return {
        'enabled': settings_manager.get('lockout_cleanup_enabled'),
        'running': cleanup_scheduler.is_running,
        'interval_hours': settings_manager.get('lockout_cleanup_interval_hours'),
        'next_cleanup': cleanup_scheduler.get_next_cleanup_time()
    }
