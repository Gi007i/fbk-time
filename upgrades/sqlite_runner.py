"""Bundled SQLite binary resolver and CLI-backed connection wrapper.

Upgrade scripts that require SQLite features unavailable in the host
system (e.g. ALTER TABLE DROP COLUMN, introduced in 3.35) can use this
module instead of importing sqlite3 directly. It auto-detects the host
platform, locates the matching static binary from the ``bin/`` tree,
and exposes a connection object that is API-compatible with the subset
of sqlite3.Connection used by the upgrade scripts.

Usage::

    import sqlite_runner

    binary = sqlite_runner.resolve_binary(
        required=(3, 35, 0), logger=logger
    )
    conn = sqlite_runner.connect(db_path, binary=binary)
    conn.execute('ALTER TABLE t DROP COLUMN old_col')
    conn.close()

When the system SQLite already satisfies the version requirement,
``resolve_binary`` returns *None* and ``connect`` falls through to
the native ``sqlite3.connect``.
"""

from __future__ import annotations

import math
import os
import platform
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

BIN_DIR = Path(__file__).resolve().parent / 'bin'

_PLATFORM_MAP = {
    ('linux', 'x86_64'): 'linux-x86_64',
    ('linux', 'amd64'): 'linux-x86_64',
    ('linux', 'aarch64'): 'linux-aarch64',
    ('linux', 'arm64'): 'linux-aarch64',
}

_STDERR_SETTLE_SECONDS = 0.01


# --- Platform detection ---------------------------------------------------

def detect_platform() -> str | None:
    """Return the platform directory name or *None* if unsupported."""
    key = (platform.system().lower(), platform.machine().lower())
    return _PLATFORM_MAP.get(key)


def find_bundled_binary() -> Path | None:
    """Locate the bundled sqlite3 binary for the current platform."""
    plat = detect_platform()
    if not plat:
        return None
    binary = BIN_DIR / plat / 'sqlite3'
    if binary.exists():
        return binary
    return None


# --- Version helpers -------------------------------------------------------

def get_system_version() -> tuple[int, ...]:
    """Return the SQLite version tuple from Python's built-in module."""
    return sqlite3.sqlite_version_info


