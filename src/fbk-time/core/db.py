"""Database layer.

Engine and session management on plain SQLAlchemy with the SQLite
tuning required for concurrent multi-device access (WAL mode, immediate
transactions).
"""

import sqlite3

from flask import abort
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, scoped_session, sessionmaker


class Base(DeclarativeBase):
    """Declarative base for all models."""


class Database:
    """Engine and thread-scoped session container for the application."""

    def __init__(self):
        self._engine = None
        # Thread-local scope matches sync Gunicorn workers and the
        # per-thread contexts of scheduler and CLI; greenlet workers would
        # need a different scopefunc.
        self.session = scoped_session(sessionmaker())

    @property
    def engine(self) -> Engine:
        return self._engine

    def init_app(self, application) -> None:
        """Create the engine from application config and bind the session.

        Registers an app-context teardown so every request and CLI context
        returns its session to a clean state.
        """
        # CLI scripts build a second app in the same process; the previous
        # engine's pooled connections must not leak past the rebind.
        if self._engine is not None:
            self._engine.dispose()
        self._engine = create_engine(
            application.config['DATABASE_URI'],
            **application.config['DATABASE_ENGINE_OPTIONS'],
        )
        self.session.configure(bind=self._engine)

        @application.teardown_appcontext
        def remove_session(exception):
            self.session.remove()

    def create_all(self) -> None:
        Base.metadata.create_all(self._engine)

    def get_or_404(self, model, ident):
        """Return the instance for the primary key or abort with 404."""
        instance = self.session.get(model, ident)
        if instance is None:
            abort(404)
        return instance


db = Database()


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
        # A restored backup may be an untrusted file: forbid unsafe schema
        # constructs and add per-page integrity checks.
        cursor.execute("PRAGMA trusted_schema=OFF")
        cursor.execute("PRAGMA cell_size_check=ON")

        cursor.close()

        # Defense in depth: cap value and statement sizes at the driver.
        dbapi_connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000)
        dbapi_connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 100_000)

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
