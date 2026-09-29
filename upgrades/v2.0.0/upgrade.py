#!/usr/bin/env python3
"""Upgrade runner for release v2.0.0.

Database changes:
    - users.start_page (VARCHAR(20), NOT NULL, default 'dashboard') and
      users.view_scope (VARCHAR(20), NOT NULL, default 'all'); the backfill
      keeps the prior behaviour.
    - login_attempts.identifier_type (VARCHAR(10), NOT NULL) separates the
      username and address counters; legacy 'ip:' rows are discarded and the
      indexes rebuilt.
    - Orphaned category overrides in recurrence_exceptions are cleared.
    - Every user receives a fresh random credential_version, which ends all
      sessions and remember-me cookies.

The new runtime settings are seeded by the application on startup. With
--app-path, an upgrade first checks the settings.json values v2.0.0 enforces
at startup and aborts without change if one is invalid. Requires the v1.6.0
layout; backs up the database file, then runs in a single transaction that
rolls back on any failure. Needs only the stdlib and ``sqlite_runner`` from
the parent directory, whose bundled SQLite is a fallback for an unexpectedly
old system SQLite.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sqlite_runner


TARGET_VERSION = '2.0.0'
SUPPORTED_FROM_VERSIONS = '1.6.x'
# ALTER TABLE ADD COLUMN with a constant default: SQLite >= 3.1.3 (2005).
REQUIRED_SQLITE_VERSION = (3, 1, 3)

# Backfill values for existing rows; match the settings template defaults
# and reproduce the prior behaviour (always landing on the dashboard, and
# every overview showing all people).
START_PAGE_DEFAULT = 'dashboard'
VIEW_SCOPE_DEFAULT = 'all'

# Added by the v1.6.0 upgrade; without them that upgrade has not run yet.
SOURCE_LAYOUT_COLUMNS = ('last_login_at', 'previous_login_at', 'credential_version')

SERVICE_NAME = 'fbk-time'
_SERVICE_STOPPED_STATES = frozenset({'inactive', 'failed'})

LOGIN_ATTEMPT_TYPE_COLUMN = 'identifier_type'
# SQLAlchemy's Enum stores the member name, not its value.
LOGIN_ATTEMPT_TYPE_DEFAULT = 'USERNAME'


class Logger:
    """Minimal colored logger using ANSI escapes.

    Colors only on a TTY, so a run captured to a log file stays free of
    escape sequences.
    """

    def __init__(self, quiet: bool = False):
        self.quiet = quiet
        self._stdout_color = sys.stdout.isatty()
        self._stderr_color = sys.stderr.isatty()

    @staticmethod
    def _paint(use_color: bool, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if use_color else text

    def success(self, msg: str) -> None:
        if not self.quiet:
            mark = self._paint(self._stdout_color, '92', '✓')
            print(f"{mark} {msg}")

    def error(self, msg: str) -> None:
        mark = self._paint(self._stderr_color, '91', '✗')
        print(f"{mark} {msg}", file=sys.stderr)

    def warning(self, msg: str) -> None:
        if not self.quiet:
            mark = self._paint(self._stdout_color, '93', '⚠')
            print(f"{mark} {msg}")

    def info(self, msg: str) -> None:
        if not self.quiet:
            mark = self._paint(self._stdout_color, '94', '→')
            print(f"{mark} {msg}")

    def section(self, title: str) -> None:
        if not self.quiet:
            print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}\n")


def _resolve_db_path(
    app_path: str | None,
    explicit_db: str | None,
    logger: Logger
) -> Path:
    """Return the SQLite database path.

    Exactly one of app_path or explicit_db must be set. The
    mutually-exclusive constraint is enforced by argparse.
    """
    if explicit_db:
        db_path = Path(explicit_db).expanduser().resolve()
        if not db_path.exists():
            logger.error(f'Database file not found: {db_path}')
            sys.exit(1)
        return db_path

    if not app_path:
        logger.error('Either --app-path or --db is required')
        sys.exit(1)

    app_dir = Path(app_path).expanduser().resolve()
    if not app_dir.exists():
        logger.error(f'Application directory not found: {app_dir}')
        sys.exit(1)

    settings_path = app_dir / 'settings.json'
    if not settings_path.exists():
        logger.error(f'settings.json not found in {app_dir}')
        logger.info('Is this the correct FBK-Time installation path?')
        sys.exit(1)

    try:
        with open(settings_path, 'r', encoding='utf-8') as fh:
            settings = json.load(fh)
        rel_db = settings['system']['database']['path']
    except Exception as exc:
        logger.error(f'Failed to read settings.json: {exc}')
        sys.exit(1)

    db_path = (app_dir / rel_db).resolve()
    if not db_path.exists():
        logger.error(f'Database file not found: {db_path}')
        logger.info(f'Resolved from {settings_path}')
        sys.exit(1)

    logger.info(f'Application: {app_dir}')
    return db_path


def _startup_settings_problems(settings: dict, app_dir: Path) -> list[str]:
    """Return the settings.json problems that stop v2.0.0 from starting.

    Mirrors the startup checks of the application's config.py, so a
    database is never upgraded for an installation that cannot start.
    """
    problems = []
    system = settings.get('system', {})

    session = system.get('security', {}).get('session', {})
    lifetime_hours = session.get('lifetime_hours')
    idle_minutes = session.get('idle_timeout_minutes')
    warning_seconds = session.get('idle_warning_seconds')
    remember_days = session.get('remember_cookie_days')
    numbers = (lifetime_hours, idle_minutes, warning_seconds, remember_days)
    if not all(isinstance(value, int) and not isinstance(value, bool)
               for value in numbers):
        problems.append(
            'system.security.session needs the integers lifetime_hours, '
            'idle_timeout_minutes, idle_warning_seconds and '
            'remember_cookie_days'
        )
    else:
        if idle_minutes <= 0:
            problems.append('idle_timeout_minutes must be greater than 0')
        if lifetime_hours * 60 <= idle_minutes:
            problems.append(
                'lifetime_hours must be longer than idle_timeout_minutes'
            )
        if warning_seconds < 20:
            problems.append('idle_warning_seconds must be at least 20')
        if idle_minutes > 0 and warning_seconds >= idle_minutes * 60:
            problems.append(
                'idle_warning_seconds must be shorter than '
                'idle_timeout_minutes'
            )
        if remember_days <= 0:
            problems.append('remember_cookie_days must be greater than 0')

    backup_dir = system.get('backup', {}).get('directory')
    if not isinstance(backup_dir, str) or not backup_dir:
        problems.append('system.backup.directory is missing')
    elif (app_dir / backup_dir).resolve().is_relative_to(app_dir):
        problems.append(
            f'system.backup.directory must lie outside {app_dir}'
        )

    return problems


def _check_startup_settings(app_path: str, logger: Logger) -> bool:
    """Abort before any backup or migration if settings.json cannot start v2.0.0."""
    app_dir = Path(app_path).expanduser().resolve()
    with open(app_dir / 'settings.json', 'r', encoding='utf-8') as fh:
        settings = json.load(fh)

    problems = _startup_settings_problems(settings, app_dir)
    if not problems:
        logger.success('settings.json meets the v2.0.0 startup checks')
        return True

    for problem in problems:
        logger.error(f'settings.json: {problem}')
    logger.info('Fix settings.json as described in upgrades/v2.0.0/README.md.')
    logger.info('Nothing was changed, no backup was written.')
    return False


def _resolve_backup_dir(
    backup_dir: str | None,
    db_path: Path,
    logger: Logger
) -> Path:
    """Return the directory where the backup file will be written.

    Defaults to the directory containing the database file; a given
    --backup-dir must be an existing directory.
    """
    if not backup_dir:
        return db_path.parent

    target = Path(backup_dir).expanduser().resolve()
    if not target.exists():
        logger.error(f'Backup directory does not exist: {target}')
        sys.exit(1)
    if not target.is_dir():
        logger.error(f'Backup path is not a directory: {target}')
        sys.exit(1)
    return target


def _resolve_sqlite_binary(
    args: argparse.Namespace,
    logger: Logger
) -> Path | None:
    """Resolve the SQLite binary via sqlite_runner.

    Returns None when the system SQLite is sufficient, or a Path to
    the bundled/user-provided binary otherwise.
    """
    return sqlite_runner.resolve_binary(
        required=REQUIRED_SQLITE_VERSION,
        user_override=getattr(args, 'sqlite_binary', None),
        force=args.force,
        logger=logger,
    )


def _check_integrity_standalone(
    db_path: Path,
    binary: Path | None,
    logger: Logger
) -> bool:
    """Run a PRAGMA integrity_check on the live database before upgrading.

    A corrupt database would otherwise be copied into the backup and fed
    into the upgrade transaction.
    """
    conn = sqlite_runner.connect(db_path, binary=binary)
    try:
        row = conn.execute('PRAGMA integrity_check').fetchone()
        if not row or row[0] != 'ok':
            logger.error(f'Live database failed integrity check: {row}')
            return False
        logger.success('Live database passed integrity check')
        return True
    except sqlite3.Error as exc:
        logger.error(f'Integrity check raised an error: {exc}')
        return False
    finally:
        conn.close()


def _get_column_names(conn: Any, table: str = 'users') -> set[str]:
    """Return the column names of a table."""
    rows = conn.execute(f'PRAGMA table_info({table})').fetchall()
    return {row[1] for row in rows}


def _missing_source_columns(
    db_path: Path,
    binary: Path | None
) -> list[str]:
    """Return the v1.6.0 users columns the database lacks."""
    conn = sqlite_runner.connect(db_path, binary=binary)
    try:
        columns = _get_column_names(conn)
    finally:
        conn.close()
    return [c for c in SOURCE_LAYOUT_COLUMNS if c not in columns]


def _is_schema_on_target(
    db_path: Path,
    binary: Path | None
) -> bool:
    """Return True if the schema is already on the v2.0.0 layout."""
    conn = sqlite_runner.connect(db_path, binary=binary)
    try:
        columns = _get_column_names(conn)
        attempt_columns = _get_column_names(conn, 'login_attempts')
    finally:
        conn.close()
    return (
        'start_page' in columns
        and 'view_scope' in columns
        and LOGIN_ATTEMPT_TYPE_COLUMN in attempt_columns
    )


def _secure_file_permissions(path: Path, logger: Logger) -> None:
    """Restrict a file to owner read/write (0o600) on POSIX.

    Backups hold password hashes. A failure only warns, since the data is
    already on disk.
    """
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        logger.warning(
            f'Could not restrict permissions on {path} (0o600): {exc}'
        )


def cmd_verify(
    db_path: Path,
    binary: Path | None,
    logger: Logger
) -> bool:
    """Check whether the schema matches the v2.0.0 layout."""
    logger.section(f'v{TARGET_VERSION} Schema Verification')
    conn = sqlite_runner.connect(db_path, binary=binary)
    try:
        columns = _get_column_names(conn)
        missing = [c for c in ('start_page', 'view_scope') if c not in columns]
        if LOGIN_ATTEMPT_TYPE_COLUMN not in _get_column_names(conn, 'login_attempts'):
            missing.append(f'login_attempts.{LOGIN_ATTEMPT_TYPE_COLUMN}')
        if not missing:
            logger.success('Schema already on v2.0.0 layout')
            logger.section(f'v{TARGET_VERSION} Verification Passed')
            return True

        logger.warning(f'Missing column(s): {", ".join(missing)}')
        logger.section(f'v{TARGET_VERSION} Verification Failed')
        return False
    finally:
        conn.close()


def _discard_unused_backup(backup_path: Path, logger: Logger) -> None:
    """Remove a backup taken for an attempt that never opened its transaction.

    The database is untouched then; keeping the duplicate would pile up
    copies of password hashes on every failed attempt.
    """
    try:
        backup_path.unlink()
        logger.info('Backup from this attempt discarded (database unchanged).')
    except OSError as exc:
        logger.warning(f'Could not remove the unused backup {backup_path}: {exc}')


def _check_service_stopped(logger: Logger) -> bool:
    """Verify the application service is not running.

    The decisive check, since a running service retakes the write lock
    within milliseconds. Without systemd only the later lock probe remains.
    """
    try:
        result = subprocess.run(
            ['systemctl', 'is-active', SERVICE_NAME],
            capture_output=True, text=True, timeout=10
        )
    except OSError as exc:
        logger.warning(f'systemctl could not be run: {exc}')
        logger.info(f'Make sure {SERVICE_NAME} is stopped before continuing.')
        return True
    except subprocess.TimeoutExpired:
        logger.error('systemctl timed out while querying the service state')
        return False

    state = result.stdout.strip()
    if state in _SERVICE_STOPPED_STATES:
        logger.success(f'Service {SERVICE_NAME} is not running ({state})')
        return True

    # 'activating', 'deactivating' and 'reloading' all mean workers may still
    # hold the database. An unknown unit proves nothing either — the
    # application may well be running outside systemd.
    if state in ('active', 'reloading', 'activating', 'deactivating'):
        logger.error(f'Service {SERVICE_NAME} is not stopped (state: {state})')
        logger.info(f'Stop it first: systemctl stop {SERVICE_NAME}')
        logger.info('Nothing was read or written.')
        return False

    logger.warning(f'Service state could not be determined ({state or "no output"})')
    if result.stderr.strip():
        logger.info(f'systemctl: {result.stderr.strip()}')
    logger.info(f'Make sure {SERVICE_NAME} is stopped before continuing.')
    return True


def _database_is_busy(db_path: Path, binary: Path | None, logger: Logger) -> bool:
    """Report whether another process holds a write lock on the database.

    Runs before the backup so a running application leaves no orphaned
    backup. BEGIN IMMEDIATE is the only reliable probe; ``PRAGMA
    locking_mode`` always succeeds.
    """
    try:
        conn = sqlite_runner.connect(db_path, binary=binary)
    except Exception as exc:
        logger.error(f'Could not open the database: {exc}')
        return True

    if isinstance(conn, sqlite3.Connection):
        conn.isolation_level = None
    try:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('ROLLBACK')
        return False
    except sqlite3.DatabaseError as exc:
        logger.error(f'Database is in use: {exc}')
        logger.info(f'Stop the application first: systemctl stop {SERVICE_NAME}')
        logger.info('Nothing was changed, no backup was written.')
        return True
    finally:
        conn.close()


def _create_backup(
    db_path: Path,
    backup_dir: Path,
    binary: Path | None,
    logger: Logger
) -> Path | None:
    """Create a transaction-consistent backup including WAL data."""
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    backup_name = f'{db_path.stem}.backup-v{TARGET_VERSION}-{timestamp}{db_path.suffix}'
    backup_path = backup_dir / backup_name

    try:
        sqlite_runner.create_backup(db_path, backup_path, binary=binary)
    except Exception as exc:
        logger.error(f'Backup failed: {exc}')
        if backup_path.exists():
            try:
                backup_path.unlink()
            except OSError:
                pass
        return None

    _secure_file_permissions(backup_path, logger)
    logger.success(f'Backup created: {backup_path}')
    return backup_path


def _apply_schema_changes(conn: Any, columns: set[str], logger: Logger) -> None:
    """Add the start_page and view_scope columns to the users table."""
    if 'start_page' not in columns:
        conn.execute(
            'ALTER TABLE users '
            f"ADD COLUMN start_page VARCHAR(20) NOT NULL DEFAULT '{START_PAGE_DEFAULT}'"
        )
        logger.success('Added column start_page')

    if 'view_scope' not in columns:
        conn.execute(
            'ALTER TABLE users '
            f"ADD COLUMN view_scope VARCHAR(20) NOT NULL DEFAULT '{VIEW_SCOPE_DEFAULT}'"
        )
        logger.success('Added column view_scope')


def _apply_login_attempt_namespace(conn: Any, logger: Logger) -> None:
    """Separate username and IP counters in the login_attempts table.

    The shared ``ip:`` prefix could be forged via the username to lock out an
    address. Prefixed rows are dropped, not rewritten: they are short-lived,
    and a forged one would survive as a genuine address counter.
    """
    if LOGIN_ATTEMPT_TYPE_COLUMN in _get_column_names(conn, 'login_attempts'):
        return

    conn.execute(
        'ALTER TABLE login_attempts '
        f'ADD COLUMN {LOGIN_ATTEMPT_TYPE_COLUMN} VARCHAR(10) '
        f"NOT NULL DEFAULT '{LOGIN_ATTEMPT_TYPE_DEFAULT}'"
    )
    logger.success(f'Added column {LOGIN_ATTEMPT_TYPE_COLUMN}')

    dropped = conn.execute(
        "DELETE FROM login_attempts WHERE identifier LIKE 'ip:%'"
    ).rowcount
    logger.success(f'Discarded {dropped} legacy address counter(s)')

    # The old index was UNIQUE over identifier alone and must go before the
    # new pair constraint; otherwise a username equal to a counted address
    # would stay rejected.
    conn.execute('DROP INDEX IF EXISTS ix_login_attempts_identifier')
    conn.execute(
        'CREATE INDEX ix_login_attempts_identifier '
        'ON login_attempts (identifier)'
    )
    conn.execute(
        'CREATE UNIQUE INDEX uq_login_attempts_identifier_type '
        f'ON login_attempts (identifier, {LOGIN_ATTEMPT_TYPE_COLUMN})'
    )
    _assert_identifier_not_unique_alone(conn)
    logger.success('Rebuilt login_attempts indexes')


def _repair_orphaned_category_overrides(conn: Any, logger: Logger) -> None:
    """Clear category overrides whose target category no longer exists.

    Before v2.0.0, deleting a category nulled ``modified_category_id`` via
    the foreign key but left ``modified_category_overridden`` at 1.
    """
    repaired = conn.execute(
        'UPDATE recurrence_exceptions SET modified_category_overridden = 0 '
        'WHERE modified_category_overridden = 1 '
        'AND modified_category_id IS NULL'
    ).rowcount
    if repaired:
        logger.success(f'Repaired {repaired} orphaned category override(s)')


def _rotate_credential_versions(conn: Any, logger: Logger) -> None:
    """Give every user a fresh random credential_version.

    A random version keeps a reused user id from reviving a deleted
    account's session. Ends every session; safe to run again.
    """
    user_ids = [int(row[0]) for row in conn.execute('SELECT id FROM users').fetchall()]
    for user_id in user_ids:
        # Same range as the application: 62 random bits plus one.
        conn.execute(
            'UPDATE users SET credential_version = ? WHERE id = ?',
            (secrets.randbits(62) + 1, user_id)
        )
    logger.success(f'Rotated credential version of {len(user_ids)} user(s)')


def _assert_identifier_not_unique_alone(conn: Any) -> None:
    """Fail if a unique index over ``identifier`` alone survived the rebuild.

    ``DROP INDEX IF EXISTS`` is silent when the name differs from what
    SQLAlchemy generated. The stale constraint would then reject every
    username matching a counted address — a lasting defect nobody notices.
    """
    for _, name, unique, *_ in conn.execute(
        'PRAGMA index_list(login_attempts)'
    ).fetchall():
        if not unique:
            continue
        columns = [row[2] for row in conn.execute(
            f'PRAGMA index_info({name})'
        ).fetchall()]
        if columns == ['identifier']:
            raise RuntimeError(
                f'Unique index {name} still covers identifier alone; '
                f'the pre-v2.0.0 index was not removed'
            )


def _verify_post_upgrade(
    conn: Any,
    count_before: int,
    logger: Logger
) -> None:
    """Confirm the target columns exist and the user row count is stable."""
    columns = _get_column_names(conn)
    missing = [c for c in ('start_page', 'view_scope') if c not in columns]
    if missing:
        raise RuntimeError(
            f'Column(s) not found after ADD COLUMN: {", ".join(missing)}'
        )

    if LOGIN_ATTEMPT_TYPE_COLUMN not in _get_column_names(conn, 'login_attempts'):
        raise RuntimeError(
            f'Column not found after ADD COLUMN: {LOGIN_ATTEMPT_TYPE_COLUMN}'
        )

    # Any surviving prefix means the cleanup did not run; the row would be
    # counted as a username from here on.
    stale = int(conn.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE identifier LIKE 'ip:%'"
    ).fetchone()[0])
    if stale:
        raise RuntimeError(
            f'{stale} login attempt row(s) still carry the legacy ip: prefix'
        )

    count_after = int(conn.execute('SELECT COUNT(*) FROM users').fetchone()[0])
    if count_before != count_after:
        raise RuntimeError(
            f'Row count mismatch: before={count_before} after={count_after}'
        )

    logger.success(f'Post-upgrade verification passed ({count_after} users)')


def cmd_restore(
    db_path: Path,
    backup_file: Path,
    force: bool,
    binary: Path | None,
    logger: Logger
) -> bool:
    """Restore the live database from a user-specified backup file.

    The current state is first saved as '<db>.pre-restore-<UTC>.db' next
    to the database.
    """
    logger.section(f'v{TARGET_VERSION} Restore')
    logger.info(f'Database:    {db_path}')
    logger.info(f'Backup file: {backup_file}')

    # A restore overwrites the live database wholesale — a running service
    # would keep serving from, and writing to, the file being replaced.
    if not _check_service_stopped(logger):
        return False

    if _database_is_busy(db_path, binary, logger):
        return False

    if not backup_file.exists():
        logger.error(f'Backup file not found: {backup_file}')
        return False

    if not backup_file.is_file():
        logger.error(f'Backup path is not a regular file: {backup_file}')
        return False

    try:
        probe = sqlite_runner.connect(backup_file, binary=binary)
        try:
            result = probe.execute('PRAGMA integrity_check').fetchone()
            if not result or result[0] != 'ok':
                logger.error(f'Backup file failed integrity check: {result}')
                return False
        finally:
            probe.close()
        logger.success('Backup file passed integrity check')
    except sqlite3.Error as exc:
        logger.error(f'Backup file is not a valid SQLite database: {exc}')
        return False

    if not force:
        print(
            f'\nThis will OVERWRITE the live database at\n'
            f'  {db_path}\n'
            f'with the contents of\n'
            f'  {backup_file}\n'
        )
        confirmation = input('Type YES to continue: ').strip()
        if confirmation != 'YES':
            logger.warning('Restore cancelled by user')
            return False

    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    safety_name = f'{db_path.stem}.pre-restore-{timestamp}{db_path.suffix}'
    safety_path = db_path.parent / safety_name

    try:
        sqlite_runner.create_backup(db_path, safety_path, binary=binary)
    except Exception as exc:
        logger.error(f'Failed to create safety snapshot: {exc}')
        return False

    _secure_file_permissions(safety_path, logger)
    logger.success(f'Pre-restore safety snapshot: {safety_path}')

    try:
        sqlite_runner.create_backup(backup_file, db_path, binary=binary)
        conn = sqlite_runner.connect(db_path, binary=binary)
        try:
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        except sqlite3.Error as exc:
            logger.warning(f'WAL checkpoint failed (non-fatal): {exc}')
        finally:
            conn.close()
    except Exception as exc:
        logger.error(f'Restore failed: {exc}')
        logger.info(
            f'Live database is likely partially written. '
            f'The safety snapshot at {safety_path} still holds the '
            f'pre-restore state.'
        )
        return False

    logger.success('Database restored from backup')
    logger.info(f'Safety snapshot retained at {safety_path}')
    return True


def cmd_upgrade(
    db_path: Path,
    backup_dir: Path,
    force: bool,
    binary: Path | None,
    logger: Logger
) -> bool:
    """Apply the v2.0.0 schema upgrade with backup and rollback."""
    logger.section(f'v{TARGET_VERSION} Schema Upgrade')
    logger.info(f'Database: {db_path}')
    logger.info(f'Backup directory: {backup_dir}')

    # First of all, before anything is read, asked or written: a running
    # service reacquires the write lock between any two steps, so a lock
    # probe alone is always a stale snapshot.
    if not _check_service_stopped(logger):
        return False

    if not _check_integrity_standalone(db_path, binary, logger):
        return False

    # This script builds on the v1.6.0 layout; applied to an older database
    # it would stamp the new columns onto a schema the application cannot use.
    missing_source = _missing_source_columns(db_path, binary)
    if missing_source:
        logger.error(
            f'Database is not on the v1.6.0 layout '
            f'(missing users column(s): {", ".join(missing_source)})'
        )
        logger.info('Run upgrades/v1.6.0/upgrade.py first, then this script.')
        logger.info('Nothing was changed, no backup was written.')
        return False

    if _is_schema_on_target(db_path, binary):
        logger.success('Schema is already on the v2.0.0 layout')
        return True

    if not force:
        confirmation = input('Continue with upgrade? [y/N] ').strip().lower()
        if confirmation != 'y':
            logger.warning('Upgrade cancelled by user')
            return False

    if _database_is_busy(db_path, binary, logger):
        return False

    backup_path = _create_backup(db_path, backup_dir, binary, logger)
    if backup_path is None:
        return False

    conn = sqlite_runner.connect(db_path, binary=binary)
    # CLIConnection uses manual transactions by default (no autocommit);
    # native sqlite3 needs isolation_level=None for explicit BEGIN/COMMIT
    if isinstance(conn, sqlite3.Connection):
        conn.isolation_level = None
    try:
        # Keeps the lock taken by BEGIN IMMEDIATE for the whole upgrade, so no
        # writer can interleave between column snapshot and ALTER.
        conn.execute('PRAGMA locking_mode = EXCLUSIVE')

        try:
            conn.execute('BEGIN IMMEDIATE')
        except sqlite3.OperationalError as exc:
            logger.error(f'Database is locked: {exc}')
            logger.info(f'Stop the application first: systemctl stop {SERVICE_NAME}')
            _discard_unused_backup(backup_path, logger)
            return False

        try:
            columns = _get_column_names(conn)

            count_before = int(conn.execute(
                'SELECT COUNT(*) FROM users'
            ).fetchone()[0])
            logger.info(f'Existing users: {count_before}')

            _apply_schema_changes(conn, columns, logger)
            _apply_login_attempt_namespace(conn, logger)
            _repair_orphaned_category_overrides(conn, logger)
            _rotate_credential_versions(conn, logger)
            _verify_post_upgrade(conn, count_before, logger)
            conn.execute('COMMIT')
            logger.success('Upgrade committed')
        except Exception as exc:
            try:
                conn.execute('ROLLBACK')
            except Exception as rollback_exc:
                logger.warning(f'Rollback also failed: {rollback_exc}')
            logger.error(f'Upgrade failed, rolled back: {exc}')
            logger.info(
                f'Transaction rolled back - live database is unchanged. '
                f'Backup retained at {backup_path}'
            )
            return False

        # Leaves the .db self-contained for file-level inspection.
        try:
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            logger.success('WAL checkpointed (database is self-contained)')
        except sqlite3.Error as exc:
            logger.warning(f'WAL checkpoint failed (non-fatal): {exc}')

        logger.section(f'v{TARGET_VERSION} Upgrade Successful')
        logger.success(
            f'Database upgraded to v{TARGET_VERSION}. '
            f'Backup: {backup_path}'
        )
        return True
    finally:
        conn.close()


def main() -> None:
    description = (
        f'FBK-Time schema upgrade runner.\n'
        f'\n'
        f'  Target version:     v{TARGET_VERSION}\n'
        f'  Supported sources:  v{SUPPORTED_FROM_VERSIONS}\n'
        f'  Required SQLite:    '
        f'>= {".".join(map(str, REQUIRED_SQLITE_VERSION))}\n'
        f'\n'
        f'First checks the settings.json values v2.0.0 enforces at startup\n'
        f'(with --app-path) and aborts without any change if one is invalid.\n'
        f'Adds the start_page and view_scope columns to the users table,\n'
        f'separates username and address counters in login_attempts\n'
        f'(identifier_type, legacy ip: rows discarded), clears orphaned\n'
        f'category overrides and assigns every user a fresh random\n'
        f'credential_version, which ends all sessions and remember-me\n'
        f'cookies. Requires the v1.6.0 layout. Runs in a single transaction\n'
        f'and creates a timestamped backup before touching the database.\n'
        f'Rolls back on any failure.'
    )

    epilog = (
        'Examples:\n'
        '\n'
        '  # Verify schema state of an installation\n'
        '  python upgrade.py verify --app-path /var/www/fbk-time\n'
        '\n'
        '  # Apply upgrade with interactive confirmation\n'
        '  python upgrade.py upgrade --app-path /var/www/fbk-time\n'
        '\n'
        '  # Apply upgrade non-interactively (CI, scripted)\n'
        '  python upgrade.py upgrade --app-path /var/www/fbk-time --force\n'
        '\n'
        '  # Store backup on a separate volume\n'
        '  python upgrade.py upgrade --app-path /var/www/fbk-time \\\n'
        '                            --backup-dir /mnt/backups/fbk-time\n'
        '\n'
        '  # Restore from an explicit backup file (no auto-find)\n'
        '  python upgrade.py restore --app-path /var/www/fbk-time \\\n'
        '                            --backup-file /var/www/fbk-time/data/'
        'fbk-time.backup-v2.0.0-20260721_120000.db\n'
        '\n'
        '  # Work on an exotic database file (override, no settings.json)\n'
        '  python upgrade.py upgrade --db /tmp/restored.db\n'
        '\n'
        'The script is location-agnostic. It requires the sqlite_runner\n'
        'module from the parent upgrades/ directory. The user_default_start_page\n'
        'and user_default_view_scope settings are seeded automatically by the\n'
        'application on startup.'
    )

    # A parent parser lets shared options follow the subcommand, as documented.
    common = argparse.ArgumentParser(add_help=False)
    db_group = common.add_mutually_exclusive_group(required=True)
    db_group.add_argument(
        '--app-path',
        dest='app_path',
        metavar='DIR',
        help='Path to the FBK-Time installation directory '
             '(e.g. /var/www/fbk-time). The database path is resolved '
             'from <DIR>/settings.json.'
    )
    db_group.add_argument(
        '--db',
        metavar='FILE',
        help='Explicit SQLite database file. Skips settings.json '
             'lookup. Use for restored backups or non-standard layouts.'
    )
    common.add_argument(
        '--sqlite-binary',
        dest='sqlite_binary',
        metavar='FILE',
        help='Path to a static sqlite3 binary. When omitted, the '
             'system SQLite is checked and a bundled binary is offered '
             'if the system version is too old.'
    )
    common.add_argument(
        '--force', action='store_true',
        help='Skip interactive confirmation prompt.'
    )
    common.add_argument(
        '--quiet', '-q', action='store_true',
        help='Suppress informational output (errors still shown).'
    )

    parser = argparse.ArgumentParser(
        prog=f'upgrade-v{TARGET_VERSION}',
        description=description,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    subparsers.add_parser(
        'verify',
        parents=[common],
        help=f'Check whether the schema is on v{TARGET_VERSION}',
        description=f'Check whether the schema is on v{TARGET_VERSION}. '
                    f'Read-only, never modifies the database.'
    )

    upgrade_parser = subparsers.add_parser(
        'upgrade',
        parents=[common],
        help=f'Apply the v{TARGET_VERSION} schema upgrade',
        description=f'Apply the v{TARGET_VERSION} schema upgrade. '
                    f'Creates a timestamped backup, runs the migration '
                    f'in a single transaction, verifies data integrity, '
                    f'rolls back on any failure.'
    )
    upgrade_parser.add_argument(
        '--backup-dir',
        dest='backup_dir',
        metavar='DIR',
        help='Directory where the pre-upgrade backup is written. '
             'Defaults to the directory containing the database file.'
    )

    restore_parser = subparsers.add_parser(
        'restore',
        parents=[common],
        help='Restore the database from a --backup-file',
        description='Restore the live database from a user-specified '
                    'backup file. The backup file must be passed '
                    'explicitly via --backup-file; no auto-detection.'
    )
    restore_parser.add_argument(
        '--backup-file',
        dest='backup_file',
        metavar='FILE',
        required=True,
        help='Backup file to restore from. Must be an existing, valid '
             'SQLite database file.'
    )

    args = parser.parse_args()
    logger = Logger(quiet=args.quiet)

    try:
        db_path = _resolve_db_path(args.app_path, args.db, logger)
        binary = _resolve_sqlite_binary(args, logger)

        if args.command == 'upgrade':
            backup_dir = _resolve_backup_dir(args.backup_dir, db_path, logger)
            ok = (
                (not args.app_path or _check_startup_settings(args.app_path, logger))
                and cmd_upgrade(db_path, backup_dir, args.force, binary, logger)
            )
        elif args.command == 'restore':
            backup_file = Path(args.backup_file).expanduser().resolve()
            ok = cmd_restore(
                db_path, backup_file, args.force, binary, logger
            )
        else:
            ok = cmd_verify(db_path, binary, logger)
        sys.exit(0 if ok else 1)
    except KeyboardInterrupt:
        print('\nCancelled')
        sys.exit(1)


if __name__ == '__main__':
    main()
