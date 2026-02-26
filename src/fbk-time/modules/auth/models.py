"""Authentication models.

Provides the User model with RBAC and LoginAttempt model for account lockout.
"""

import enum
from datetime import datetime, timezone

from flask_login import UserMixin

from core.extensions import db


def _utc_now():
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


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


class User(UserMixin, db.Model):
    """User model combining authentication and user data.

    Each user is both a login account and a user record.
    Uses Argon2id for password hashing via argon2-cffi.
    """

    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)

    # Authentication
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)

    # Profile fields
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=True, index=True)

    # RBAC
    role = db.Column(db.Enum(UserRole), default=UserRole.USER, nullable=False)
    status = db.Column(db.Enum(UserStatus), default=UserStatus.ACTIVE, nullable=False)

    # Security
    last_login = db.Column(db.DateTime, nullable=True)
    force_password_change = db.Column(db.Boolean, default=False, nullable=False)
    has_real_password = db.Column(db.Boolean, default=True, nullable=False)

    # Timestamps
    created_at = db.Column(db.DateTime, default=_utc_now, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utc_now, onupdate=_utc_now, nullable=False)

    # User Settings (defaults from settings.json user_defaults)
    theme = db.Column(db.String(20), default='light', nullable=False)
    date_format = db.Column(db.String(20), default='DD.MM.YYYY', nullable=False)
    items_per_page = db.Column(db.Integer, default=10, nullable=False)
    holiday_region = db.Column(db.String(50), default='none', nullable=False)
    default_text_color = db.Column(db.String(7), default='#FFFFFF', nullable=False)

    # Relationships
    absences = db.relationship(
        'Absence',
        foreign_keys='Absence.user_id',
        backref='user',
        lazy='dynamic',
        cascade='all, delete-orphan',
        passive_deletes=True
    )

    def __repr__(self):
        return f'<User {self.username} ({self.role.value})>'

    @property
    def is_admin(self):
        """Check if user has admin role."""
        return self.role == UserRole.ADMIN

    @property
    def is_manager(self):
        """Check if user has manager or admin role."""
        return self.role in (UserRole.ADMIN, UserRole.MANAGER)

    @property
    def is_active_account(self):
        """Check if account is active and can login."""
        return self.status == UserStatus.ACTIVE


class LoginAttempt(db.Model):
    """Track failed login attempts for account lockout protection.

    Locks account after configured failed attempts for configured duration.
    """

    __tablename__ = 'login_attempts'

    id = db.Column(db.Integer, primary_key=True)
    identifier = db.Column(db.String(255), unique=True, nullable=False, index=True)
    attempt_count = db.Column(db.Integer, default=0, nullable=False)
    last_attempt = db.Column(db.DateTime, default=_utc_now)
    locked_until = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f'<LoginAttempt {self.identifier}: {self.attempt_count}>'
