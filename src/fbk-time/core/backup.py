"""Database backup and restore manager.

Creates WAL-safe SQLite backups using the online backup API (sqlite3.backup),
packages them as gzip-compressed tar archives with a SHA-256 manifest, and
handles restore with WAL file cleanup.

Archive structure:
    database/fbk-time.db     — WAL-safe SQLite snapshot
    config/settings.json     — static application configuration
    config/.env              — environment file (SECRET_KEY)
    metadata/manifest.json   — timestamps, checksums, app version
"""

import fcntl
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import tarfile
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from sqlalchemy import select

from core.version import APP_VERSION


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _utc_now_str() -> str:
    return _utc_now().isoformat(timespec='seconds')


def _checksum(path: Path) -> str:
    """Compute SHA-256 checksum of a file.

    Returns:
        Hex digest prefixed with 'sha256:'.
    """
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return f'sha256:{h.hexdigest()}'


def _parse_manifest_timestamp(value) -> Optional[datetime]:
    """Parse a manifest ``created_at`` into naive UTC.

    Accepts only the timezone-aware ISO 8601 form the archive writer
    produces.

    Returns:
        Naive UTC datetime, or None if the value is not such a timestamp.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def version_mismatch_message(archive_version: str) -> str:
    """Explain why an archive of another application version is refused.

    Args:
        archive_version: ``app_version`` recorded in the archive manifest.
    """
    return (
        f'Die Sicherung stammt aus Version {archive_version}, installiert ist '
        f'Version {APP_VERSION}. Datenbank und settings.json passen nur zu ihrer '
        f'eigenen Version. Version {archive_version} installieren und die '
        f'Sicherung dort wiederherstellen; ist sie älter, die Datenbank danach '
        f'mit den Upgrade-Skripten unter upgrades/ auf Version {APP_VERSION} '
        f'bringen.'
    )


class BackupManager:
    """Manages creation, verification, and restore of application backups.

    Each archive contains the database, static configuration and the
    environment file, so a full restore requires only the archive.
    """

    _ENTRY_DB = 'database/fbk-time.db'
    _ENTRY_SETTINGS = 'config/settings.json'
    _ENTRY_ENV = 'config/.env'
    _ENTRY_MANIFEST = 'metadata/manifest.json'

    _MANIFEST_SCHEMA_VERSION = '1.0'
    _MAX_DESCRIPTION_LEN = 255
    _SNAPSHOT_BUSY_TIMEOUT_MS = 30000

    _MAX_MANIFEST_BYTES = 1024 * 1024
    _MAX_CONFIG_ENTRY_BYTES = 4 * 1024 * 1024
    _MAX_DB_ENTRY_BYTES = 2 * 1024 * 1024 * 1024

    # The random suffix is absent in archives written before v2.0.0.
    _ARCHIVE_NAME_PATTERN = re.compile(
        r'backup_\d{8}_\d{6}_(manual|scheduled|pre_restore)(?:_[0-9a-f]{6})?\.tar\.gz'
    )
    _PARTIAL_NAME_PATTERN = re.compile(
        r'\.backup_\d{8}_\d{6}_(?:manual|scheduled|pre_restore)_[0-9a-f]{6}\.tar\.gz\.partial'
    )

    @classmethod
    def _required_entries(cls) -> Tuple[str, ...]:
        return (cls._ENTRY_DB, cls._ENTRY_SETTINGS, cls._ENTRY_ENV)

    def __init__(self, app=None):
        self.app = app
        self._db_path: Optional[Path] = None
        self._app_root: Path = Path(__file__).resolve().parent.parent
        self._operation_rlock = threading.RLock()
        self._operation_depth = 0
        self._operation_fd = None

        if app is not None:
            self.init_app(app)

    def init_app(self, app) -> None:
        self.app = app
        uri: str = app.config['DATABASE_URI']
        self._db_path = Path(uri.replace('sqlite:///', ''))

    def _backup_dir(self) -> Path:
        return Path(self.app.config['BACKUP_DIR'])

    @contextmanager
    def _operation_lock(self, blocking: bool = True):
        """Serialize backup write operations across processes.

        ``lockf`` instead of ``flock``: a record lock is not inherited across
        the Gunicorn fork, but it gives no exclusion between threads and is
        dropped by closing any descriptor, hence the RLock and a single
        descriptor held by the outermost acquisition.

        Args:
            blocking: When False, raise ``BlockingIOError`` immediately if
                the lock is held (auto-discovery must never delay a request).
        """
        if not self._operation_rlock.acquire(blocking=blocking):
            raise BlockingIOError('Sicherungsvorgang läuft bereits.')

        try:
            if self._operation_depth == 0:
                lock_path = Path(self.app.config['RUNTIME_DIR']) / 'backup-operation.lock'
                # O_NOFOLLOW: a planted symlink must not redirect the open.
                fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                flags = fcntl.LOCK_EX
                if not blocking:
                    flags |= fcntl.LOCK_NB
                try:
                    fcntl.lockf(fd, flags)
                except BaseException:
                    os.close(fd)
                    raise
                self._operation_fd = fd
            self._operation_depth += 1
        except BaseException:
            self._operation_rlock.release()
            raise

        try:
            yield
        finally:
            self._operation_depth -= 1
            if self._operation_depth == 0:
                os.close(self._operation_fd)
                self._operation_fd = None
            self._operation_rlock.release()

    def safe_archive_path(self, path_str: str) -> Optional[Path]:
        """Resolve a stored archive path and ensure it stays within BACKUP_DIR.

        Guards every unlink, so a tampered ``file_path`` cannot remove files
        elsewhere.

        Returns:
            Resolved path, or None if it lies outside or cannot be resolved.
        """
        try:
            backup_dir = self._backup_dir().resolve()
            candidate = Path(path_str).resolve()
            candidate.relative_to(backup_dir)
            return candidate
        except (ValueError, OSError):
            return None

    def _archive_name(self, backup_type: str) -> str:
        # The suffix keeps two same-type backups of one second on separate
        # files, so deleting one cannot take the other's archive with it.
        ts = _utc_now().strftime('%Y%m%d_%H%M%S')
        return f'backup_{ts}_{backup_type}_{secrets.token_hex(3)}.tar.gz'

    def _remove_orphan_archive(self, archive_path: Path) -> None:
        """Delete an archive left behind by a failed creation step.

        Keeps an archive from staying on disk without a matching record.
        """
        if not archive_path.exists():
            return
        try:
            archive_path.unlink()
        except OSError as exc:
            if self.app:
                self.app.logger.error(
                    f"Failed to remove orphan archive {archive_path}: {exc}"
                )

    def _snapshot_db(self, dest_path: Path) -> None:
        """Create a WAL-safe copy of the live database.

        sqlite3.backup() includes committed WAL frames and is safe while the
        app runs; the busy timeout waits for writers instead of SQLITE_BUSY.
        """
        timeout_s = self._SNAPSHOT_BUSY_TIMEOUT_MS / 1000
        src = sqlite3.connect(str(self._db_path), timeout=timeout_s)
        dst = sqlite3.connect(str(dest_path), timeout=timeout_s)
        try:
            src.execute(f'PRAGMA busy_timeout = {self._SNAPSHOT_BUSY_TIMEOUT_MS}')
            dst.execute(f'PRAGMA busy_timeout = {self._SNAPSHOT_BUSY_TIMEOUT_MS}')
            src.backup(dst, pages=200)
        finally:
            dst.close()
            src.close()

    def _add_bytes(self, tar: tarfile.TarFile, data: bytes, arcname: str) -> None:
        buf = io.BytesIO(data)
        info = tarfile.TarInfo(name=arcname)
        info.size = len(data)
        tar.addfile(info, buf)

    def _create_archive(self, archive_path: Path, db_snapshot: Path,
                        description: Optional[str] = None) -> None:
        """Build the tar.gz archive. Raises if any required source file is missing.

        The description goes into the manifest so ``sync_filesystem`` can
        recover it after a restore.
        """
        settings_path = self._app_root / 'settings.json'
        env_path = self._app_root / '.env'

        if not settings_path.exists():
            raise FileNotFoundError(f'settings.json nicht gefunden: {settings_path}')
        if not env_path.exists():
            raise FileNotFoundError(f'.env nicht gefunden: {env_path}')

        checksums = {
            self._ENTRY_DB: _checksum(db_snapshot),
            self._ENTRY_SETTINGS: _checksum(settings_path),
            self._ENTRY_ENV: _checksum(env_path),
        }

        # A partial name the sync ignores, renamed once complete, so a crash
        # never leaves a truncated archive. 0o600 up front since it carries
        # SECRET_KEY and hashes; O_EXCL refuses a planted file or symlink.
        partial_path = archive_path.with_name(f'.{archive_path.name}.partial')
        fd = os.open(str(partial_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'wb') as raw:
                with tarfile.open(fileobj=raw, mode='w:gz', compresslevel=6) as tar:
                    tar.add(str(db_snapshot), arcname=self._ENTRY_DB)
                    tar.add(str(settings_path), arcname=self._ENTRY_SETTINGS)
                    tar.add(str(env_path), arcname=self._ENTRY_ENV)

                    manifest = {
                        'schema_version': self._MANIFEST_SCHEMA_VERSION,
                        'created_at': _utc_now_str(),
                        'app_version': APP_VERSION,
                        'description': description,
                        'checksums': checksums,
                    }
                    self._add_bytes(
                        tar,
                        json.dumps(manifest, indent=2, ensure_ascii=False).encode(),
                        self._ENTRY_MANIFEST
                    )
                raw.flush()
                os.fsync(raw.fileno())
            os.replace(partial_path, archive_path)
        except BaseException:
            partial_path.unlink(missing_ok=True)
            raise

    def _read_manifest(self, archive_path: Path) -> Optional[dict]:
        try:
            with tarfile.open(archive_path, 'r:gz') as tar:
                member = tar.getmember(self._ENTRY_MANIFEST)
                # TarInfo.size is attacker-controlled; a tiny gzip can declare a
                # multi-GB manifest, so reject and bound-read to avoid OOM.
                if member.size > self._MAX_MANIFEST_BYTES:
                    raise ValueError('manifest exceeds size limit')
                f = tar.extractfile(member)
                if f:
                    data = f.read(self._MAX_MANIFEST_BYTES + 1)
                    if len(data) > self._MAX_MANIFEST_BYTES:
                        raise ValueError('manifest exceeds size limit')
                    return json.loads(data.decode())
        except Exception as e:
            if self.app:
                self.app.logger.warning(
                    f"Failed to read manifest from {archive_path}: {e}"
                )
        return None

    def archive_app_version(self, archive_path: Path) -> Optional[str]:
        """Return the application version recorded in an archive manifest.

        Lets the restore CLI refuse a version mismatch before it stops the
        service; ``restore_from_archive`` enforces the same rule again.

        Returns:
            The ``app_version`` string, or None if the manifest is unreadable
            or lacks it.
        """
        manifest = self._read_manifest(archive_path)
        if not isinstance(manifest, dict):
            return None
        version = manifest.get('app_version')
        return version if isinstance(version, str) else None

    def _validate_archive(
        self, archive_path: Path
    ) -> Tuple[Optional[dict], Optional[str]]:
        """Strictly validate a tar.gz as a genuine application backup.

        Keeps a foreign tar.gz in the backup directory from becoming a
        restorable record. Checksums prove integrity only; authenticity comes
        from BackupRecord.checksum outside the archive (no HMAC, which would
        invalidate every existing archive).

        Args:
            archive_path: Archive to validate.

        Returns:
            (manifest, None) on success, (None, error_message) otherwise.
        """
        manifest = self._read_manifest(archive_path)
        if not manifest:
            return None, 'Manifest fehlt oder unlesbar'

        if not isinstance(manifest, dict):
            return None, 'Manifest hat unerwartete Struktur'

        if manifest.get('schema_version') != self._MANIFEST_SCHEMA_VERSION:
            return None, (
                f'Manifest-Schema unbekannt: '
                f'{manifest.get("schema_version")!r}'
            )

        if _parse_manifest_timestamp(manifest.get('created_at')) is None:
            return None, 'Manifest-Feld created_at fehlt oder ungültig'

        if not isinstance(manifest.get('app_version'), str):
            return None, 'Manifest-Feld app_version fehlt oder ungültig'

        description = manifest.get('description')
        if description is not None and not isinstance(description, str):
            return None, 'Manifest-Feld description hat unerwarteten Typ'
        if isinstance(description, str) and len(description) > self._MAX_DESCRIPTION_LEN:
            return None, 'Manifest-Feld description überschreitet Längenlimit'
        # The CLI prints this straight to an escape-interpreting terminal, so
        # control characters could overdraw earlier lines of the listing.
        if isinstance(description, str) and any(ord(c) < 0x20 for c in description):
            return None, 'Manifest-Feld description enthält Steuerzeichen'

        checksums = manifest.get('checksums')
        if not isinstance(checksums, dict) or not checksums:
            return None, 'Manifest-Feld checksums fehlt oder ungültig'

        for entry in self._required_entries():
            if entry not in checksums:
                return None, f'Manifest deckt erforderlichen Eintrag nicht ab: {entry}'

        try:
            with tarfile.open(archive_path, 'r:gz') as tar:
                for entry, expected_cs in checksums.items():
                    if not isinstance(expected_cs, str) or not expected_cs.startswith('sha256:'):
                        return None, f'Ungültige Prüfsumme für {entry}'
                    try:
                        member = tar.getmember(entry)
                    except KeyError:
                        return None, f'Archiveintrag fehlt: {entry}'
                    # A link member would send extractfile chasing its target
                    # inside the archive, which raises KeyError when that
                    # target is absent. The required entries are plain files.
                    if not member.isreg():
                        return None, f'Archiveintrag ist keine reguläre Datei: {entry}'
                    cap = (self._MAX_DB_ENTRY_BYTES if entry == self._ENTRY_DB
                           else self._MAX_CONFIG_ENTRY_BYTES)
                    if member.size > cap:
                        return None, f'Archiveintrag zu groß: {entry}'
                    f = tar.extractfile(member)
                    if not f:
                        return None, f'Archiveintrag nicht lesbar: {entry}'
                    # Block-wise read: a decompression bomb cannot exhaust memory.
                    h = hashlib.sha256()
                    for chunk in iter(lambda: f.read(65536), b''):
                        h.update(chunk)
                    actual = f'sha256:{h.hexdigest()}'
                    if actual != expected_cs:
                        return None, f'Prüfsumme abweichend: {entry}'
        except (tarfile.TarError, OSError, KeyError, RecursionError) as e:
            return None, f'Archiv nicht lesbar: {e}'

        return manifest, None

    def create_backup(self, description: Optional[str] = None,
                      backup_type: str = 'manual',
                      created_by_id: Optional[int] = None) -> Optional[object]:
        """Create a compressed backup archive.

        The archive is verified before returning, so success means restorable.

        Args:
            description: Optional human-readable note.
            backup_type: One of 'manual', 'scheduled', 'pre_restore'.
            created_by_id: User ID for audit trail (None for system tasks).

        Returns:
            BackupRecord instance on success, None on failure.
        """
        from modules.backup.models import BackupRecord, BackupStatus, BackupType
        from core.db import db

        backup_dir = self._backup_dir()
        archive_path = backup_dir / self._archive_name(backup_type)

        try:
            with self._operation_lock():
                with tempfile.TemporaryDirectory() as tmp:
                    db_snapshot = Path(tmp) / 'fbk-time.db'
                    self._snapshot_db(db_snapshot)
                    self._create_archive(archive_path, db_snapshot, description=description)

                file_size = archive_path.stat().st_size
                archive_checksum = _checksum(archive_path)

                record = BackupRecord(
                    backup_type=BackupType(backup_type),
                    file_path=str(archive_path),
                    file_size=file_size,
                    checksum=archive_checksum,
                    status=BackupStatus.CREATED,
                    description=description,
                    created_by_id=created_by_id
                )
                db.session.add(record)
                db.session.commit()

                verified, verify_error = self._verify_record(record)

            if not verified:
                # The record stays visible as CORRUPTED; success would claim a
                # restorable backup.
                if self.app:
                    self.app.logger.error(
                        f"Backup #{record.id} failed verification: {verify_error}"
                    )
                return None

            return record

        except Exception as e:
            db.session.rollback()
            self._remove_orphan_archive(archive_path)
            if self.app:
                self.app.logger.error(f"Backup creation failed: {e}")
            return None

    def verify_backup(self, record_id: int) -> Tuple[bool, Optional[str]]:
        """Verify archive integrity for a backup record.

        Args:
            record_id: Primary key of the BackupRecord.

        Returns:
            Tuple of (success, error_message).
        """
        from modules.backup.models import BackupRecord
        from core.db import db

        record = db.session.get(BackupRecord, record_id)
        if not record:
            return False, 'Sicherung nicht gefunden'

        return self._verify_record(record)

    def _verify_record(self, record) -> Tuple[bool, Optional[str]]:
        from modules.backup.models import BackupStatus
        from core.db import db

        archive_path = Path(record.file_path)
        error: Optional[str] = None

        if not archive_path.exists():
            error = 'Archivdatei nicht gefunden'
        elif _checksum(archive_path) != record.checksum:
            error = 'Prüfsummenfehler: Archiv wurde verändert'
        else:
            _, error = self._validate_archive(archive_path)

        if error is None:
            record.status = BackupStatus.VERIFIED
            record.verified_at = _utc_now_naive()
            record.verification_error = None
        else:
            record.status = BackupStatus.CORRUPTED
            record.verified_at = _utc_now_naive()
            record.verification_error = error

        db.session.commit()
        return error is None, error

    def verify_all(self) -> Tuple[int, int]:
        """Verify all backups. Returns (verified_count, corrupted_count)."""
        from modules.backup.models import BackupRecord
        from core.db import db

        records = db.session.execute(select(BackupRecord)).scalars().all()
        verified = corrupted = 0
        for record in records:
            ok, _ = self._verify_record(record)
            if ok:
                verified += 1
            else:
                corrupted += 1
        return verified, corrupted

    def cleanup_old_backups(self) -> int:
        """Remove backups exceeding the configured retention count.

        Keeps the newest ``backup_retention_count`` archives.

        Returns:
            Number of backups removed.
        """
        from modules.backup.models import BackupRecord
        from core.db import db
        from core.settings_manager import settings_manager

        keep_count = max(1, int(settings_manager.get('backup_retention_count')))

        with self._operation_lock():
            all_records = db.session.execute(
                select(BackupRecord).order_by(BackupRecord.created_at.desc())
            ).scalars().all()

            removed = 0
            for record in all_records[keep_count:]:
                try:
                    path = self.safe_archive_path(record.file_path)
                    if path is None:
                        if self.app:
                            self.app.logger.error(
                                f"Refused to remove backup {record.id}: "
                                f"file_path outside BACKUP_DIR"
                            )
                        continue
                    if path.exists():
                        path.unlink()
                    db.session.delete(record)
                    removed += 1
                except Exception as e:
                    if self.app:
                        self.app.logger.error(f"Failed to remove backup {record.id}: {e}")

            if removed:
                db.session.commit()
            return removed

    def delete_backup(self, record_id: int) -> Tuple[bool, Optional[str]]:
        """Delete a backup record and its archive file.

        Args:
            record_id: Primary key of the BackupRecord.

        Returns:
            Tuple of (success, error_message).
        """
        from modules.backup.models import BackupRecord
        from core.db import db

        with self._operation_lock():
            record = db.session.get(BackupRecord, record_id)
            if not record:
                return False, 'Sicherung nicht gefunden'

            try:
                path = self.safe_archive_path(record.file_path)
                if path is None:
                    return False, 'Archivpfad liegt außerhalb des Sicherungs-Verzeichnisses'
                if path.exists():
                    path.unlink()
                db.session.delete(record)
                db.session.commit()
                return True, None
            except Exception as e:
                db.session.rollback()
                return False, str(e)

    def sync_filesystem(self, blocking: bool = True) -> Tuple[int, int, int, list]:
        """Reconcile the backup directory with BackupRecord entries.

        Matched by filename: an unknown archive is registered after full
        validation (foreign or truncated files are refused), a moved one gets
        its new path (restore on a host with another ``BACKUP_DIR``), and a
        record without archive is deleted.

        Args:
            blocking: When False, skip if another backup operation runs.

        Returns:
            Tuple of (added, updated, removed, errors). A skipped run returns
            zero counters and one explanatory error.
        """
        from modules.backup.models import BackupRecord, BackupStatus
        from core.db import db

        backup_dir = self._backup_dir()
        errors: list = []
        added = 0
        updated = 0
        removed = 0

        if not backup_dir.exists():
            return 0, 0, 0, [f'Sicherungs-Verzeichnis fehlt: {backup_dir}']

        try:
            with self._operation_lock(blocking=blocking):
                self._remove_stale_partials(backup_dir)

                filesystem_files = {
                    entry.name: entry
                    for entry in backup_dir.iterdir()
                    if entry.is_file() and entry.name.endswith('.tar.gz')
                }

                db_records = db.session.execute(select(BackupRecord)).scalars().all()
                db_records_by_name = {Path(rec.file_path).name: rec for rec in db_records}

                # An empty directory with existing records signals an unmounted
                # volume; dropping the records would lose created_by_id, which
                # no manifest can restore.
                if not filesystem_files and db_records_by_name:
                    return 0, 0, 0, [
                        f'Sicherungs-Verzeichnis {backup_dir} ist leer, es sind aber '
                        f'{len(db_records_by_name)} Sicherungen registriert. Abgleich '
                        f'abgebrochen, damit kein Datenbestand verworfen wird. '
                        f'Einbindung des Verzeichnisses prüfen.'
                    ]

                for filename, archive in filesystem_files.items():
                    if filename in db_records_by_name:
                        continue
                    name_match = self._ARCHIVE_NAME_PATTERN.fullmatch(filename)
                    if name_match is None:
                        errors.append(f'{filename}: Dateiname entspricht keinem Sicherungsarchiv')
                        continue
                    manifest, error = self._validate_archive(archive)
                    if error is not None:
                        errors.append(f'{filename}: {error}')
                        continue
                    try:
                        created_at, description = self._metadata_from_manifest(manifest)
                        record = self.register_archive(
                            archive,
                            backup_type=name_match.group(1),
                            description=description,
                            created_at=created_at
                        )
                        if record is None:
                            errors.append(f'{filename}: Registrierung fehlgeschlagen')
                            continue
                        added += 1
                    except Exception as e:
                        db.session.rollback()
                        errors.append(f'{filename}: {e}')

                for filename, record in db_records_by_name.items():
                    if filename in filesystem_files:
                        actual_path = str(filesystem_files[filename])
                        if record.file_path != actual_path:
                            try:
                                record.file_path = actual_path
                                record.status = BackupStatus.CREATED
                                record.verified_at = None
                                record.verification_error = None
                                db.session.commit()
                                updated += 1
                            except Exception as e:
                                db.session.rollback()
                                errors.append(f'{filename}: Pfad-Aktualisierung fehlgeschlagen: {e}')
                        continue
                    try:
                        db.session.delete(record)
                        db.session.commit()
                        removed += 1
                    except Exception as e:
                        db.session.rollback()
                        errors.append(f'#{record.id}: {e}')
        except BlockingIOError:
            return 0, 0, 0, ['Andere Backup-Operation läuft, Sync übersprungen']

        if errors and self.app:
            for err in errors:
                self.app.logger.warning(f"Backup sync: {err}")

        return added, updated, removed, errors

    def _remove_stale_partials(self, backup_dir: Path) -> None:
        """Delete partial archives left behind by a process that died mid-write.

        Must run under ``_operation_lock``: every writer holds it, so a
        partial file present while the lock is held has no live writer.
        """
        for entry in backup_dir.iterdir():
            if not self._PARTIAL_NAME_PATTERN.fullmatch(entry.name):
                continue
            if entry.is_symlink() or not entry.is_file():
                continue
            if self.safe_archive_path(str(entry)) is None:
                continue
            try:
                entry.unlink()
            except OSError as exc:
                if self.app:
                    self.app.logger.error(f"Failed to remove stale partial archive {entry}: {exc}")
                continue
            if self.app:
                self.app.logger.warning(f"Removed stale partial archive {entry}")

    @staticmethod
    def _metadata_from_manifest(manifest: dict) -> Tuple[datetime, Optional[str]]:
        """Derive creation time and description from a manifest.

        The manifest must have passed ``_validate_archive``, which
        guarantees a parseable ``created_at``.

        Returns:
            Tuple of (created_at as naive UTC, description or None).
        """
        created_at = _parse_manifest_timestamp(manifest['created_at'])

        raw_description = manifest.get('description')
        description: Optional[str] = None
        if isinstance(raw_description, str) and raw_description.strip():
            description = raw_description

        return created_at, description

    def register_archive(self, archive_path: Path,
                         backup_type: str = 'manual',
                         description: Optional[str] = None,
                         created_by_id: Optional[int] = None,
                         created_at: Optional[datetime] = None) -> Optional[object]:
        """Register an existing archive file as a BackupRecord.

        Args:
            archive_path: Existing tar.gz archive to register.
            backup_type: One of 'manual', 'scheduled', 'pre_restore'.
            description: Optional human-readable note.
            created_by_id: User ID for audit trail (None for system tasks).
            created_at: Original creation timestamp (UTC, naive). Defaults to
                the model column default (current UTC) when None.

        Returns:
            BackupRecord on success, None on failure.
        """
        from modules.backup.models import BackupRecord, BackupStatus, BackupType
        from core.db import db

        try:
            if not archive_path.exists():
                return None

            record = BackupRecord(
                backup_type=BackupType(backup_type),
                file_path=str(archive_path),
                file_size=archive_path.stat().st_size,
                checksum=_checksum(archive_path),
                status=BackupStatus.CREATED,
                description=description,
                created_by_id=created_by_id
            )
            if created_at is not None:
                record.created_at = created_at
            db.session.add(record)
            db.session.commit()
            return record

        except Exception as e:
            if self.app:
                self.app.logger.error(f"Archive registration failed: {e}")
            return None

    def _create_pre_restore_archive(self) -> Optional[Path]:
        """Snapshot the live DB into a tar.gz archive without DB record.

        A record would be wiped by the restore, so the archive is registered
        only after the restore completes.

        Returns:
            Path to the archive on success, None on failure.
        """
        archive_path = self._backup_dir() / self._archive_name('pre_restore')
        try:
            with tempfile.TemporaryDirectory() as tmp:
                db_snapshot = Path(tmp) / 'fbk-time.db'
                self._snapshot_db(db_snapshot)
                self._create_archive(
                    archive_path,
                    db_snapshot,
                    description='Automatischer Snapshot vor Wiederherstellung'
                )
            return archive_path
        except Exception as e:
            self._remove_orphan_archive(archive_path)
            if self.app:
                self.app.logger.error(f"Pre-restore archive failed: {e}")
            return None

    def restore_from_archive(self, archive_path: Path,
                              pre_restore: bool = True,
                              allow_version_mismatch: bool = False) -> Tuple[bool, str]:
        """Restore database and configuration from a backup archive.

        CLI only, with the service stopped; the operation lock still guards
        against a worker left running. An archive of another application
        version is refused unless allowed, since its schema need not match.

        Args:
            archive_path: Path to the tar.gz archive.
            pre_restore: Whether to create a pre-restore backup first.
            allow_version_mismatch: Restore even if the manifest records a
                different application version.

        Returns:
            Tuple of (success, message).
        """
        from core.db import db

        if not archive_path.exists():
            return False, f'Archiv nicht gefunden: {archive_path}'

        with self._operation_lock():
            manifest, validation_error = self._validate_archive(archive_path)
            if validation_error is not None:
                return False, f'Validierung fehlgeschlagen: {validation_error}'

            if manifest['app_version'] != APP_VERSION and not allow_version_mismatch:
                return False, version_mismatch_message(manifest['app_version'])

            pre_restore_archive: Optional[Path] = None
            db_swapped = False

            if pre_restore and self._db_path and self._db_path.exists():
                pre_restore_archive = self._create_pre_restore_archive()
                if pre_restore_archive is None:
                    return False, 'Snapshot vor Wiederherstellung fehlgeschlagen'

            try:
                # Release the session's checked-out connection first:
                # dispose() only closes pooled connections, and the open
                # transaction would block the WAL checkpoint below.
                db.session.remove()
                db.engine.dispose()
                _remove_wal_files(self._db_path)

                with tarfile.open(archive_path, 'r:gz') as tar, \
                     tempfile.TemporaryDirectory() as tmp:
                    tmp_path = Path(tmp)

                    self._db_path.parent.mkdir(parents=True, exist_ok=True)

                    # Extract every entry before swapping any live file so an
                    # extraction error cannot leave the DB restored while
                    # settings.json/.env are still the old ones.
                    for entry in self._required_entries():
                        tar.extract(tar.getmember(entry), path=str(tmp_path), filter='data')

                    swaps = (
                        (tmp_path / self._ENTRY_DB, self._db_path, True),
                        (tmp_path / self._ENTRY_SETTINGS, self._app_root / 'settings.json', False),
                        (tmp_path / self._ENTRY_ENV, self._app_root / '.env', True),
                    )

                    # Stage current live files so a mid-swap failure rolls the
                    # already-replaced targets back to their pre-restore state.
                    rollback = {}
                    for _, target, restrict in swaps:
                        if target.exists():
                            staged = tmp_path / (target.name + '.rollback')
                            shutil.copy2(str(target), str(staged))
                            rollback[target] = (staged, restrict)

                    replaced = []
                    try:
                        for src, target, restrict in swaps:
                            _atomic_replace(src, target, restrict=restrict)
                            replaced.append(target)
                            if target == self._db_path:
                                db_swapped = True
                    except Exception as swap_error:
                        rollback_errors = self._roll_back_swaps(replaced, rollback)
                        if rollback_errors:
                            raise RuntimeError(
                                f'{swap_error}; Zurücksetzen fehlgeschlagen für '
                                f'{"; ".join(rollback_errors)}'
                            ) from swap_error
                        raise

                warning = ''
                if pre_restore_archive is not None:
                    registered = self.register_archive(
                        pre_restore_archive,
                        backup_type='pre_restore',
                        description='Automatischer Snapshot vor Wiederherstellung'
                    )
                    if registered is None:
                        if self.app:
                            self.app.logger.warning(
                                f"Pre-restore archive at {pre_restore_archive} could "
                                "not be registered in the restored database. The "
                                "archive file is intact and will be re-registered "
                                "automatically by the next backup directory sync."
                            )
                        warning = (
                            f' Hinweis: Snapshot konnte nicht registriert '
                            f'werden, Datei liegt unter {pre_restore_archive} '
                            f'und wird beim nächsten Sync automatisch '
                            f'wiedererkannt.'
                        )

                return True, 'Datenbank, settings.json und .env wiederhergestellt.' + warning

            except Exception as e:
                return False, f'Restore fehlgeschlagen: {e}'

            finally:
                # Before the DB swap the snapshot is an orphan; after it, the
                # only recovery path.
                if pre_restore_archive is not None and not db_swapped:
                    self._remove_orphan_archive(pre_restore_archive)

    def _roll_back_swaps(self, replaced: list, rollback: dict) -> list:
        """Return already-replaced restore targets to their pre-restore state.

        A target without a staged copy did not exist before the restore and
        is removed again.

        Args:
            replaced: Targets swapped so far, in swap order.
            rollback: Target -> (staged copy, restrict flag).

        Returns:
            One message per target that could not be rolled back.
        """
        errors = []
        for target in reversed(replaced):
            staged, restrict = rollback.get(target, (None, False))
            try:
                if staged is None:
                    target.unlink(missing_ok=True)
                else:
                    _atomic_replace(staged, target, restrict=restrict)
            except Exception as exc:
                errors.append(f'{target}: {exc}')
        for message in errors:
            if self.app:
                self.app.logger.error(f"Restore rollback failed: {message}")
        return errors


def _remove_wal_files(db_path: Path) -> None:
    """Checkpoint pending WAL frames into the DB, then remove the sidecars.

    A stale sidecar would replay old frames onto the restored file. A busy
    checkpoint returns busy=1 instead of raising, so it aborts before unlink.
    """
    if db_path.exists():
        conn = sqlite3.connect(str(db_path), timeout=30)
        try:
            busy, _, _ = conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        finally:
            conn.close()
        if busy:
            raise RuntimeError(
                'WAL checkpoint blocked by an active database connection; '
                'stop the service and retry the restore (aborted before WAL '
                'removal to avoid data loss).'
            )
    for suffix in ('-wal', '-shm'):
        sidecar = Path(str(db_path) + suffix)
        if sidecar.exists():
            sidecar.unlink()


def _atomic_replace(source: Path, target: Path, restrict: bool = False) -> None:
    """Replace target with source atomically, preserving source permissions.

    Stages next to the target, since ``os.replace`` cannot cross filesystems.

    Args:
        source: File to move into place.
        target: Destination path.
        restrict: Keep the staging file's 0o600 instead of the source mode
            (database, .env).
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(target.name + '.restore_tmp')
    # 0o600 before the first byte lands; copy2 would expose the data under the
    # umask during the copy. O_EXCL refuses a planted file or symlink.
    staging.unlink(missing_ok=True)
    fd = os.open(str(staging), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as destination, open(source, 'rb') as origin:
        shutil.copyfileobj(origin, destination)
    if not restrict:
        shutil.copystat(str(source), str(staging))
    os.replace(str(staging), str(target))


backup_manager = BackupManager()


def start_auto_discovery(app) -> None:
    """Trigger an asynchronous backup directory sync after app start.

    Makes archives present after a restore or copied in visible without a
    manual sync. Non-blocking, so it never starves worker requests.
    """
    def _run():
        with app.app_context():
            try:
                added, updated, removed, errors = backup_manager.sync_filesystem(
                    blocking=False
                )
                if added or updated or removed:
                    app.logger.info(
                        f"Backup auto-discovery: {added} added, "
                        f"{updated} updated, {removed} removed"
                    )
            except Exception as e:
                app.logger.warning(
                    f"Backup auto-discovery failed: {e}", exc_info=True
                )

    thread = threading.Thread(
        target=_run, name='backup-auto-discovery', daemon=True
    )
    thread.start()
