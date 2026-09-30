#!/usr/bin/env python3
"""Upgrade runner for release v1.6.0.

Extends the users table:
    - Renames last_login   → last_login_at
    - Adds   previous_login_at (DATETIME, nullable) to track the login
      before the current session, used as the displayed "last login"
      security indicator.
    - Adds   credential_version (INTEGER, default 0) so a password change
      invalidates existing sessions and remember-me cookies.

Existing rows receive NULL for previous_login_at, which is the correct
initial state: no prior session has been recorded yet. credential_version
starts at 0 for every existing user.

It also ensures the installation's settings.json defines the settings
this release requires (idle_timeout_minutes, idle_warning_seconds and
backup.directory), adding any that are absent with the shipped defaults.
This step is idempotent and creates its own timestamped settings.json
backup before writing.

Runs the schema change in a single transaction. Creates a timestamped
backup of the database file before touching it. Rolls back on any
failure.

Dependencies: Python stdlib + ``sqlite_runner`` from the parent
``upgrades/`` directory. No third-party packages required.

When the host system ships a SQLite version older than 3.25 (e.g.
an unusually old distribution), the script automatically falls back
to a bundled static sqlite3 binary via the ``sqlite_runner`` module.
The operator can override the binary path with ``--sqlite-binary``.

The script is location-agnostic: pass --app-path pointing at the
FBK-Time installation directory (e.g. /var/www/fbk-time) and it
will resolve the database location from that installation's
settings.json. The script can live anywhere on disk.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sqlite_runner


TARGET_VERSION = '1.6.0'
SERVICE_NAME = 'fbk-time'
_SERVICE_STOPPED_STATES = frozenset({'inactive', 'failed'})
SUPPORTED_FROM_VERSIONS = '1.5.x'
# ALTER TABLE RENAME COLUMN requires SQLite >= 3.25.0 (2018-09-15).
REQUIRED_SQLITE_VERSION = (3, 25, 0)

# Defaults applied when an installation's settings.json predates settings
# this release introduced. Match the values shipped in the source tree.
IDLE_TIMEOUT_DEFAULT_MINUTES = 30
IDLE_WARNING_DEFAULT_SECONDS = 60
# /tmp is volatile across reboots and world-writable; production must point
# this at a persistent path outside the install directory (see settings.json).
BACKUP_DIR_DEFAULT = '/tmp/fbk-time-backups'

# Settings under "system" this release requires, as (key path, default).
# Absent keys are inserted with the default; existing values are kept.
REQUIRED_SYSTEM_SETTINGS = (
    (('security', 'session', 'idle_timeout_minutes'), IDLE_TIMEOUT_DEFAULT_MINUTES),
    (('security', 'session', 'idle_warning_seconds'), IDLE_WARNING_DEFAULT_SECONDS),
    (('backup', 'directory'), BACKUP_DIR_DEFAULT),
)


class Logger:
    """Minimal colored logger using ANSI escapes.

    Color codes are emitted only when the target stream is a TTY, so a
    redirected upgrade run captured to a log file stays free of escape
    sequences.
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
            mark = self._paint(self._stdout_color, '92', '\u2713')
            print(f"{mark} {msg}")

    def error(self, msg: str) -> None:
        mark = self._paint(self._stderr_color, '91', '\u2717')
        print(f"{mark} {msg}", file=sys.stderr)

    def warning(self, msg: str) -> None:
        if not self.quiet:
            mark = self._paint(self._stdout_color, '93', '\u26A0')
            print(f"{mark} {msg}")

    def info(self, msg: str) -> None:
        if not self.quiet:
            mark = self._paint(self._stdout_color, '94', '\u2192')
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


