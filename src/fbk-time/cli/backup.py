#!/usr/bin/env python3
"""Database backup and restore CLI tool.

Commands:
    list       — Show all backup records
    stats      — Show aggregate backup statistics
    create     — Create a new manual backup
    verify     — Verify archive integrity (single ID or all)
    delete     — Delete a backup record and its archive file
    cleanup    — Remove backups exceeding the retention policy
    restore    — Restore the database from a backup archive

Usage:
    python cli/backup.py list
    python cli/backup.py stats
    python cli/backup.py create [--description TEXT]
    python cli/backup.py verify <ID>
    python cli/backup.py verify --all
    python cli/backup.py delete <ID> [--yes]
    python cli/backup.py cleanup
    python cli/backup.py restore <ID> [--yes] [--no-pre-restore]

Restore accepts only registered backup IDs. To replay an external archive,
copy it into BACKUP_DIR first — the next backup directory sync registers
it (provided its manifest is valid) and assigns an ID.

The service must be stopped before running restore. The tool attempts to stop
the systemd service automatically; after restore the admin must restart it.
"""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app


class _Out:
    """Simple output with ANSI colour codes."""

    @staticmethod
    def ok(msg: str) -> None:
        print(f'\033[92m✓\033[0m {msg}')

    @staticmethod
    def err(msg: str) -> None:
        print(f'\033[91m✗\033[0m {msg}', file=sys.stderr)

    @staticmethod
    def warn(msg: str) -> None:
        print(f'\033[93m⚠\033[0m {msg}')

    @staticmethod
    def info(msg: str) -> None:
        print(f'\033[94m→\033[0m {msg}')

    @staticmethod
    def section(title: str) -> None:
        print(f'\n{"=" * 60}\n  {title}\n{"=" * 60}\n')

    @staticmethod
    def row(label: str, value: str) -> None:
        print(f'  {label:<22} {value}')


_out = _Out()

_SERVICE_NAME = 'fbk-time'


def _systemctl(*args) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ['systemctl', *args, _SERVICE_NAME],
            capture_output=True, text=True, timeout=10
        )
    except FileNotFoundError:
        raise RuntimeError('systemctl nicht gefunden — kein systemd-System')
    except subprocess.TimeoutExpired:
        raise RuntimeError('systemctl timed out')


def _confirm(prompt: str) -> bool:
    """Ask the operator to confirm a destructive action.

    Returns True only on explicit ``j``/``ja``. Any other input, EOF or
    Ctrl+C maps to a cancellation.
    """
    try:
        answer = input(f'{prompt} [j/N] ').strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in ('j', 'ja')


def _service_active() -> bool:
    """Return True if the fbk-time service is currently active.

    Raises:
        RuntimeError: If systemctl is unavailable.
    """
    result = _systemctl('is-active')
    return result.returncode == 0


def _stop_service() -> bool:
    """Stop the fbk-time service. Returns True on success."""
    try:
        result = _systemctl('stop')
        return result.returncode == 0
    except RuntimeError as e:
        _out.err(str(e))
        return False


def cmd_list(args, app) -> int:
    """List all backup records."""
    from modules.backup.models import BackupRecord
    from core.extensions import db

    with app.app_context():
        records = db.session.execute(
            db.select(BackupRecord).order_by(BackupRecord.created_at.desc())
        ).scalars().all()

    if not records:
        _out.info('Keine Backups vorhanden.')
        return 0

    header = f'  {"ID":<5} {"Erstellt":<20} {"Typ":<12} {"Status":<12} {"Größe":>10}  Beschreibung'
    print(header)
    print('  ' + '-' * 78)

    for r in records:
        ts = r.created_at.strftime('%d.%m.%Y %H:%M') if r.created_at else '-'
        size = f'{r.file_size_mb} MB'
        desc = (r.description or '')[:30]
        exists = '' if r.archive_exists else ' [fehlt]'
        print(f'  {r.id:<5} {ts:<20} {r.backup_type.label:<12} {r.status.label:<12} {size:>10}  {desc}{exists}')

    print(f'\n  Gesamt: {len(records)} Backup(s)')
    return 0


