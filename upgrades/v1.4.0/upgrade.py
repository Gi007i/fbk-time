#!/usr/bin/env python3
"""Upgrade runner for FBK-Time release v1.4.0.

Reworks the recurrence_exceptions schema:
    - Adds modified_time_type (enum: 'all_day' | 'morning' | 'afternoon')
    - Adds modified_substitute_overridden (boolean)
    - Adds modified_notes_overridden (boolean)
    - Backfills the new columns from legacy half-day boolean flags
    - Drops the legacy modified_is_half_day_morning / _afternoon columns

Runs in a single transaction. Creates a timestamped backup of the
database file before touching it. Rolls back on any failure.

Dependencies: Python stdlib + ``sqlite_runner`` from the parent
``upgrades/`` directory. No third-party packages required.

When the host system ships a SQLite version older than 3.35 (e.g.
RHEL 9 with 3.34), the script automatically falls back to a bundled
static sqlite3 binary via the ``sqlite_runner`` module. The operator
can override the binary path with ``--sqlite-binary``.

The script is location-agnostic: pass --app-path pointing at the
FBK-Time installation directory (e.g. /var/www/fbk-time) and it
will resolve the database location from that installation's
settings.json. The script can live anywhere on disk.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sqlite_runner


TARGET_VERSION = '1.4.0'
SUPPORTED_FROM_VERSIONS = '1.3.x'
REQUIRED_SQLITE_VERSION = (3, 35, 0)


class Logger:
    """Minimal colored logger using ANSI escapes."""

    def __init__(self, quiet: bool = False):
        self.quiet = quiet

    def success(self, msg: str) -> None:
        if not self.quiet:
            print(f"\033[92m\u2713\033[0m {msg}")

    def error(self, msg: str) -> None:
        print(f"\033[91m\u2717\033[0m {msg}", file=sys.stderr)

    def warning(self, msg: str) -> None:
        if not self.quiet:
            print(f"\033[93m\u26A0\033[0m {msg}")

    def info(self, msg: str) -> None:
        if not self.quiet:
            print(f"\033[94m\u2192\033[0m {msg}")

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


def _is_schema_on_target(
    db_path: Path,
    binary: Path | None
) -> bool:
    """Return True if the schema is already on the v1.4.0 layout."""
    conn = sqlite_runner.connect(db_path, binary=binary)
    try:
        columns = _get_column_names(conn)
    finally:
        conn.close()
    return (
        'modified_time_type' in columns
        and 'modified_substitute_overridden' in columns
        and 'modified_notes_overridden' in columns
        and 'modified_category_overridden' in columns
        and 'modified_is_half_day_morning' not in columns
        and 'modified_is_half_day_afternoon' not in columns
    )


def _get_column_names(conn: Any) -> set[str]:
    """Return the column names of the recurrence_exceptions table.

    The table name is hardcoded rather than parameterized because
    SQLite's ``PRAGMA table_info`` does not accept placeholders for
    identifiers and this helper is only ever called for one table.
    """
    rows = conn.execute('PRAGMA table_info(recurrence_exceptions)').fetchall()
    return {row[1] for row in rows}


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


def cmd_verify(
    db_path: Path,
    binary: Path | None,
    logger: Logger
) -> bool:
    """Check whether the schema matches the v1.4.0 layout."""
    logger.section(f'v{TARGET_VERSION} Schema Verification')
    conn = sqlite_runner.connect(db_path, binary=binary)
    try:
        columns = _get_column_names(conn)
        required_new = {
            'modified_time_type',
            'modified_substitute_overridden',
            'modified_notes_overridden',
            'modified_category_overridden'
        }
        legacy = {
            'modified_is_half_day_morning',
            'modified_is_half_day_afternoon'
        }

        missing_new = required_new - columns
        remaining_legacy = legacy & columns

        if not missing_new and not remaining_legacy:
            logger.success('Schema already on v1.4.0 layout')
            logger.section(f'v{TARGET_VERSION} Verification Passed')
            return True

        if missing_new:
            logger.warning(
                f'Missing columns: {", ".join(sorted(missing_new))}'
            )
        if remaining_legacy:
            logger.warning(
                f'Legacy columns still present: {", ".join(sorted(remaining_legacy))}'
            )
        logger.section(f'v{TARGET_VERSION} Verification Failed')
        return False
    finally:
        conn.close()


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


def _add_new_columns(conn: Any, columns: set[str], logger: Logger) -> None:
    """Add the v1.4.0 override-flag columns to recurrence_exceptions."""
    if 'modified_time_type' not in columns:
        conn.execute(
            'ALTER TABLE recurrence_exceptions '
            'ADD COLUMN modified_time_type VARCHAR(20)'
        )
        logger.success('Added column modified_time_type')

    if 'modified_substitute_overridden' not in columns:
        conn.execute(
            'ALTER TABLE recurrence_exceptions '
            'ADD COLUMN modified_substitute_overridden '
            'BOOLEAN NOT NULL DEFAULT 0'
        )
        logger.success('Added column modified_substitute_overridden')

    if 'modified_notes_overridden' not in columns:
        conn.execute(
            'ALTER TABLE recurrence_exceptions '
            'ADD COLUMN modified_notes_overridden '
            'BOOLEAN NOT NULL DEFAULT 0'
        )
        logger.success('Added column modified_notes_overridden')

    if 'modified_category_overridden' not in columns:
        conn.execute(
            'ALTER TABLE recurrence_exceptions '
            'ADD COLUMN modified_category_overridden '
            'BOOLEAN NOT NULL DEFAULT 0'
        )
        logger.success('Added column modified_category_overridden')


def _backfill(conn: Any, columns: set[str], logger: Logger) -> None:
    """Populate new columns from legacy half-day boolean flags."""
    has_legacy_morning = 'modified_is_half_day_morning' in columns
    has_legacy_afternoon = 'modified_is_half_day_afternoon' in columns

    if has_legacy_morning and has_legacy_afternoon:
        conn.execute(
            """
            UPDATE recurrence_exceptions
            SET modified_time_type = CASE
                WHEN modified_is_half_day_morning = 1 THEN 'morning'
                WHEN modified_is_half_day_afternoon = 1 THEN 'afternoon'
                WHEN (modified_is_half_day_morning = 0
                      AND modified_is_half_day_afternoon = 0
                      AND (
                          modified_category_id IS NOT NULL
                          OR modified_substitute_id IS NOT NULL
                          OR modified_notes IS NOT NULL
                      )) THEN 'all_day'
                ELSE NULL
            END
            WHERE exception_type = 'modified'
              AND modified_time_type IS NULL
            """
        )
        logger.success('Backfilled modified_time_type from legacy flags')

    conn.execute(
        """
        UPDATE recurrence_exceptions
        SET modified_substitute_overridden = 1
        WHERE exception_type = 'modified'
          AND modified_substitute_id IS NOT NULL
          AND modified_substitute_overridden = 0
        """
    )
    logger.success('Backfilled modified_substitute_overridden')

    conn.execute(
        """
        UPDATE recurrence_exceptions
        SET modified_notes_overridden = 1
        WHERE exception_type = 'modified'
          AND modified_notes IS NOT NULL
          AND modified_notes_overridden = 0
        """
    )
    logger.success('Backfilled modified_notes_overridden')

    conn.execute(
        """
        UPDATE recurrence_exceptions
        SET modified_category_overridden = 1
        WHERE exception_type = 'modified'
          AND modified_category_id IS NOT NULL
          AND modified_category_overridden = 0
        """
    )
    logger.success('Backfilled modified_category_overridden')


_NEW_LIMIT_SETTINGS = (
    ('limits_max_future_months', '14', 'limits'),
    ('limits_bulk_delete_items', '200', 'limits'),
)


def _seed_limits_settings(conn: Any, logger: Logger) -> None:
    """Seed new v1.4.0 runtime settings that must exist for the app to run.

    The Flask app reads these keys on every request and raises KeyError
    when they are missing. Inserted with INSERT OR IGNORE so that reruns
    of the upgrade and already-seeded installations stay no-op.
    """
    # Naive UTC timestamp to match the existing DB column convention
    now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(
        sep=' ', timespec='microseconds'
    )
    inserted = 0
    for key, default_value, category in _NEW_LIMIT_SETTINGS:
        cursor = conn.execute(
            'INSERT OR IGNORE INTO settings '
            '(key, value, data_type, category, updated_at) '
            'VALUES (?, ?, ?, ?, ?)',
            (key, default_value, 'INTEGER', category, now_iso)
        )
        if cursor.rowcount > 0:
            inserted += 1

    if inserted:
        logger.success(f'Seeded {inserted} new limits setting(s)')
    else:
        logger.info('Limits settings already present')


def _drop_legacy_columns(conn: Any, columns: set[str], logger: Logger) -> None:
    """Remove the superseded half-day boolean columns."""
    if 'modified_is_half_day_morning' in columns:
        conn.execute(
            'ALTER TABLE recurrence_exceptions '
            'DROP COLUMN modified_is_half_day_morning'
        )
        logger.success('Dropped legacy column modified_is_half_day_morning')
    if 'modified_is_half_day_afternoon' in columns:
        conn.execute(
            'ALTER TABLE recurrence_exceptions '
            'DROP COLUMN modified_is_half_day_afternoon'
        )
        logger.success('Dropped legacy column modified_is_half_day_afternoon')


def _verify_post_upgrade(
    conn: Any,
    count_before: int,
    logger: Logger
) -> None:
    count_after = int(conn.execute(
        'SELECT COUNT(*) FROM recurrence_exceptions'
    ).fetchone()[0])
    if count_before != count_after:
        raise RuntimeError(
            f'Row count mismatch: before={count_before} after={count_after}'
        )

    inconsistent = int(conn.execute(
        """
        SELECT COUNT(*) FROM recurrence_exceptions
        WHERE exception_type = 'modified'
          AND modified_time_type IS NOT NULL
          AND modified_time_type NOT IN ('all_day', 'morning', 'afternoon')
        """
    ).fetchone()[0])
    if inconsistent:
        raise RuntimeError(
            f'{inconsistent} modified_time_type values are invalid'
        )

    logger.success(
        f'Post-upgrade verification passed ({count_after} exceptions)'
    )


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

    if not backup_file.exists():
        logger.error(f'Backup file not found: {backup_file}')
        return False

    if not backup_file.is_file():
        logger.error(f'Backup path is not a regular file: {backup_file}')
        return False

    try:
        probe = sqlite_runner.connect(backup_file, binary=binary)
        try:
            result = probe.execute(
                'PRAGMA integrity_check'
            ).fetchone()
            if not result or result[0] != 'ok':
                logger.error(
                    f'Backup file failed integrity check: {result}'
                )
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
        sqlite_runner.create_backup(
            backup_file, db_path, binary=binary
        )
        conn = sqlite_runner.connect(db_path, binary=binary)
        try:
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        except sqlite3.Error as exc:
            logger.warning(
                f'WAL checkpoint failed (non-fatal): {exc}'
            )
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
    """Apply the v1.4.0 schema upgrade with backup and rollback."""
    logger.section(f'v{TARGET_VERSION} Schema Upgrade')
    logger.info(f'Database: {db_path}')
    logger.info(f'Backup directory: {backup_dir}')

    if not _check_integrity_standalone(db_path, binary, logger):
        return False

    if _is_schema_on_target(db_path, binary):
        logger.success('Schema is already on the v1.4.0 layout')
        return True

    if not force:
        confirmation = input('Continue with upgrade? [y/N] ').strip().lower()
        if confirmation != 'y':
            logger.warning('Upgrade cancelled by user')
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
        # Hold an EXCLUSIVE lock for the duration of the upgrade so that
        # no concurrent writer can interleave between the column snapshot
        # and the ALTER statements. Defense-in-depth: the operator is
        # already expected to have stopped the application.
        try:
            conn.execute('PRAGMA locking_mode = EXCLUSIVE')
        except sqlite3.Error as exc:
            logger.error(
                f'Could not enable EXCLUSIVE locking mode: {exc}'
            )
            logger.info('Ensure the application is stopped before upgrading.')
            return False

        conn.execute('BEGIN IMMEDIATE')
        try:
            columns = _get_column_names(conn)

            count_before = int(conn.execute(
                'SELECT COUNT(*) FROM recurrence_exceptions'
            ).fetchone()[0])
            logger.info(f'Existing exceptions: {count_before}')

            _add_new_columns(conn, columns, logger)
            _backfill(conn, columns, logger)
            _drop_legacy_columns(conn, columns, logger)
            _seed_limits_settings(conn, logger)
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

        # Force a full WAL checkpoint so the .db file is self-contained
        # and the -wal / -shm sidecars are empty. Without this, the next
        # reader could still be served from WAL until a passive checkpoint
        # runs, which would make file-level inspection inconsistent.
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
        f'Reworks the recurrence_exceptions schema. Runs in a single\n'
        f'transaction and creates a timestamped backup before touching\n'
        f'the database. Rolls back on any failure.'
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
        'fbk-time.db.backup-v1.4.0-20260411_120000.db\n'
        '\n'
        '  # Work on an exotic database file (override, no settings.json)\n'
        '  python upgrade.py upgrade --db /tmp/restored.db\n'
        '\n'
        '  # Use a custom SQLite binary (e.g. on RHEL 9 with old system SQLite)\n'
        '  python upgrade.py upgrade --app-path /var/www/fbk-time \\\n'
        '                            --sqlite-binary /opt/sqlite3/bin/sqlite3\n'
        '\n'
        'The script is location-agnostic. It requires the sqlite_runner\n'
        'module from the parent upgrades/ directory. Bundled static\n'
        'SQLite binaries are auto-detected when the system version is\n'
        'too old.'
    )

    # Parent parser with options shared across all subcommands.
    # Defining them here (and passing via `parents=`) lets the operator
    # put them after the subcommand, which matches the documented
    # invocation style (e.g. `upgrade.py upgrade --app-path ...`).
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
            ok = cmd_upgrade(db_path, backup_dir, args.force, binary, logger)
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