def _resolve_backup_dir(
    backup_dir: str | None,
    db_path: Path,
    logger: Logger
) -> Path:
    """Return the directory where the backup file will be written.

    Defaults to the directory containing the database file. If
    --backup-dir is given, it must exist and be writable.
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

    A corrupt live database would otherwise silently be copied into
    the backup and fed into the upgrade transaction. Running the
    check before any write operation gives the operator a chance to
    abort and restore from an earlier backup.
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


def _get_column_names(conn: Any) -> set[str]:
    """Return the column names of the users table."""
    rows = conn.execute('PRAGMA table_info(users)').fetchall()
    return {row[1] for row in rows}


def _is_schema_on_target(
    db_path: Path,
    binary: Path | None
) -> bool:
    """Return True if the schema is already on the v1.6.0 layout."""
    conn = sqlite_runner.connect(db_path, binary=binary)
    try:
        columns = _get_column_names(conn)
    finally:
        conn.close()
    return (
        'last_login_at' in columns
        and 'previous_login_at' in columns
        and 'credential_version' in columns
        and 'last_login' not in columns
    )


def _secure_file_permissions(path: Path, logger: Logger) -> None:
    """Restrict a file to owner read/write (0o600) on POSIX.

    Backups contain the full database, including Argon2id password
    hashes and session state. Windows ignores POSIX mode bits for
    files; the best-effort chmod is harmless there. Any OSError is
    logged as a warning and does not fail the upgrade or restore
    because the data inside the backup is already on disk.
    """
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        logger.warning(
            f'Could not restrict permissions on {path} (0o600): {exc}'
        )


def _missing_system_settings(settings_path: Path) -> list[str]:
    """Return the dotted names of required system settings absent from the file."""
    with open(settings_path, 'r', encoding='utf-8') as fh:
        data = json.load(fh)
    system = data.get('system', {})
    missing = []
    for path, _default in REQUIRED_SYSTEM_SETTINGS:
        node = system
        for key in path[:-1]:
            node = node.get(key, {}) if isinstance(node, dict) else {}
        if not isinstance(node, dict) or path[-1] not in node:
            missing.append('.'.join(path))
    return missing


def _settings_complete(settings_path: Path) -> bool:
    """Return True if settings.json defines every required system setting."""
    return not _missing_system_settings(settings_path)


def _settings_write_problem(settings_path: Path, backup_dir: Path) -> str | None:
    """Return why settings.json cannot be updated, or None if it can.

    Checked before the schema commit, so a known write problem does not
    leave the database upgraded while settings.json stays behind.
    """
    for directory in (settings_path.parent, backup_dir):
        if not os.access(directory, os.W_OK | os.X_OK):
            return f'No write access to {directory}'

    with open(settings_path, 'r', encoding='utf-8') as fh:
        node = json.load(fh)
    for path, _default in REQUIRED_SYSTEM_SETTINGS:
        parents = ('system',) + path[:-1]
        current = node
        for depth, key in enumerate(parents):
            current = current.get(key, {})
            if not isinstance(current, dict):
                return f'{".".join(parents[:depth + 1])} is not an object'
    return None


def _ensure_system_settings(
    settings_path: Path,
    backup_dir: Path,
    logger: Logger
) -> bool:
    """Add any missing required settings under "system" to settings.json.

    Inserts the keys in REQUIRED_SYSTEM_SETTINGS (idle timeout, idle warning
    lead time, backup directory) when absent. Idempotent: existing values are
    left untouched. Creates a timestamped settings.json backup before editing
    and writes the file atomically, preserving the original file mode. Returns
    True if any key was inserted.
    """
    with open(settings_path, 'r', encoding='utf-8') as fh:
        data = json.load(fh)

    system = data.setdefault('system', {})
    inserted = {}
    for path, default in REQUIRED_SYSTEM_SETTINGS:
        node = system
        for key in path[:-1]:
            node = node.setdefault(key, {})
        if path[-1] not in node:
            node[path[-1]] = default
            inserted['.'.join(path)] = default

    if not inserted:
        logger.success('settings.json already defines all required settings')
        return False

    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    backup_path = backup_dir / f'settings.json.backup-v{TARGET_VERSION}-{timestamp}'
    shutil.copy2(settings_path, backup_path)
    _secure_file_permissions(backup_path, logger)
    logger.success(f'settings.json backup created: {backup_path}')

    original_mode = settings_path.stat().st_mode & 0o777
    tmp_path = settings_path.with_name(settings_path.name + '.tmp')
    try:
        with open(tmp_path, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, indent=2)
            fh.write('\n')
        os.chmod(tmp_path, original_mode)
        os.replace(tmp_path, settings_path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise

    for name, default in inserted.items():
        logger.success(f'Added {name} = {default} to settings.json')
    if 'backup.directory' in inserted:
        logger.warning(
            'system.backup.directory was set to the volatile /tmp default. '
            'Point it at a persistent path outside the install directory '
            'before relying on backups.'
        )
    return True


def cmd_verify(
    db_path: Path,
    settings_path: Path | None,
    binary: Path | None,
    logger: Logger
) -> bool:
    """Check whether schema and settings match the v1.6.0 layout."""
    logger.section(f'v{TARGET_VERSION} Verification')
    ok = True

    conn = sqlite_runner.connect(db_path, binary=binary)
    try:
        columns = _get_column_names(conn)
        missing_new = {
            'last_login_at', 'previous_login_at', 'credential_version'
        } - columns
        remaining_legacy = {'last_login'} & columns

        if not missing_new and not remaining_legacy:
            logger.success('Schema already on v1.6.0 layout')
        else:
            ok = False
            if missing_new:
                logger.warning(
                    f'Missing columns: {", ".join(sorted(missing_new))}'
                )
            if remaining_legacy:
                logger.warning(
                    f'Legacy columns still present: {", ".join(sorted(remaining_legacy))}'
                )
    finally:
        conn.close()

    if settings_path is None:
        logger.warning('settings.json not checked (no --app-path given)')
    elif _settings_complete(settings_path):
        logger.success('settings.json defines all required settings')
    else:
        ok = False
        missing = ', '.join(_missing_system_settings(settings_path))
        logger.warning(f'settings.json is missing required settings: {missing}')

    logger.section(
        f'v{TARGET_VERSION} Verification {"Passed" if ok else "Failed"}'
    )
    return ok


def _create_backup(
    db_path: Path,
    backup_dir: Path,
    binary: Path | None,
    logger: Logger
) -> Path | None:
    """Create a transaction-consistent backup via sqlite_runner.

    Uses the native online backup API or the CLI .backup command,
    depending on whether a bundled binary is in use. Both methods
    produce a self-contained, consistent snapshot including WAL data.
    """
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
    """Rename last_login → last_login_at, add previous_login_at/credential_version."""
    if 'last_login' in columns and 'last_login_at' not in columns:
        conn.execute(
            'ALTER TABLE users RENAME COLUMN last_login TO last_login_at'
        )
        logger.success('Renamed column last_login → last_login_at')

    if 'previous_login_at' not in columns:
        conn.execute(
            'ALTER TABLE users ADD COLUMN previous_login_at DATETIME'
        )
        logger.success('Added column previous_login_at')

    if 'credential_version' not in columns:
        conn.execute(
            'ALTER TABLE users ADD COLUMN credential_version INTEGER NOT NULL DEFAULT 0'
        )
        logger.success('Added column credential_version')


def _verify_post_upgrade(conn: Any, logger: Logger) -> None:
    """Confirm target columns exist and user row count is stable."""
    columns = _get_column_names(conn)

    if 'last_login_at' not in columns:
        raise RuntimeError(
            'Column last_login_at not found after RENAME COLUMN'
        )
    if 'previous_login_at' not in columns:
        raise RuntimeError(
            'Column previous_login_at not found after ADD COLUMN'
        )
    if 'credential_version' not in columns:
        raise RuntimeError(
            'Column credential_version not found after ADD COLUMN'
        )
    if 'last_login' in columns:
        raise RuntimeError(
            'Legacy column last_login still present after RENAME'
        )

    count = int(conn.execute('SELECT COUNT(*) FROM users').fetchone()[0])
    logger.success(f'Post-upgrade verification passed ({count} users)')


def cmd_restore(
    db_path: Path,
    backup_file: Path,
    force: bool,
    binary: Path | None,
    logger: Logger
) -> bool:
    """Restore the live database from a user-specified backup file.

    Before overwriting the live database, the current state is copied
    to a safety snapshot named '<db>.pre-restore-<UTC>.db' in the same
    directory. This is the only implicit behavior: the backup source
    file is never guessed, it must be passed explicitly.
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


