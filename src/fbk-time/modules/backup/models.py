"""Backup models.

Provides the BackupRecord model for tracking database backup history.
"""

import enum
from datetime import datetime, timezone

from core.extensions import db


def _utc_now():
    """Return current UTC datetime without timezone info for SQLite."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class BackupType(enum.Enum):
    """Type of backup creation."""

    MANUAL = "manual"
    SCHEDULED = "scheduled"
    PRE_RESTORE = "pre_restore"

    @property
    def label(self) -> str:
        """Localized German label for display."""
        return _BACKUP_TYPE_LABELS[self]


class BackupStatus(enum.Enum):
    """Integrity status of a backup archive."""

    CREATED = "created"
    VERIFIED = "verified"
    CORRUPTED = "corrupted"

    @property
    def label(self) -> str:
        """Localized German label for display."""
        return _BACKUP_STATUS_LABELS[self]


_BACKUP_TYPE_LABELS = {
    BackupType.MANUAL: 'Manuell',
    BackupType.SCHEDULED: 'Geplant',
    BackupType.PRE_RESTORE: 'Snapshot',
}


_BACKUP_STATUS_LABELS = {
    BackupStatus.CREATED: 'Erstellt',
    BackupStatus.VERIFIED: 'Verifiziert',
    BackupStatus.CORRUPTED: 'Beschädigt',
}


class BackupRecord(db.Model):
    """Backup archive metadata stored in the database.

    Attributes:
        id: Primary key.
        backup_type: How the backup was triggered.
        file_path: Absolute path to the tar.gz archive.
        file_size: Archive size in bytes.
        checksum: SHA-256 checksum of the archive file ('sha256:<hex>').
        status: Current verification status.
        description: Optional human-readable note.
        verified_at: Timestamp of last verification run.
        verification_error: Error message if last verification failed.
        created_by_id: FK to User who triggered the backup (NULL for system).
        created_at: Creation timestamp.
    """

    __tablename__ = 'backup_records'

    id = db.Column(db.Integer, primary_key=True)
    backup_type = db.Column(db.Enum(BackupType), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)
    file_size = db.Column(db.Integer, nullable=False)
    checksum = db.Column(db.String(71), nullable=False)
    status = db.Column(
        db.Enum(BackupStatus),
        nullable=False,
        default=BackupStatus.CREATED,
        index=True
    )
    description = db.Column(db.String(255), nullable=True)
    verified_at = db.Column(db.DateTime, nullable=True)
    verification_error = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True,
        index=True
    )
    created_at = db.Column(db.DateTime, default=_utc_now, nullable=False, index=True)

    created_by = db.relationship('User', foreign_keys=[created_by_id])

    def __repr__(self):
        return f'<BackupRecord {self.id} {self.backup_type.value} {self.status.value}>'

    @property
    def file_size_mb(self) -> float:
        """File size in megabytes, rounded to two decimal places."""
        return round(self.file_size / (1024 * 1024), 2)

    @property
    def archive_exists(self) -> bool:
        """Whether the archive file exists on disk."""
        from pathlib import Path
        return Path(self.file_path).exists()
