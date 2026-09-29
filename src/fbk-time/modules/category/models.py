"""Category model.

Provides the Category model for absence categories.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import String
from sqlalchemy.orm import Mapped, WriteOnlyMapped, mapped_column, relationship

from core.db import Base

from . import helpers


def _utc_now():
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


class Category(Base):
    """Absence category with visual styling and substitute requirement.

    Deletion is restricted if absences exist (handled in application logic).
    """

    __tablename__ = 'categories'

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    color: Mapped[str] = mapped_column(String(7))  # e.g., "#FF5733"
    text_color: Mapped[str] = mapped_column(String(7), default='#FFFFFF')
    icon: Mapped[Optional[str]] = mapped_column(String(50))
    requires_substitute: Mapped[bool] = mapped_column(default=False)
    is_present: Mapped[bool] = mapped_column(default=False)  # True = working remotely, False = absent
    sort_order: Mapped[Optional[int]] = mapped_column(default=0)
    active: Mapped[bool] = mapped_column(default=True, index=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(default=_utc_now)

    # Deletion is guarded in application code; passive_deletes leaves the
    # FK RESTRICT backstop entirely to the database.
    absences: WriteOnlyMapped['Absence'] = relationship(
        back_populates='category', passive_deletes='all'
    )

    @property
    def contrast_ratio(self) -> Optional[float]:
        """Contrast ratio of the label text on the category colour."""
        return helpers.contrast_ratio(self.text_color, self.color)

    @property
    def has_low_contrast(self) -> bool:
        """Whether the colour pair stays below the readable minimum."""
        ratio = self.contrast_ratio
        return ratio is not None and ratio < helpers.MIN_CONTRAST_RATIO

    def __repr__(self):
        return f'<Category {self.name}>'
