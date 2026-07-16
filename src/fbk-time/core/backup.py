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
import shutil
import sqlite3
import tarfile
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

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


def _restrict_file(path: Path) -> None:
    """Set 0o600 on a file. Best-effort on POSIX, no-op elsewhere."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


class BackupManager:
    """Manages creation, verification, and restore of application backups.

    Each backup archive contains the database, static configuration, and the
    environment file so that a full restore requires only the archive.
    Backup directory is read from app.config['BACKUP_DIR'] (settings.json).
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

    @classmethod
    def _required_entries(cls) -> Tuple[str, ...]:
        return (cls._ENTRY_DB, cls._ENTRY_SETTINGS, cls._ENTRY_ENV)

    def __init__(self, app=None):
        self.app = app
        self._db_path: Optional[Path] = None
        self._app_root: Path = Path(__file__).resolve().parent.parent

        if app is not None:
            self.init_app(app)

    def init_app(self, app) -> None:
        self.app = app
        uri: str = app.config['SQLALCHEMY_DATABASE_URI']
        self._db_path = Path(uri.replace('sqlite:///', ''))

    def _backup_dir(self) -> Path:
        return Path(self.app.config['BACKUP_DIR'])

    @contextmanager
    def _operation_lock(self, blocking: bool = True):
        """Serialize backup write operations across processes.

        Uses POSIX ``fcntl.lockf`` (advisory record lock) instead of
        ``flock`` so that the lock is bound to the process — not to the
        open file description — and is therefore **not inherited across
        ``fork``**. With ``preload_app=True`` Gunicorn forks workers
        from the master after ``start_auto_discovery`` has already
        opened the lock; with ``flock`` every worker would have kept a
        reference to the master's locked OFD, making it impossible for
        any worker to acquire the lock until the OS released the last
        reference.

        Args:
            blocking: When True (default), wait for the lock — used by
                interactive endpoints and the scheduler. When False,
                raise ``BlockingIOError`` immediately if another process
                holds the lock — used by the auto-discovery background
                thread so it never delays a worker request.
        """
        lock_path = Path(self.app.config['RUNTIME_DIR']) / 'backup-operation.lock'
        fd = open(lock_path, 'w')
        flags = fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB
        try:
            fcntl.lockf(fd, flags)
            yield
        finally:
            fd.close()

    def _safe_archive_path(self, path_str: str) -> Optional[Path]:
        """Resolve a stored archive path and ensure it stays within BACKUP_DIR.

        Returns None when the path resolves outside the backup directory or
        cannot be resolved at all. Used before any unlink operation so a
        tampered ``file_path`` cannot remove files outside ``BACKUP_DIR``.
        """
        try:
            backup_dir = self._backup_dir().resolve()
            candidate = Path(path_str).resolve()
            candidate.relative_to(backup_dir)
            return candidate
        except (ValueError, OSError):
            return None

    def _archive_name(self, backup_type: str) -> str:
        ts = _utc_now().strftime('%Y%m%d_%H%M%S')
        return f'backup_{ts}_{backup_type}.tar.gz'

    def _remove_orphan_archive(self, archive_path: Path) -> None:
        """Delete a partial archive left behind by a failed creation step.

        Called from the error path of ``create_backup`` and
        ``_create_pre_restore_archive`` so a half-written ``.tar.gz`` does
        not stay on disk without a matching record.
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

        sqlite3.backup() reads committed WAL frames and produces a clean,
        WAL-free database file — safe while the application is running. A
        busy timeout lets the snapshot wait for concurrent writers to
        release their locks instead of failing immediately with
        SQLITE_BUSY under WAL load.
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

        The description is embedded in the manifest so it survives a DB
        wipe and can be recovered by ``sync_filesystem`` after a restore.
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

        # Open with 0o600 up front so the archive is never briefly world-
        # readable during writing (it carries .env/SECRET_KEY and hashes).
        fd = os.open(str(archive_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as raw, \
                tarfile.open(fileobj=raw, mode='w:gz', compresslevel=6) as tar:
            tar.add(str(db_snapshot), arcname=self._ENTRY_DB)
            tar.add(str(settings_path), arcname=self._ENTRY_SETTINGS)
            tar.add(str(env_path), arcname=self._ENTRY_ENV)

            manifest = {
                'schema_version': '1.0',
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

    def _validate_archive(
        self, archive_path: Path
    ) -> Tuple[Optional[dict], Optional[str]]:
        """Strictly validate a tar.gz as a genuine application backup.

        Rejects any archive whose manifest is missing, malformed, declares
        a different schema version, omits a required entry, lists a
        checksum that does not match the actual archive content, or whose
        description exceeds the configured cap. Used by sync_filesystem
        before registering filesystem-discovered archives so a foreign
        tar.gz dropped into the backup directory cannot become a
        restorable BackupRecord. Also used by restore_from_archive to
        consolidate manifest + checksum verification in one place.

        The SHA-256 checksums establish integrity only — they detect
        accidental corruption, not deliberate tampering. An attacker with
        write access to the archive can recompute both the content and the
        manifest checksum, so this is not an authenticity guarantee. Adding
        an HMAC is deliberately avoided because it would invalidate every
        existing backup archive.

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

        if not isinstance(manifest.get('created_at'), str):
            return None, 'Manifest-Feld created_at fehlt oder ungültig'

        if not isinstance(manifest.get('app_version'), str):
            return None, 'Manifest-Feld app_version fehlt oder ungültig'

        description = manifest.get('description')
        if description is not None and not isinstance(description, str):
            return None, 'Manifest-Feld description hat unerwarteten Typ'
        if isinstance(description, str) and len(description) > self._MAX_DESCRIPTION_LEN:
            return None, 'Manifest-Feld description überschreitet Längenlimit'

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
                    cap = (self._MAX_DB_ENTRY_BYTES if entry == self._ENTRY_DB
                           else self._MAX_CONFIG_ENTRY_BYTES)
                    if member.size > cap:
                        return None, f'Archiveintrag zu groß: {entry}'
                    f = tar.extractfile(member)
                    if not f:
                        return None, f'Archiveintrag nicht lesbar: {entry}'
                    # Read in fixed-size blocks instead of a single f.read()
                    # so a maliciously crafted archive entry cannot exhaust
                    # memory (decompression bomb) during validation.
                    h = hashlib.sha256()
                    for chunk in iter(lambda: f.read(65536), b''):
                        h.update(chunk)
                    actual = f'sha256:{h.hexdigest()}'
                    if actual != expected_cs:
                        return None, f'Prüfsumme abweichend: {entry}'
        except (tarfile.TarError, OSError) as e:
            return None, f'Archiv nicht lesbar: {e}'

        return manifest, None

    def create_backup(self, description: Optional[str] = None,
                      backup_type: str = 'manual',
                      created_by_id: Optional[int] = None) -> Optional[object]:
        """Create a compressed backup archive.

        Each created archive is verified immediately so the caller knows
        the backup is restorable before the call returns.

        Args:
            description: Optional human-readable note.
            backup_type: One of 'manual', 'scheduled', 'pre_restore'.
            created_by_id: User ID for audit trail (None for system tasks).

        Returns:
            BackupRecord instance on success, None on failure.
        """
        from modules.backup.models import BackupRecord, BackupStatus, BackupType
        from core.extensions import db

        backup_dir = self._backup_dir()
        archive_path = backup_dir / self._archive_name(backup_type)

        try:
            with self._operation_lock():
                with tempfile.TemporaryDirectory() as tmp:
                    db_snapshot = Path(tmp) / 'fbk-time.db'
                    self._snapshot_db(db_snapshot)
                    self._create_archive(archive_path, db_snapshot, description=description)

                _restrict_file(archive_path)
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

                self._verify_record(record)

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
        from core.extensions import db

        record = db.session.get(BackupRecord, record_id)
        if not record:
            return False, 'Sicherung nicht gefunden'

        return self._verify_record(record)

    def _verify_record(self, record) -> Tuple[bool, Optional[str]]:
        from modules.backup.models import BackupStatus
        from core.extensions import db

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
        from core.extensions import db

        records = db.session.execute(db.select(BackupRecord)).scalars().all()
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

        Keeps the newest ``backup_retention_count`` archives and removes
        the rest. Sorting is by creation timestamp descending, so the
        most recent backups always survive.

        Returns:
            Number of backups removed.
        """
        from modules.backup.models import BackupRecord
        from core.extensions import db
        from core.settings_manager import settings_manager

        keep_count = max(1, int(settings_manager.get('backup_retention_count')))

        with self._operation_lock():
            all_records = db.session.execute(
                db.select(BackupRecord).order_by(BackupRecord.created_at.desc())
            ).scalars().all()

            removed = 0
            for record in all_records[keep_count:]:
                try:
                    path = self._safe_archive_path(record.file_path)
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
        from core.extensions import db

        with self._operation_lock():
            record = db.session.get(BackupRecord, record_id)
            if not record:
                return False, 'Sicherung nicht gefunden'

            try:
                path = self._safe_archive_path(record.file_path)
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

        Compares the contents of ``BACKUP_DIR`` with the database and
        repairs three forms of drift:

        * Archive on disk without record  → register with status CREATED.
        * Record with archive at a different path but matching filename
          → update ``file_path`` to the current location (covers the
          case where a database backup was restored on a host with a
          different ``BACKUP_DIR``).
        * Record without archive on disk  → delete record.

        Sync is intentionally **integrity-agnostic**: it never reads
        archive contents and never recomputes checksums. Verification
        is a separate concern triggered by the per-record
        "Verifizieren" action so that sync stays a fast O(filename)
        operation even with hundreds of archives — well under the
        gunicorn worker timeout. Newly registered records show status
        ``CREATED`` until the operator verifies them.

        The match key is the archive filename; archive timestamps are
        unique, so two records cannot collide on the same file.

        Args:
            blocking: When True (default), wait for the operation lock.
                The auto-discovery thread passes False so it skips
                silently if a user-triggered sync, backup, or delete is
                already in progress.

        Returns:
            Tuple of (added, updated, removed, errors). When the lock is
            unavailable in non-blocking mode all counters are zero and a
            single explanatory entry is appended to errors.
        """
        from modules.backup.models import BackupRecord, BackupStatus
        from core.extensions import db

        backup_dir = self._backup_dir()
        errors: list = []
        added = 0
        updated = 0
        removed = 0

        if not backup_dir.exists():
            return 0, 0, 0, [f'Sicherungs-Verzeichnis fehlt: {backup_dir}']

        try:
            with self._operation_lock(blocking=blocking):
                filesystem_files = {
                    entry.name: entry
                    for entry in backup_dir.iterdir()
                    if entry.is_file() and entry.name.endswith('.tar.gz')
                }

                db_records = db.session.execute(db.select(BackupRecord)).scalars().all()
                db_records_by_name = {Path(rec.file_path).name: rec for rec in db_records}

                for filename, archive in filesystem_files.items():
                    if filename in db_records_by_name:
                        continue
                    manifest, error = self._validate_archive(archive)
                    if error is not None:
                        errors.append(f'{filename}: {error}')
                        continue
                    try:
                        backup_type, created_at, description = self._metadata_from_manifest(
                            archive.name, manifest
                        )
                        record = self.register_archive(
                            archive,
                            backup_type=backup_type.value,
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

    def _metadata_from_manifest(
        self, archive_name: str, manifest: dict
    ) -> Tuple['BackupType', Optional[datetime], Optional[str]]:
        """Derive backup type, creation time, and description from a manifest.

        Filename pattern ``backup_YYYYMMDD_HHMMSS_<type>.tar.gz`` provides
        the backup type. Manifest provides created_at and description.
        Manifest is assumed to be validated by ``_validate_archive``.
        """
        from modules.backup.models import BackupType

        backup_type = BackupType.MANUAL
        parts = archive_name[:-len('.tar.gz')].split('_')
        if len(parts) >= 4 and parts[0] == 'backup':
            try:
                backup_type = BackupType(parts[3])
            except ValueError:
                pass

        created_at: Optional[datetime] = None
        iso = manifest['created_at']
        if iso.endswith('Z'):
            iso = iso[:-1] + '+00:00'
        try:
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            created_at = dt
        except ValueError:
            pass

        raw_description = manifest.get('description')
        description: Optional[str] = None
        if isinstance(raw_description, str) and raw_description.strip():
            description = raw_description

        return backup_type, created_at, description

    def register_archive(self, archive_path: Path,
                         backup_type: str = 'manual',
                         description: Optional[str] = None,
                         created_by_id: Optional[int] = None,
                         created_at: Optional[datetime] = None) -> Optional[object]:
        """Register an existing archive file as a BackupRecord.

        Used after restore to re-attach the pre-restore safety archive whose
        original record was wiped when the live DB was overwritten, and by
        ``sync_filesystem`` to register archives discovered in the backup
        directory.

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
        from core.extensions import db

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

        Used during restore: the DB is about to be overwritten, so writing a
        BackupRecord first would be wiped. The archive lives on disk and is
        re-registered after the restore completes.

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
            _restrict_file(archive_path)
            return archive_path
        except Exception as e:
            self._remove_orphan_archive(archive_path)
            if self.app:
                self.app.logger.error(f"Pre-restore archive failed: {e}")
            return None

    def restore_from_archive(self, archive_path: Path,
                              pre_restore: bool = True) -> Tuple[bool, str]:
        """Restore database and configuration from a backup archive.

        Intended for CLI use only — the application service should be
        stopped before calling this method. The full body runs under
        ``_operation_lock`` so that, even if a worker is mistakenly left
        running, no concurrent ``create_backup``/``sync_filesystem``/
        ``cleanup_old_backups``/``delete_backup`` can interleave with the
        pre-restore snapshot or the file swap.

        Restores in order:
            1. database/fbk-time.db  → original DB path (WAL files removed first)
            2. config/settings.json  → app root/settings.json
            3. config/.env           → app root/.env

        All three entries must be present in the archive. Checksums are verified
        before any file is written to disk. The SQLAlchemy engine is disposed
        before the DB file is replaced so no stale handles survive the swap.

        Args:
            archive_path: Path to the tar.gz archive.
            pre_restore: Whether to create a pre-restore backup first.

        Returns:
            Tuple of (success, message).
        """
        from core.extensions import db

        if not archive_path.exists():
            return False, f'Archiv nicht gefunden: {archive_path}'

        with self._operation_lock():
            manifest, validation_error = self._validate_archive(archive_path)
            if validation_error is not None:
                return False, f'Validierung fehlgeschlagen: {validation_error}'

            pre_restore_archive: Optional[Path] = None
            db_swapped = False

            if pre_restore and self._db_path and self._db_path.exists():
                pre_restore_archive = self._create_pre_restore_archive()
                if pre_restore_archive is None:
                    return False, 'Snapshot vor Wiederherstellung fehlgeschlagen'

            try:
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
                    except Exception:
                        for target in reversed(replaced):
                            staged, restrict = rollback.get(target, (None, False))
                            if staged is not None:
                                try:
                                    _atomic_replace(staged, target, restrict=restrict)
                                except Exception:
                                    pass
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
                # Pre-DB-swap failure: snapshot has no purpose (live DB is
                # untouched) and must not linger as an orphan archive.
                # Post-DB-swap failure: snapshot is the only recovery path
                # and is retained.
                if pre_restore_archive is not None and not db_swapped:
                    self._remove_orphan_archive(pre_restore_archive)


def _remove_wal_files(db_path: Path) -> None:
    """Checkpoint pending WAL frames into the DB, then remove the sidecars.

    The restore swaps the .db wholesale, so a stale WAL/SHM sidecar would
    replay old frames onto the restored file and corrupt it. A TRUNCATE
    checkpoint folds committed frames in first. wal_checkpoint does NOT raise
    on a busy reader — it returns busy=1 and folds nothing, so a busy result
    aborts before any unlink instead of dropping those frames. Non-WAL
    databases return busy=0 (no-op).
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

    Stages the file as ``<target>.restore_tmp`` in the target's directory so
    that the final ``os.replace`` happens within a single filesystem (a
    cross-device rename would raise ``OSError``). On both POSIX and Windows
    ``os.replace`` is atomic — if the process is killed before this call,
    the previous target file remains intact.

    Args:
        source: File to move into place.
        target: Destination path.
        restrict: When True, tighten permissions to 0o600 after the swap.
            Used for sensitive targets (database, .env with SECRET_KEY) so a
            restored file never inherits world-readable permissions.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(target.name + '.restore_tmp')
    shutil.copy2(str(source), str(staging))
    # Tighten before the swap so the live target is never briefly readable
    # with the archived member's (possibly 0o644) mode.
    if restrict:
        _restrict_file(staging)
    os.replace(str(staging), str(target))


backup_manager = BackupManager()


def start_auto_discovery(app) -> None:
    """Trigger an asynchronous backup directory sync after app start.

    Reconciles BackupRecord rows with the archive files on disk so that
    backups present after a database restore (or files copied in
    out-of-band) become visible without requiring a manual sync click.
    Runs in a daemon thread so the application start is not delayed.

    The thread acquires the operation lock **non-blocking** — if a
    worker is already mid-operation (manual sync, delete, scheduled
    backup), auto-discovery skips silently rather than holding the lock
    and starving worker requests until the gunicorn timeout kills them.
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