def cmd_create(args, app) -> int:
    """Create a new manual backup."""
    with app.app_context():
        from core.backup import backup_manager

        _out.info('Erstelle Backup…')
        record = backup_manager.create_backup(
            description=args.description or None,
            backup_type='manual'
        )

    if record:
        _out.ok(f'Backup #{record.id} erstellt ({record.file_size_mb} MB)')
        _out.info(f'Archiv: {record.file_path}')
        return 0

    _out.err('Backup-Erstellung fehlgeschlagen. Details im Systemlog.')
    return 1


def cmd_verify(args, app) -> int:
    """Verify integrity of one or all backups."""
    with app.app_context():
        from core.backup import backup_manager

        if args.all:
            _out.info('Verifiziere alle Backups…')
            verified, corrupted = backup_manager.verify_all()
            _out.ok(f'{verified} verifiziert.')
            if corrupted:
                _out.err(f'{corrupted} beschädigt.')
                return 1
            return 0

        if args.id is None:
            _out.err('Bitte Backup-ID oder --all angeben.')
            return 1

        ok, error = backup_manager.verify_backup(args.id)

    if ok:
        _out.ok(f'Backup #{args.id} erfolgreich verifiziert.')
        return 0

    _out.err(f'Verifikation fehlgeschlagen: {error}')
    return 1


def cmd_delete(args, app) -> int:
    """Delete a backup record and its archive file."""
    with app.app_context():
        from modules.backup.models import BackupRecord
        from core.extensions import db
        from core.backup import backup_manager

        record = db.session.get(BackupRecord, args.id)
        if not record:
            _out.err(f'Backup #{args.id} nicht gefunden.')
            return 1

        ts = record.created_at.strftime('%d.%m.%Y %H:%M') if record.created_at else '-'
        _out.row('Backup-ID:', str(record.id))
        _out.row('Erstellt:', ts)
        _out.row('Typ:', record.backup_type.label)
        _out.row('Größe:', f'{record.file_size_mb} MB')
        if record.description:
            _out.row('Beschreibung:', record.description)
        print()

        if not args.yes and not _confirm(f'Backup #{args.id} wirklich löschen?'):
            _out.warn('Abgebrochen.')
            return 1

        ok, error = backup_manager.delete_backup(args.id)

    if ok:
        _out.ok(f'Backup #{args.id} gelöscht.')
        return 0

    _out.err(f'Löschen fehlgeschlagen: {error}')
    return 1


def cmd_stats(args, app) -> int:
    """Show aggregate backup statistics."""
    with app.app_context():
        from modules.backup.services import get_backup_stats
        stats = get_backup_stats()

    _out.section('Backup-Statistik')
    _out.row('Backups gesamt:', str(stats['total']))
    _out.row('Verifiziert:', str(stats['verified']))
    _out.row('Beschädigt:', str(stats['corrupted']))
    _out.row('Gesamtgröße:', f"{stats['total_size_mb']} MB")
    return 0


def cmd_cleanup(args, app) -> int:
    """Remove old backups according to the retention policy."""
    _out.info('Bereinige alte Backups…')

    with app.app_context():
        from core.backup import backup_manager
        removed = backup_manager.cleanup_old_backups()

    _out.ok(f'{removed} Backup(s) entfernt.')
    return 0


