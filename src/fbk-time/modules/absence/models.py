"""Absence, AbsenceHistory and RecurrenceException models."""

from datetime import date, datetime, time, timezone
from typing import Optional

from sqlalchemy import (
    ColumnElement, ForeignKey, Index, String, Text, UniqueConstraint, or_, text
)
from sqlalchemy.orm import Mapped, WriteOnlyMapped, mapped_column, relationship

from core.db import Base
from modules.auth.models import User
from modules.category.models import Category


def _utc_now():
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


class Absence(Base):
    """Absence record with flexible time options and substitute support.

    Delete behaviors:
        user_id: CASCADE (delete absence when user deleted)
        category_id: RESTRICT (prevent category deletion, handled in app)
        substitute_id: SET NULL (clear substitute reference when user deleted)
    """

    __tablename__ = 'absences'

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey('users.id', ondelete='CASCADE'),
        index=True
    )

    category_id: Mapped[int] = mapped_column(
        ForeignKey('categories.id', ondelete='RESTRICT'),
        index=True
    )

    start_date: Mapped[date] = mapped_column(index=True)
    end_date: Mapped[date] = mapped_column(index=True)

    start_time: Mapped[Optional[time]]
    end_time: Mapped[Optional[time]]

    is_all_day: Mapped[bool] = mapped_column(default=True)
    is_half_day_morning: Mapped[bool] = mapped_column(default=False)
    is_half_day_afternoon: Mapped[bool] = mapped_column(default=False)

    substitute_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey('users.id', ondelete='SET NULL'),
        index=True
    )

    notes: Mapped[Optional[str]] = mapped_column(Text)

    rrule: Mapped[Optional[str]] = mapped_column(String(500))  # e.g., "FREQ=WEEKLY;BYDAY=MO"
    is_recurring: Mapped[bool] = mapped_column(default=False, index=True)
    recurrence_end_date: Mapped[Optional[date]]

    created_at: Mapped[Optional[datetime]] = mapped_column(default=_utc_now)
    updated_at: Mapped[Optional[datetime]] = mapped_column(default=_utc_now, onupdate=_utc_now)

    __table_args__ = (
        Index('ix_absence_user_dates', 'user_id', 'start_date', 'end_date'),
    )

    user: Mapped[User] = relationship(
        foreign_keys=[user_id],
        back_populates='absences'
    )

    category: Mapped[Category] = relationship(
        back_populates='absences'
    )

    substitute: Mapped[Optional[User]] = relationship(
        foreign_keys=[substitute_id]
    )

    history: WriteOnlyMapped['AbsenceHistory'] = relationship(
        back_populates='absence',
        cascade='all, delete-orphan',
        passive_deletes=True
    )

    exceptions: WriteOnlyMapped['RecurrenceException'] = relationship(
        back_populates='absence',
        cascade='all, delete-orphan',
        passive_deletes=True
    )

    def __repr__(self):
        return f'<Absence {self.user.name if self.user else "?"} {self.start_date}>'

    @classmethod
    def overlaps(cls, range_start: date, range_end: date) -> ColumnElement[bool]:
        """Return a filter for records that can have occurrences in a range.

        A series is bounded by its recurrence end, a single absence by its
        end date.
        """
        return or_(
            (cls.is_recurring == False)
            & (cls.start_date <= range_end)
            & (cls.end_date >= range_start),
            (cls.is_recurring == True)
            & (cls.start_date <= range_end)
            & (
                (cls.recurrence_end_date >= range_start)
                | cls.recurrence_end_date.is_(None)
            )
        )

    @property
    def duration_days(self):
        """Calculate number of days for this absence.

        A half-day flag applies to every day of the span, so it halves the
        total. Custom time absences count as full days (informational only).
        """
        if self.start_date and self.end_date:
            delta = self.end_date - self.start_date
            base_days = delta.days + 1

            # e.g., 15.01.-17.01. afternoon = 1.5 days (3 * 0.5)
            if self.is_half_day_morning or self.is_half_day_afternoon:
                return base_days * 0.5

            return base_days
        return 0


class AbsenceHistory(Base):
    """Change history for absence records.

    Tracks all modifications with user attribution.
    Automatically deleted when parent absence is deleted (CASCADE).
    """

    __tablename__ = 'absence_history'

    id: Mapped[int] = mapped_column(primary_key=True)

    absence_id: Mapped[int] = mapped_column(
        ForeignKey('absences.id', ondelete='CASCADE'),
        index=True
    )

    changed_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey('users.id', ondelete='SET NULL'),
        index=True
    )

    changed_at: Mapped[datetime] = mapped_column(default=_utc_now)
    field_name: Mapped[str] = mapped_column(String(50))
    old_value: Mapped[Optional[str]] = mapped_column(String(255))
    new_value: Mapped[Optional[str]] = mapped_column(String(255))

    absence: Mapped[Absence] = relationship(back_populates='history')

    changed_by: Mapped[Optional[User]] = relationship(
        foreign_keys=[changed_by_id]
    )

    def __repr__(self):
        return f'<AbsenceHistory {self.field_name} @ {self.changed_at}>'


class RecurrenceException(Base):
    """Exceptions for recurring absences (deleted or modified occurrences).

    Stores dates that deviate from the recurring pattern:
        deleted: Occurrence removed from series
        modified: Occurrence with overridden values

    Automatically deleted when parent absence is deleted (CASCADE).
    """

    __tablename__ = 'recurrence_exceptions'

    id: Mapped[int] = mapped_column(primary_key=True)

    absence_id: Mapped[int] = mapped_column(
        ForeignKey('absences.id', ondelete='CASCADE'),
        index=True
    )

    exception_date: Mapped[date] = mapped_column(index=True)

    exception_type: Mapped[str] = mapped_column(String(10))

    modified_category_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey('categories.id', ondelete='SET NULL')
    )
    # server_default mirrors what the v1.4.0 upgrade script writes, so a fresh
    # install and an upgraded one end up with the same column definition.
    modified_category_overridden: Mapped[bool] = mapped_column(
        default=False, server_default=text('0')
    )
    modified_time_type: Mapped[Optional[str]] = mapped_column(String(20))
    modified_substitute_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey('users.id', ondelete='SET NULL')
    )
    modified_substitute_overridden: Mapped[bool] = mapped_column(
        default=False, server_default=text('0')
    )
    modified_notes: Mapped[Optional[str]] = mapped_column(Text)
    modified_notes_overridden: Mapped[bool] = mapped_column(
        default=False, server_default=text('0')
    )

    created_at: Mapped[Optional[datetime]] = mapped_column(default=_utc_now)

    __table_args__ = (
        UniqueConstraint('absence_id', 'exception_date', name='uq_exception_date'),
    )

    absence: Mapped[Absence] = relationship(back_populates='exceptions')

    modified_category: Mapped[Optional[Category]] = relationship(
        foreign_keys=[modified_category_id]
    )
    modified_substitute: Mapped[Optional[User]] = relationship(
        foreign_keys=[modified_substitute_id]
    )

    def __repr__(self):
        return f'<RecurrenceException {self.exception_date} ({self.exception_type})>'
