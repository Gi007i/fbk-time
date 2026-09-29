"""Authentication models.

Provides the User model with RBAC and LoginAttempt model for account lockout.
"""

import enum
import secrets
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Enum, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, WriteOnlyMapped, mapped_column, relationship, validates

from core.auth import UserMixin
from core.db import Base


def _utc_now():
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


def new_credential_version() -> int:
    """Return a fresh random credential version for a user.

    Random rather than incremented: SQLite may reuse the id of a deleted
    user, and a sequential version would let a leftover session or
    remember cookie of the deleted account match the new one.
    """
    # 62 bits plus one stays inside SQLite's signed 64-bit INTEGER and never
    # equals the legacy column default 0.
    return secrets.randbits(62) + 1


class UserRole(enum.Enum):
    """User roles for RBAC."""

    ADMIN = "admin"
    MANAGER = "manager"
    USER = "user"


class UserStatus(enum.Enum):
    """User account status."""

    PENDING = "pending"
    ACTIVE = "active"
    LOCKED = "locked"
    DISABLED = "disabled"
    MANAGED = "managed"


class User(UserMixin, Base):
    """User model combining authentication and user data.

    Each user is both a login account and a user record.
    Uses Argon2id for password hashing via argon2-cffi.
    """

    __tablename__ = 'users'

    id: Mapped[int] = mapped_column(primary_key=True)

    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))

    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[Optional[str]] = mapped_column(String(120), unique=True, index=True)

    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.USER)
    status: Mapped[UserStatus] = mapped_column(Enum(UserStatus), default=UserStatus.ACTIVE)

    last_login_at: Mapped[Optional[datetime]]
    previous_login_at: Mapped[Optional[datetime]]
    force_password_change: Mapped[bool] = mapped_column(default=False)
    has_real_password: Mapped[bool] = mapped_column(default=True)
    # server_default mirrors what the upgrade scripts write, so a fresh
    # install and an upgraded one end up with the same column definition.
    credential_version: Mapped[int] = mapped_column(
        default=new_credential_version, server_default=text('0')
    )

    created_at: Mapped[datetime] = mapped_column(default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=_utc_now, onupdate=_utc_now)

    theme: Mapped[str] = mapped_column(String(20))
    date_format: Mapped[str] = mapped_column(String(20))
    items_per_page: Mapped[int]
    holiday_region: Mapped[str] = mapped_column(String(50))
    default_text_color: Mapped[str] = mapped_column(String(7))
    start_page: Mapped[str] = mapped_column(String(20), server_default='dashboard')
    view_scope: Mapped[str] = mapped_column(String(20), server_default='all')

    absences: WriteOnlyMapped['Absence'] = relationship(
        foreign_keys='Absence.user_id',
        back_populates='user',
        cascade='all, delete-orphan',
        passive_deletes=True
    )

    def __repr__(self):
        return f'<User {self.username} ({self.role.value})>'

    def get_id(self):
        """Return the session identity, versioned by credential_version.

        Sessions and remember-me cookies persist this value; rotating
        credential_version on a password change or account reactivation
        invalidates all of them.
        """
        return f'{self.id}:{self.credential_version}'

    @validates('status')
    def _rotate_credential_version_on_reactivation(self, key, new_status):
        """Rotate credential_version on any non-ACTIVE -> ACTIVE transition.

        Without it, a session/remember-me cookie issued before deactivation
        would revive on reactivation. Central hook so every path (service,
        view, CLI) is covered.
        """
        previous = self.status
        if (previous is not None
                and previous != UserStatus.ACTIVE
                and new_status == UserStatus.ACTIVE):
            self.credential_version = new_credential_version()
        return new_status

    @property
    def is_admin(self):
        """Check if user has admin role."""
        return self.role == UserRole.ADMIN

    @property
    def is_manager(self):
        """Check if user has manager or admin role."""
        return self.role in (UserRole.ADMIN, UserRole.MANAGER)


class LoginAttemptType(enum.Enum):
    """Namespace an attempt counter belongs to."""

    USERNAME = "username"
    IP_ADDRESS = "ip_address"
    # Identifier: hash of a device cookie entry (see core.auth.known_device_id)
    DEVICE = "device"


class LoginAttempt(Base):
    """Track failed login attempts for lockout and progressive delay.

    A username counter delays and locks the account for clients without a
    trusted device cookie; a device counter withdraws that trust from a
    cookie entry after repeated failures. Namespaces are kept apart so a
    submitted username cannot impersonate an address entry.
    """

    __tablename__ = 'login_attempts'
    __table_args__ = (
        UniqueConstraint(
            'identifier', 'identifier_type',
            name='uq_login_attempts_identifier_type'
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    identifier: Mapped[str] = mapped_column(String(255), index=True)
    identifier_type: Mapped[LoginAttemptType] = mapped_column(
        Enum(LoginAttemptType), server_default='USERNAME'
    )
    attempt_count: Mapped[int] = mapped_column(default=0)
    last_attempt: Mapped[Optional[datetime]] = mapped_column(default=_utc_now)
    locked_until: Mapped[Optional[datetime]]

    def __repr__(self):
        return (
            f'<LoginAttempt {self.identifier_type.value}:{self.identifier}: '
            f'{self.attempt_count}>'
        )
