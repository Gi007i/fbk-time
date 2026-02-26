"""Absence models.

Provides Absence, AbsenceHistory, and RecurrenceException models.
"""

from datetime import datetime, timezone

from core.extensions import db


def _utc_now():
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


class Absence(db.Model):
    """Absence record with flexible time options and substitute support.

    Delete behaviors:
        user_id: CASCADE (delete absence when user deleted)
        category_id: RESTRICT (prevent category deletion, handled in app)
        substitute_id: SET NULL (clear substitute reference when user deleted)
    """

    __tablename__ = 'absences'

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    category_id = db.Column(
        db.Integer,
        db.ForeignKey('categories.id', ondelete='RESTRICT'),
        nullable=False,
        index=True
    )

    start_date = db.Column(db.Date, nullable=False, index=True)
    end_date = db.Column(db.Date, nullable=False, index=True)

    start_time = db.Column(db.Time, nullable=True)
    end_time = db.Column(db.Time, nullable=True)

    is_all_day = db.Column(db.Boolean, default=True, nullable=False)
    is_half_day_morning = db.Column(db.Boolean, default=False, nullable=False)
    is_half_day_afternoon = db.Column(db.Boolean, default=False, nullable=False)

    substitute_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True,
        index=True
    )

    notes = db.Column(db.Text, nullable=True)

    rrule = db.Column(db.String(500), nullable=True)  # e.g., "FREQ=WEEKLY;BYDAY=MO"
    is_recurring = db.Column(db.Boolean, default=False, nullable=False, index=True)
    recurrence_end_date = db.Column(db.Date, nullable=True)  # Max 1 year from start_date

    created_at = db.Column(db.DateTime, default=_utc_now)
    updated_at = db.Column(db.DateTime, default=_utc_now, onupdate=_utc_now)

    __table_args__ = (
        db.Index('ix_absence_user_dates', 'user_id', 'start_date', 'end_date'),
    )

    substitute = db.relationship(
        'User',
        foreign_keys=[substitute_id],
        backref='substitute_for'
    )

    history = db.relationship(
        'AbsenceHistory',
        backref='absence',
        lazy='dynamic',
        cascade='all, delete-orphan',
        passive_deletes=True
    )

    exceptions = db.relationship(
        'RecurrenceException',
        backref='absence',
        lazy='dynamic',
        cascade='all, delete-orphan',
        passive_deletes=True
    )

    def __repr__(self):
        return f'<Absence {self.user.name if self.user else "?"} {self.start_date}>'

    @property
    def duration_days(self):
        """Calculate number of days for this absence.

        Half-day absences (morning/afternoon) subtract 0.5 from total.
        Custom time absences count as full days (informational only).
        """
        if self.start_date and self.end_date:
            delta = self.end_date - self.start_date
            base_days = delta.days + 1

            # Half-day subtracts 0.5 from total
            # e.g., 15.01.-17.01. afternoon = 2.5 days (3 - 0.5)
            if self.is_half_day_morning or self.is_half_day_afternoon:
                return base_days - 0.5

            return base_days
        return 0


class AbsenceHistory(db.Model):
    """Change history for absence records.

    Tracks all modifications with user attribution.
    Automatically deleted when parent absence is deleted (CASCADE).
    """

    __tablename__ = 'absence_history'

    id = db.Column(db.Integer, primary_key=True)

    absence_id = db.Column(
        db.Integer,
        db.ForeignKey('absences.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    changed_by_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True,
        index=True
    )

    changed_at = db.Column(db.DateTime, nullable=False, default=_utc_now)
    field_name = db.Column(db.String(50), nullable=False)
    old_value = db.Column(db.String(255), nullable=True)
    new_value = db.Column(db.String(255), nullable=True)

    changed_by = db.relationship(
        'User',
        foreign_keys=[changed_by_id]
    )

    def __repr__(self):
        return f'<AbsenceHistory {self.field_name} @ {self.changed_at}>'


class RecurrenceException(db.Model):
    """Exceptions for recurring absences (deleted or modified occurrences).

    Stores dates that deviate from the recurring pattern:
        deleted: Occurrence removed from series
        modified: Occurrence with overridden values

    Automatically deleted when parent absence is deleted (CASCADE).
    """

    __tablename__ = 'recurrence_exceptions'

    id = db.Column(db.Integer, primary_key=True)

    absence_id = db.Column(
        db.Integer,
        db.ForeignKey('absences.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    exception_date = db.Column(db.Date, nullable=False, index=True)

    exception_type = db.Column(db.String(10), nullable=False)

    modified_category_id = db.Column(
        db.Integer,
        db.ForeignKey('categories.id', ondelete='SET NULL'),
        nullable=True
    )
    modified_is_half_day_morning = db.Column(db.Boolean, nullable=True)
    modified_is_half_day_afternoon = db.Column(db.Boolean, nullable=True)
    modified_substitute_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True
    )
    modified_notes = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=_utc_now)

    __table_args__ = (
        db.UniqueConstraint('absence_id', 'exception_date', name='uq_exception_date'),
    )

    modified_category = db.relationship(
        'Category',
        foreign_keys=[modified_category_id]
    )
    modified_substitute = db.relationship(
        'User',
        foreign_keys=[modified_substitute_id]
    )

    def __repr__(self):
        return f'<RecurrenceException {self.exception_date} ({self.exception_type})>'
