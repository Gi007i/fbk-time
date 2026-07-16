"""Flask extensions.

Centralizes extension initialization to avoid circular imports.
Includes WAL mode configuration for SQLite concurrent access.
"""

import sqlite3
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import event
from sqlalchemy.engine import Engine


db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()

# Bind the session to a client identifier (user-agent + IP) so a stolen
# session cookie is rejected when replayed from a different client. A
# remember-me cookie replayed without a session cookie is not covered:
# Flask-Login skips the check for an empty session and mints a fresh one.
login_manager.session_protection = 'strong'


@event.listens_for(Engine, "connect")
def enable_sqlite_wal_mode(dbapi_connection, connection_record):
    """Apply SQLite PRAGMAs and hand transaction control to SQLAlchemy.

    WAL mode allows concurrent reads during writes - required for
    multi-device access with same user account. Setting ``isolation_level``
    to ``None`` disables the driver's implicit ``BEGIN`` so transaction scope
    is emitted explicitly by ``begin_sqlite_transaction``.
    """
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()

        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA cache_size=10000")
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.execute("PRAGMA mmap_size=268435456")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA secure_delete=ON")

        cursor.close()

        dbapi_connection.isolation_level = None


@event.listens_for(Engine, "begin")
def begin_sqlite_transaction(connection):
    """Open every SQLite transaction with ``BEGIN IMMEDIATE``.

    A deferred ``BEGIN`` takes a read lock first, so a later write in the
    same transaction must upgrade to a write lock. That upgrade fails with
    ``database is locked`` (SQLITE_BUSY) - ignoring ``busy_timeout`` - when
    another connection wrote after the read lock was taken. Acquiring the
    write lock up front removes the upgrade and lets writers queue within
    the busy timeout instead of failing.
    """
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql("BEGIN IMMEDIATE")