def get_binary_version(binary: Path) -> tuple[int, ...] | None:
    """Return the SQLite version tuple reported by an external binary."""
    try:
        result = subprocess.run(
            [str(binary), ':memory:', 'SELECT sqlite_version();'],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return tuple(int(x) for x in result.stdout.strip().split('.'))
    except ValueError:
        return None


def _format_version(v: tuple[int, ...]) -> str:
    """Format a version tuple as a dotted string."""
    if not v:
        return 'unknown'
    return '.'.join(map(str, v))


# --- Binary resolution -----------------------------------------------------

def resolve_binary(
    required: tuple[int, ...],
    user_override: str | None = None,
    force: bool = False,
    logger: Any = None,
) -> Path | None:
    """Determine which SQLite binary to use for the upgrade.

    Returns:
        *None* when the system sqlite3 already satisfies *required*.
        A ``Path`` to a suitable binary otherwise.

    Exits the process when no suitable binary can be found.
    """
    system_ver = get_system_version()
    req_str = _format_version(required)

    if system_ver >= required:
        if logger:
            logger.success(
                f'System SQLite {_format_version(system_ver)} '
                f'(>= {req_str})'
            )
        return None

    if logger:
        logger.warning(
            f'System SQLite {_format_version(system_ver)} < {req_str}'
        )

    if user_override:
        return _validate_binary(Path(user_override), required, logger)

    bundled = find_bundled_binary()
    if bundled:
        bin_ver = get_binary_version(bundled)
        ver_label = _format_version(bin_ver) if bin_ver else '?'

        if logger:
            logger.info(f'Bundled binary: {bundled} ({ver_label})')

        if not force:
            answer = input(
                'Use this binary? [Y/path]: '
            ).strip()
            if answer and answer.lower() != 'y':
                return _validate_binary(Path(answer), required, logger)

        return _validate_binary(bundled, required, logger)

    if logger:
        logger.error('No suitable SQLite binary found')
        logger.info('Provide one with --sqlite-binary /path/to/sqlite3')
    sys.exit(1)


def _validate_binary(
    binary: Path,
    required: tuple[int, ...],
    logger: Any,
) -> Path:
    """Verify that *binary* exists, is executable, and meets the version."""
    binary = binary.resolve()
    if not binary.exists():
        if logger:
            logger.error(f'Binary not found: {binary}')
        sys.exit(1)

    if not os.access(binary, os.X_OK):
        if logger:
            logger.error(f'Binary is not executable: {binary}')
            logger.info(f'Run: chmod +x {binary}')
        sys.exit(1)

    bin_ver = get_binary_version(binary)
    if not bin_ver or bin_ver < required:
        actual = _format_version(bin_ver) if bin_ver else 'unknown'
        if logger:
            logger.error(
                f'Binary version {actual} < {_format_version(required)}'
            )
        sys.exit(1)

    if logger:
        logger.success(
            f'Using binary: {binary} ({_format_version(bin_ver)})'
        )
    return binary


# --- Path validation -------------------------------------------------------

def _validate_path_for_cli(path: str) -> None:
    """Reject paths that contain characters unsafe for CLI dot-commands.

    The sqlite3 CLI interprets newlines as command separators. A path
    containing ``\\n`` would cause the remainder to be executed as a
    new dot-command or SQL statement.
    """
    if '\n' in path or '\r' in path or '\x00' in path or ';' in path:
        raise ValueError(
            f'Path contains invalid characters '
            f'(newline, NUL or semicolon): {path!r}'
        )


# --- CLI-backed connection -------------------------------------------------

class CLICursor:
    """Minimal cursor returned by :meth:`CLIConnection.execute`.

    All cell values are returned as strings or ``None``. Callers must
    cast explicitly (e.g. ``int(row[0])``) when numeric types are
    expected — unlike ``sqlite3.Cursor`` which returns native types.
    """

    def __init__(
        self,
        rows: Sequence[tuple[str | None, ...]] | None = None,
        rowcount: int = -1,
    ):
        self._rows = list(rows) if rows else []
        self.rowcount = rowcount

    def fetchone(self) -> tuple[str | None, ...] | None:
        """Return the first row or *None* if the result set is empty.

        Always returns the same row on repeated calls — there is no
        internal cursor position. This matches the upgrade scripts'
        usage pattern of single-row results (COUNT, PRAGMA).
        """
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[str | None, ...]]:
        """Return all rows as a list of tuples."""
        return self._rows