def cmd_restore(args, app) -> int:
    """Restore the database from a registered backup."""
    _out.section('Datenbankwiederherstellung')

    if args.id is None:
        _out.err('Bitte Backup-ID angeben.')
        return 1

    with app.app_context():
        from modules.backup.models import BackupRecord
        from core.extensions import db
        from core.backup import backup_manager

        record = db.session.get(BackupRecord, args.id)
        if not record:
            _out.err(f'Backup #{args.id} nicht gefunden.')
            return 1

        archive_path = backup_manager._safe_archive_path(record.file_path)
        if archive_path is None:
            _out.err(
                'Archivpfad liegt außerhalb des Sicherungs-Verzeichnisses '
                'und wird abgelehnt.'
            )
            return 1
        if not archive_path.exists():
            _out.err(f'Archivdatei nicht gefunden: {archive_path}')
            return 1

        ts = record.created_at.strftime('%d.%m.%Y %H:%M') if record.created_at else '-'
        _out.row('Backup-ID:', str(record.id))
        _out.row('Erstellt:', ts)
        _out.row('Typ:', record.backup_type.label)
        _out.row('Status:', record.status.label)
        _out.row('Größe:', f'{record.file_size_mb} MB')
        _out.row('Archiv:', str(archive_path))
        if record.description:
            _out.row('Beschreibung:', record.description)
        print()

        try:
            service_was_active = _service_active()
        except RuntimeError as e:
            _out.err(str(e))
            _out.err('Dienststatus kann nicht ermittelt werden.')
            _out.info(f'Sicherstellen dass {_SERVICE_NAME} gestoppt ist, dann erneut ausführen.')
            return 1

        if service_was_active:
            _out.warn(f'Dienst {_SERVICE_NAME!r} ist aktiv und wird gestoppt.')
        else:
            _out.info(f'Dienst {_SERVICE_NAME!r} ist nicht aktiv.')

        if not args.yes and not _confirm('Wiederherstellung wirklich durchführen?'):
            _out.warn('Abgebrochen.')
            return 1

        if service_was_active:
            _out.info(f'Stoppe {_SERVICE_NAME}…')
            if not _stop_service():
                _out.err('Dienst konnte nicht gestoppt werden.')
                _out.info(f'Tipp: sudo systemctl stop {_SERVICE_NAME}')
                return 1
            _out.ok('Dienst gestoppt.')

        ok, message = backup_manager.restore_from_archive(
            archive_path=archive_path,
            pre_restore=not args.no_pre_restore
        )

        if ok:
            _out.ok(message)
            print()
            _out.warn('Dienst muss manuell neu gestartet werden:')
            _out.info(f'  sudo systemctl start {_SERVICE_NAME}')
            return 0

        _out.err(message)
        if service_was_active:
            _out.warn(f'Bitte Dienst manuell prüfen: systemctl status {_SERVICE_NAME}')
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='backup',
        description='FBK-Time Backup & Restore',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python cli/backup.py list
    python cli/backup.py stats
    python cli/backup.py create --description "Vor Update"
    python cli/backup.py verify 5
    python cli/backup.py verify --all
    python cli/backup.py delete 5
    python cli/backup.py delete 5 --yes
    python cli/backup.py cleanup
    python cli/backup.py restore 5
    python cli/backup.py restore 5 --yes --no-pre-restore
        """
    )

    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('list', help='Alle Backups anzeigen')
    sub.add_parser('stats', help='Backup-Statistik anzeigen')

    p_create = sub.add_parser('create', help='Backup erstellen')
    p_create.add_argument('--description', '-d', metavar='TEXT',
                          help='Optionale Beschreibung')

    p_verify = sub.add_parser('verify', help='Archivintegrität prüfen')
    p_verify.add_argument('id', type=int, nargs='?', default=None, metavar='ID',
                          help='Backup-ID (entfällt mit --all)')
    p_verify.add_argument('--all', '-a', action='store_true',
                          help='Alle Backups prüfen')

    p_delete = sub.add_parser('delete', help='Backup löschen')
    p_delete.add_argument('id', type=int, metavar='ID', help='Backup-ID')
    p_delete.add_argument('--yes', '-y', action='store_true',
                          help='Ohne Bestätigung fortfahren')

    sub.add_parser('cleanup', help='Alte Backups gemäß Retention-Policy entfernen')

    p_restore = sub.add_parser('restore', help='Datenbank wiederherstellen')
    p_restore.add_argument('id', type=int, metavar='ID',
                           help='Backup-ID aus der Datenbank')
    p_restore.add_argument('--yes', '-y', action='store_true',
                           help='Ohne Bestätigung fortfahren')
    p_restore.add_argument('--no-pre-restore', action='store_true',
                           help='Keinen Snapshot vor Wiederherstellung erstellen')

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    app = create_app(cli_mode=True)

    commands = {
        'list': cmd_list,
        'stats': cmd_stats,
        'create': cmd_create,
        'verify': cmd_verify,
        'delete': cmd_delete,
        'cleanup': cmd_cleanup,
        'restore': cmd_restore,
    }

    try:
        sys.exit(commands[args.command](args, app))
    except KeyboardInterrupt:
        print()
        _out.warn('Abgebrochen.')
        sys.exit(1)
    except Exception as e:
        _out.err(f'Fehler: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
