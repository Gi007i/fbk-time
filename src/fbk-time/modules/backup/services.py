"""Backup services.

Provides database query and business logic for the backup module.
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from sqlalchemy import func, select

from core.db import db
from .models import BackupRecord, BackupStatus, BackupType

# The scheduler fires within one poll tick, catches a slot up for
# CATCH_UP_GRACE after a restart, and the archive takes time to write, so
# a slot that has just passed is not yet a missed run.
_SCHEDULED_BACKUP_GRACE = timedelta(minutes=30)


def get_backup_list(page: int, per_page: int) -> List[BackupRecord]:
    """Return a paginated list of backup records, newest first.

    The total comes from ``get_backup_stats``, so pagination and
    statistics share one tally that cannot diverge under concurrent inserts.

    Args:
        page: 1-indexed page number.
        per_page: Number of records per page (0 = all).

    Returns:
        The records for the requested page.
    """
    query = select(BackupRecord).order_by(BackupRecord.created_at.desc())

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


def get_overdue_scheduled_backup() -> Optional[dict]:
    """Return details when the daily scheduled backup has not run on time.

    Returns:
        None while no scheduled run is overdue, otherwise a dict with
        ``due_at`` and ``last_at`` (naive UTC; ``last_at`` is None without
        any usable scheduled backup).
    """
    from core.scheduler import last_daily_run
    from core.settings_manager import settings_manager
    from modules.settings.models import Setting

    if not settings_manager.get('backup_scheduled_enabled'):
        return None

    try:
        due_at = last_daily_run(settings_manager.get('backup_time'))
    except ValueError:
        # The scheduler refuses such a value at registration and logs it.
        return None

    now = datetime.now(timezone.utc)
    if now - due_at < _SCHEDULED_BACKUP_GRACE:
        return None

    due_naive = due_at.replace(tzinfo=None)

    # A schedule enabled or moved after the slot only takes effect at the
    # next slot, so the missing run is expected rather than a failure. The
    # timezone moves the slot as well.
    schedule_changed_at = db.session.execute(
        select(func.max(Setting.updated_at)).where(
            Setting.key.in_(('backup_scheduled_enabled', 'backup_time', 'app_timezone'))
        )
    ).scalar()
    if schedule_changed_at is not None and schedule_changed_at >= due_naive:
        return None

    last_at = latest_usable_scheduled_backup_at(not_after=now.replace(tzinfo=None))
    if last_at is not None and last_at >= due_naive:
        return None

    return {'due_at': due_naive, 'last_at': last_at}


def latest_usable_scheduled_backup_at(not_after: datetime) -> Optional[datetime]:
    """Return when the newest scheduled backup that passed or awaits verification was created.

    A creation time after ``not_after`` (clock step or a registered archive
    with a future timestamp) would otherwise mark every slot up to it as done.

    Args:
        not_after: Naive UTC upper bound, normally the current time.

    Returns:
        Naive UTC datetime, or None without such a backup.
    """
    return db.session.execute(
        select(func.max(BackupRecord.created_at)).where(
            BackupRecord.backup_type == BackupType.SCHEDULED,
            BackupRecord.status != BackupStatus.CORRUPTED,
            BackupRecord.created_at <= not_after
        )
    ).scalar()


def get_backup_stats() -> dict:
    """Return aggregate statistics for the backup overview.

    Returns:
        Dict with total, verified, corrupted, total_size_mb.
    """
    total = db.session.execute(
        select(func.count()).select_from(BackupRecord)
    ).scalar() or 0

    verified = db.session.execute(
        select(func.count()).select_from(BackupRecord).where(
            BackupRecord.status == BackupStatus.VERIFIED
        )
    ).scalar() or 0

    corrupted = db.session.execute(
        select(func.count()).select_from(BackupRecord).where(
            BackupRecord.status == BackupStatus.CORRUPTED
        )
    ).scalar() or 0

    total_size = db.session.execute(
        select(func.sum(BackupRecord.file_size)).select_from(BackupRecord)
    ).scalar() or 0

    return {
        'total': total,
        'verified': verified,
        'corrupted': corrupted,
        'total_size_mb': round(total_size / (1024 * 1024), 2)
    }
