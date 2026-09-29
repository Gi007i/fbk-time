"""Backup models.

Provides the BackupRecord model for tracking database backup history.
"""

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base
from modules.auth.models import User


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


class BackupRecord(Base):
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

    id: Mapped[int] = mapped_column(primary_key=True)
    backup_type: Mapped[BackupType] = mapped_column(Enum(BackupType))
    file_path: Mapped[str] = mapped_column(String(500))
    file_size: Mapped[int]
    checksum: Mapped[str] = mapped_column(String(71))
    status: Mapped[BackupStatus] = mapped_column(
        Enum(BackupStatus),
        default=BackupStatus.CREATED,
        index=True
    )
    description: Mapped[Optional[str]] = mapped_column(String(255))
    verified_at: Mapped[Optional[datetime]]
    verification_error: Mapped[Optional[str]] = mapped_column(Text)
    created_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey('users.id', ondelete='SET NULL'),
        index=True
    )
    created_at: Mapped[datetime] = mapped_column(default=_utc_now, index=True)

    created_by: Mapped[Optional[User]] = relationship(foreign_keys=[created_by_id])

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

    @property
    def archive_name(self) -> str:
        """Base name of the archive file for display (no directory path)."""
        from pathlib import Path
        return Path(self.file_path).name
