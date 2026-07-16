"""Backup services.

Provides database query and business logic for the backup module.
"""

from typing import List, Optional, Tuple

from core.extensions import db
from .models import BackupRecord, BackupStatus


def get_backup_list(page: int, per_page: int) -> List[BackupRecord]:
    """Return a paginated list of backup records, newest first.

    The total record count comes from ``get_backup_stats`` so pagination
    and statistics derive from a single tally; a second count here could
    diverge under concurrent inserts.

    Args:
        page: 1-indexed page number.
        per_page: Number of records per page (0 = all).

    Returns:
        The records for the requested page.
    """
    query = db.select(BackupRecord).order_by(BackupRecord.created_at.desc())

    if per_page == 0:
        records = db.session.execute(query).scalars().all()
    else:
        offset = (page - 1) * per_page
        records = db.session.execute(query.limit(per_page).offset(offset)).scalars().all()

    return list(records)


def get_backup_or_404(backup_id: int) -> BackupRecord:
    """Return a BackupRecord by ID or abort with 404.

    Args:
        backup_id: Primary key of the backup record.

    Returns:
        BackupRecord instance.
    """
    from flask import abort
    record = db.session.get(BackupRecord, backup_id)
    if not record:
        abort(404)
    return record


def create_backup(description: Optional[str], created_by_id: int) -> Tuple[bool, str]:
    """Create a manual database backup.

    Args:
        description: Optional description for the backup.
        created_by_id: ID of the user initiating the backup.

    Returns:
        Tuple of (success, message).
    """
    from core.backup import backup_manager

    record = backup_manager.create_backup(
        description=description or None,
        backup_type='manual',
        created_by_id=created_by_id
    )

    if record:
        return True, f'Sicherung #{record.id} erfolgreich erstellt.'
    return False, 'Erstellung der Sicherung fehlgeschlagen. Details im Systemlog.'


def verify_backup(backup_id: int) -> Tuple[bool, str]:
    """Verify the integrity of a backup archive.

    Args:
        backup_id: Primary key of the backup record.

    Returns:
        Tuple of (success, message).
    """
    from core.backup import backup_manager

    ok, error = backup_manager.verify_backup(backup_id)
    if ok:
        return True, f'Sicherung #{backup_id} erfolgreich verifiziert.'
    return False, f'Verifikation von Sicherung #{backup_id} fehlgeschlagen: {error}'


def delete_backup(backup_id: int) -> Tuple[bool, str]:
    """Delete a backup record and its archive file.

    Args:
        backup_id: Primary key of the backup record.

    Returns:
        Tuple of (success, message).
    """
    from core.backup import backup_manager

    ok, error = backup_manager.delete_backup(backup_id)
    if ok:
        return True, f'Sicherung #{backup_id} gelöscht.'
    return False, f'Löschen von Sicherung #{backup_id} fehlgeschlagen: {error}'


def sync_filesystem() -> Tuple[bool, str]:
    """Reconcile the backup directory with database records.

    Registers archives found on disk that have no record yet, repairs
    records whose ``file_path`` no longer matches the archive location,
    and removes records whose archive file is gone.

    Returns:
        Tuple of (success, message).
    """
    from core.backup import backup_manager

    added, updated, removed, errors = backup_manager.sync_filesystem()

    parts = [
        f'{added} neu eingelesen',
        f'{updated} aktualisiert',
        f'{removed} entfernt',
    ]
    message = 'Synchronisation abgeschlossen: ' + ', '.join(parts) + '.'
    if errors:
        message += f' Hinweise: {len(errors)} Fehler — {errors[0]}'
        return False, message
    return True, message


def get_backup_stats() -> dict:
    """Return aggregate statistics for the backup overview.

    Returns:
        Dict with total, verified, corrupted, total_size_mb.
    """
    total = db.session.execute(
        db.select(db.func.count()).select_from(BackupRecord)
    ).scalar() or 0

    verified = db.session.execute(
        db.select(db.func.count()).select_from(BackupRecord).where(
            BackupRecord.status == BackupStatus.VERIFIED
        )
    ).scalar() or 0

    corrupted = db.session.execute(
        db.select(db.func.count()).select_from(BackupRecord).where(
            BackupRecord.status == BackupStatus.CORRUPTED
        )
    ).scalar() or 0

    total_size = db.session.execute(
        db.select(db.func.sum(BackupRecord.file_size)).select_from(BackupRecord)
    ).scalar() or 0

    return {
        'total': total,
        'verified': verified,
        'corrupted': corrupted,
        'total_size_mb': round(total_size / (1024 * 1024), 2)
    }
