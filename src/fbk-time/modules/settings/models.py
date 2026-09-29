"""Settings models.

Provides the Setting model for runtime-configurable system settings.
"""

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Enum, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base


def _utc_now():
    """Return current UTC datetime without timezone info for SQLite."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SettingDataType(enum.Enum):
    """Data types for settings values."""

    STRING = "string"
    INTEGER = "integer"
    BOOLEAN = "boolean"


class Setting(Base):
    """System setting stored in database.

    Attributes:
        key: Unique setting identifier (e.g., 'lockout_threshold').
        value: Stored value as text (converted based on data_type).
        data_type: Type for value conversion (string, integer, boolean).
        category: Grouping category (e.g., 'lockout', 'password_policy').
        updated_at: Timestamp of last modification.
    """

    __tablename__ = 'settings'

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    data_type: Mapped[SettingDataType] = mapped_column(Enum(SettingDataType))
    category: Mapped[str] = mapped_column(String(50), index=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(default=_utc_now, onupdate=_utc_now)

    def get_typed_value(self):
        """Return value converted to its declared type.

        Returns:
            Value as int, bool, or str depending on data_type.
        """
        if self.data_type == SettingDataType.INTEGER:
            return int(self.value)
        elif self.data_type == SettingDataType.BOOLEAN:
            return self.value.lower() == 'true'
        return self.value

    def set_typed_value(self, value):
        """Set value with type conversion to string.

        Args:
            value: Value to store (int, bool, or str).
        """
        if self.data_type == SettingDataType.BOOLEAN:
            self.value = 'true' if value else 'false'
        else:
            self.value = str(value)

    def __repr__(self):
        return f'<Setting {self.key}={self.value}>'