class CLIConnection:
    """``sqlite3.Connection``-compatible wrapper backed by a CLI process.

    Starts a persistent ``sqlite3`` process and communicates via
    stdin/stdout with sentinel markers to delimit command output.
    stderr is drained by a background thread and checked after each
    command for error messages from the SQLite engine.

    The sentinel and null marker are randomized per instance to
    prevent database content from accidentally matching them.

    Not thread-safe — designed for single-threaded, operator-supervised
    upgrade scripts. An I/O lock guards against accidental concurrent
    use.
    """

    def __init__(self, binary: Path, db_path: Path):
        self._sentinel = f'__SQLITE_RUNNER_{secrets.token_hex(8)}__'
        self._null_marker = f'__NULL_{secrets.token_hex(4)}__'
        self._closed = False
        self._io_lock = threading.Lock()
        self._proc = subprocess.Popen(
            [str(binary), str(db_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._stderr_lines: list[str] = []
        self._stderr_lock = threading.Lock()
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True
        )
        self._stderr_thread.start()

        self._raw('.headers off')
        # C-style escape interpreted by sqlite3 CLI .separator command
        self._raw('.separator "\\x1f"')
        self._raw(f'.nullvalue {self._null_marker}')

    # --- internal helpers --------------------------------------------------

    def _drain_stderr(self) -> None:
        """Continuously read stderr in a background thread."""
        for line in self._proc.stderr:
            with self._stderr_lock:
                self._stderr_lines.append(line.rstrip('\r\n'))

    def _collect_stderr(self) -> list[str]:
        """Return and clear accumulated stderr lines."""
        with self._stderr_lock:
            lines = self._stderr_lines[:]
            self._stderr_lines.clear()
        return lines

    def _raw(self, cmd: str) -> list[str]:
        """Send a command and collect output lines until the sentinel.

        Only called with hardcoded SQL or dot-commands from the upgrade
        scripts. Never receives external user input.

        After the sentinel arrives on stdout, a short settle period
        (``_STDERR_SETTLE_SECONDS``) lets the stderr drain thread
        catch up. This is a best-effort heuristic — there is no
        reliable cross-pipe synchronisation primitive short of
        terminating the process. In practice the settle time is
        sufficient because the sqlite3 process writes stderr before
        advancing to the next command on stdin.
        """
        if self._closed:
            raise sqlite3.ProgrammingError(
                'Cannot operate on a closed connection'
            )

        with self._io_lock:
            self._proc.stdin.write(f'{cmd}\n')
            self._proc.stdin.write(f'.print {self._sentinel}\n')
            self._proc.stdin.flush()

            lines: list[str] = []
            while True:
                line = self._proc.stdout.readline()
                if not line:
                    stderr = self._collect_stderr()
                    raise sqlite3.OperationalError(
                        'SQLite process terminated unexpectedly. '
                        f'stderr: {stderr}'
                    )
                line = line.rstrip('\r\n')
                if line == self._sentinel:
                    break
                lines.append(line)

            time.sleep(_STDERR_SETTLE_SECONDS)
            stderr = self._collect_stderr()
            if stderr:
                raise sqlite3.OperationalError('\n'.join(stderr))

            return lines

    @staticmethod
    def _quote(value: Any) -> str:
        """Quote a Python value for safe SQL embedding.

        This is intentionally not using parameterized queries because the
        sqlite3 CLI does not support prepared statements. Only called
        with trusted, program-internal values — never with external
        user input.
        """
        if value is None:
            return 'NULL'
        if isinstance(value, bool):
            return '1' if value else '0'
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError('Non-finite float values are not supported')
            return repr(value)
        if isinstance(value, bytes):
            raise TypeError(
                'bytes values are not supported; decode to str first'
            )
        text = str(value)
        if '\x00' in text:
            raise ValueError('NUL byte in SQL parameter value')
        escaped = text.replace("'", "''")
        return f"'{escaped}'"

    def _bind(self, sql: str, params: Sequence[Any]) -> str:
        """Substitute ``?`` placeholders with quoted values."""
        parts: list[str] = []
        idx = 0
        in_str = False
        in_dq = False
        i = 0
        while i < len(sql):
            ch = sql[i]
            if ch == '"' and not in_str:
                if in_dq and i + 1 < len(sql) and sql[i + 1] == '"':
                    parts.append('""')
                    i += 2
                    continue
                in_dq = not in_dq
                parts.append(ch)
            elif ch == "'" and not in_dq and not in_str:
                in_str = True
                parts.append(ch)
            elif ch == "'" and in_str:
                if i + 1 < len(sql) and sql[i + 1] == "'":
                    parts.append("''")
                    i += 2
                    continue
                in_str = False
                parts.append(ch)
            elif ch == '?' and not in_str and not in_dq:
                if idx >= len(params):
                    raise sqlite3.ProgrammingError(
                        f'Not enough parameters (expected > {idx})'
                    )
                parts.append(self._quote(params[idx]))
                idx += 1
            else:
                parts.append(ch)
            i += 1

        if idx < len(params):
            raise sqlite3.ProgrammingError(
                f'Too many parameters (got {len(params)}, used {idx})'
            )

        return ''.join(parts)

    # --- public interface (sqlite3.Connection subset) ----------------------

    def execute(
        self, sql: str, params: Sequence[Any] | None = None
    ) -> CLICursor:
        """Execute a single SQL statement and return a cursor."""
        if sql.lstrip().startswith('.'):
            raise sqlite3.ProgrammingError(
                'Dot-commands are not allowed via execute()'
            )
        if params is not None:
            sql = self._bind(sql, params)

        lines = self._raw(sql)

        rows: list[tuple[str | None, ...]] = []
        for line in lines:
            if line:
                cells = tuple(
                    None if v == self._null_marker else v
                    for v in line.split('\x1f')
                )
                rows.append(cells)

        rowcount = -1
        stripped = sql.strip().split()[0].upper() if sql.strip() else ''
        if stripped in ('INSERT', 'UPDATE', 'DELETE', 'REPLACE'):
            changes = self._raw('SELECT changes();')
            if changes and changes[0]:
                try:
                    rowcount = int(changes[0])
                except ValueError:
                    pass

        return CLICursor(rows, rowcount)

    def close(self) -> None:
        """Shut down the CLI process and join the stderr thread."""
        if self._closed:
            return
        self._closed = True

        if self._proc and self._proc.poll() is None:
            try:
                self._proc.stdin.write('.quit\n')
                self._proc.stdin.flush()
                self._proc.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self._proc.kill()
                    self._proc.wait(timeout=5)
                except OSError:
                    pass

        self._stderr_thread.join(timeout=5)

    def __enter__(self) -> CLIConnection:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# --- Factory ---------------------------------------------------------------

def connect(
    db_path: Path,
    binary: Path | None = None,
) -> sqlite3.Connection | CLIConnection:
    """Open a database connection.

    When *binary* is ``None``, delegates to ``sqlite3.connect``.
    Otherwise wraps the given CLI binary in a :class:`CLIConnection`.
    """
    if binary is None:
        return sqlite3.connect(str(db_path))
    return CLIConnection(binary, db_path)


# --- Backup / Restore helpers ---------------------------------------------

def create_backup(
    source_path: Path,
    dest_path: Path,
    binary: Path | None = None,
) -> None:
    """Create a transaction-consistent database backup.

    Uses the SQLite online backup API (native ``sqlite3``) or the
    CLI ``.backup`` command when a *binary* is provided. Both
    methods produce a self-contained, consistent snapshot that
    includes uncommitted WAL data.

    Raises:
        sqlite3.OperationalError: When the backup command fails or
            the destination file is not created.
        ValueError: When a path contains characters that are unsafe
            for CLI dot-commands.
    """
    if binary:
        _validate_path_for_cli(str(source_path))
        dest_str = str(dest_path)
        _validate_path_for_cli(dest_str)
        escaped = dest_str.replace("'", "''")
        result = subprocess.run(
            [str(binary), str(source_path)],
            input=f".backup '{escaped}'\n",
            capture_output=True, text=True, timeout=300,
        )
        error_output = (result.stderr or '').strip()
        if result.returncode != 0 or error_output:
            raise sqlite3.OperationalError(
                f'Backup failed: '
                f'{error_output or "exit code " + str(result.returncode)}'
            )
        if not dest_path.exists() or dest_path.stat().st_size == 0:
            raise sqlite3.OperationalError(
                f'Backup file not created or empty: {dest_path}'
            )
    else:
        source = sqlite3.connect(str(source_path))
        try:
            dest = sqlite3.connect(str(dest_path))
            try:
                source.backup(dest)
            finally:
                dest.close()
        finally:
            source.close()
        if not dest_path.exists() or dest_path.stat().st_size == 0:
            raise sqlite3.OperationalError(
                f'Backup file not created or empty: {dest_path}'
            )