def _discard_unused_backup(backup_path: Path, logger: Logger) -> None:
    """Remove a backup taken for an attempt that never opened its transaction.

    The live database is provably untouched in that case, so the backup is a
    byte-identical duplicate. Keeping it would accumulate copies of the full
    database — including password hashes — on every failed attempt.
    """
    try:
        backup_path.unlink()
        logger.info('Backup from this attempt discarded (database unchanged).')
    except OSError as exc:
        logger.warning(f'Could not remove the unused backup {backup_path}: {exc}')


def _check_service_stopped(logger: Logger) -> bool:
    """Verify the application service is not running.

    The decisive check: a running service holds a write lock again within
    milliseconds, so probing the lock cannot stay valid until the upgrade
    transaction opens. Systems without systemd fall through to the lock
    probe further down, which is the best that can be done there.
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

    Checked before the backup is written so a still-running application does
    not leave an orphaned backup behind. A short IMMEDIATE transaction is the
    only reliable probe: ``PRAGMA locking_mode`` merely sets the mode for
    later locks and always succeeds.
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


def _upgrade_db_schema(
    db_path: Path,
    backup_dir: Path,
    binary: Path | None,
    logger: Logger
) -> bool:
    """Run the users-table schema migration in a single transaction."""
    if _database_is_busy(db_path, binary, logger):
        return False

    backup_path = _create_backup(db_path, backup_dir, binary, logger)
    if backup_path is None:
        return False

    conn = sqlite_runner.connect(db_path, binary=binary)
    if isinstance(conn, sqlite3.Connection):
        conn.isolation_level = None
    try:
        # The PRAGMA only sets the mode for subsequent locks and never fails
        # on its own; the write lock is taken by BEGIN IMMEDIATE below.
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
            _apply_schema_changes(conn, columns, logger)
            _verify_post_upgrade(conn, logger)
            conn.execute('COMMIT')
            logger.success('Schema upgrade committed')
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

        try:
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            logger.success('WAL checkpointed (database is self-contained)')
        except sqlite3.Error as exc:
            logger.warning(f'WAL checkpoint failed (non-fatal): {exc}')

        logger.success(f'Database schema upgraded. Backup: {backup_path}')
        return True
    finally:
        conn.close()


def cmd_upgrade(
    db_path: Path,
    settings_path: Path | None,
    backup_dir: Path,
    force: bool,
    binary: Path | None,
    logger: Logger
) -> bool:
    """Apply the v1.6.0 upgrade: users-table schema and settings.json."""
    logger.section(f'v{TARGET_VERSION} Upgrade')
    logger.info(f'Database: {db_path}')
    if settings_path is not None:
        logger.info(f'Settings: {settings_path}')
    logger.info(f'Backup directory: {backup_dir}')

    # First of all, before anything is read, asked or written: a running
    # service reacquires the write lock between any two steps, so a lock
    # probe alone is always a stale snapshot.
    if not _check_service_stopped(logger):
        return False

    if not _check_integrity_standalone(db_path, binary, logger):
        return False

    schema_done = _is_schema_on_target(db_path, binary)
    settings_done = (
        settings_path is None or _settings_complete(settings_path)
    )
    if schema_done and settings_done:
        logger.success('Database and settings already on the v1.6.0 layout')
        return True

    if not settings_done:
        problem = _settings_write_problem(settings_path, backup_dir)
        if problem:
            logger.error(f'settings.json cannot be updated: {problem}')
            logger.info('Nothing was changed, no backup was written.')
            return False

    if not force:
        confirmation = input('Continue with upgrade? [y/N] ').strip().lower()
        if confirmation != 'y':
            logger.warning('Upgrade cancelled by user')
            return False

    if schema_done:
        logger.success('Schema already on v1.6.0 layout, skipping DB step')
    elif not _upgrade_db_schema(db_path, backup_dir, binary, logger):
        return False

    if settings_path is None:
        logger.warning(
            'No --app-path given: settings.json was not located. '
            'Ensure the required settings (idle_timeout_minutes, '
            'idle_warning_seconds, backup.directory) are set manually.'
        )
    else:
        try:
            _ensure_system_settings(settings_path, backup_dir, logger)
        except OSError as exc:
            logger.error(f'Updating settings.json failed: {exc}')
            logger.info(
                'The database schema is already upgraded. Fix the cause and '
                'run the upgrade again; it then only completes settings.json.'
            )
            return False

    logger.section(f'v{TARGET_VERSION} Upgrade Successful')
    logger.success(f'Installation upgraded to v{TARGET_VERSION}.')
    return True


def main() -> None:
    description = (
        f'FBK-Time schema upgrade runner.\n'
        f'\n'
        f'  Target version:     v{TARGET_VERSION}\n'
        f'  Supported sources:  v{SUPPORTED_FROM_VERSIONS}\n'
        f'  Required SQLite:    '
        f'>= {".".join(map(str, REQUIRED_SQLITE_VERSION))}\n'
        f'\n'
        f'Renames last_login → last_login_at and adds previous_login_at\n'
        f'and credential_version to the users table, and ensures\n'
        f'settings.json defines the required settings (idle_timeout_minutes,\n'
        f'idle_warning_seconds, backup.directory).\n'
        f'Runs the schema change in a single transaction and creates\n'
        f'timestamped backups before touching the database or\n'
        f'settings.json. Rolls back on any failure.'
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
        'fbk-time.backup-v1.6.0-20260506_120000.db\n'
        '\n'
        '  # Work on an exotic database file (override, no settings.json)\n'
        '  python upgrade.py upgrade --db /tmp/restored.db\n'
        '\n'
        '  # Use a custom SQLite binary\n'
        '  python upgrade.py upgrade --app-path /var/www/fbk-time \\\n'
        '                            --sqlite-binary /opt/sqlite3/bin/sqlite3\n'
        '\n'
        'The script is location-agnostic. It requires the sqlite_runner\n'
        'module from the parent upgrades/ directory.'
    )

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
        settings_path = (
            Path(args.app_path).expanduser().resolve() / 'settings.json'
            if args.app_path else None
        )
        binary = _resolve_sqlite_binary(args, logger)

        if args.command == 'upgrade':
            backup_dir = _resolve_backup_dir(args.backup_dir, db_path, logger)
            ok = cmd_upgrade(
                db_path, settings_path, backup_dir, args.force, binary, logger
            )
        elif args.command == 'restore':
            backup_file = Path(args.backup_file).expanduser().resolve()
            ok = cmd_restore(
                db_path, backup_file, args.force, binary, logger
            )
        else:
            ok = cmd_verify(db_path, settings_path, binary, logger)
        sys.exit(0 if ok else 1)
    except KeyboardInterrupt:
        print('\nCancelled')
        sys.exit(1)


if __name__ == '__main__':
    main()
