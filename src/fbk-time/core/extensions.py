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


@event.listens_for(Engine, "connect")
def enable_sqlite_wal_mode(dbapi_connection, connection_record):
    """
    Enable WAL mode and Foreign Keys for SQLite connections.

    WAL mode allows concurrent reads during writes - required for
    multi-device access with same user account.
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

        cursor.close()
