"""Absence forms."""

from datetime import date

from flask_wtf import FlaskForm
from wtforms import (
    SelectField, TextAreaField, DateField, TimeField, BooleanField,
    SelectMultipleField
)
from wtforms.validators import DataRequired, InputRequired, Optional, Length, ValidationError

from core.settings_manager import settings_manager
from modules.absence.recurrence import recurrence_service
from utils.helpers import format_date_for_user
from utils.request_validators import MAX_DATE_RANGE_DAYS, min_allowed_date


def _check_lower_bound(value: date | None) -> None:
    """Reject dates before the range accepted for URL parameters."""
    if value is not None and value < min_allowed_date():
        raise ValidationError(
            f'Datum darf nicht vor dem {format_date_for_user(min_allowed_date())} liegen.'
        )


def _check_span(start: date | None, end: date | None) -> None:
    """Reject spans longer than the maximum date range of the views."""
    if start is not None and end is not None and (end - start).days > MAX_DATE_RANGE_DAYS:
        raise ValidationError(
            f'Der Zeitraum darf höchstens {MAX_DATE_RANGE_DAYS} Tage umfassen.'
        )


def _check_planning_horizon(value: date, label: str) -> None:
    """Reject dates beyond the configured planning horizon."""
    if value > recurrence_service.max_future_date:
        months = settings_manager.get('limits_max_future_months')
        raise ValidationError(
            f'{label} darf maximal {months} Monate in der Zukunft liegen.'
        )


class AbsenceForm(FlaskForm):
    """Absence create/edit form."""

    user_id = SelectField(
        'Person',
        coerce=lambda x: int(x) if x and x != '' else None,
        validators=[
            InputRequired(message='Person ist erforderlich.')
        ]
    )
    category_id = SelectField(
        'Kategorie',
        coerce=lambda x: int(x) if x and x != '' else None,
        validators=[
            InputRequired(message='Kategorie ist erforderlich.')
        ]
    )
    start_date = DateField(
        'Von',
        validators=[
            DataRequired(message='Startdatum ist erforderlich.')
        ]
    )
    end_date = DateField(
        'Bis',
        validators=[
            DataRequired(message='Enddatum ist erforderlich.')
        ]
    )

    time_type = SelectField(
        'Zeittyp',
        choices=[
            ('all_day', 'Ganztags'),
            ('half_day_morning', 'Halbtags Vormittag'),
            ('half_day_afternoon', 'Halbtags Nachmittag'),
            ('custom_time', 'Benutzerdefinierte Zeit')
        ],
        default='all_day'
    )

    start_time = TimeField(
        'Von (Uhrzeit)',
        validators=[Optional()]
    )
    end_time = TimeField(
        'Bis (Uhrzeit)',
        validators=[Optional()]
    )

    substitute_id = SelectField(
        'Vertretung',
        coerce=lambda x: int(x) if x and x != '' else None,
        validators=[Optional()]
    )

    notes = TextAreaField(
        'Notizen',
        validators=[
            Optional(),
            Length(max=1000, message='Notizen dürfen maximal 1000 Zeichen lang sein.')
        ]
    )

    is_recurring = BooleanField('Wiederholen', default=False)

    recurrence_frequency = SelectField(
        'Wiederholungsart',
        choices=[
            ('daily', 'Täglich'),
            ('weekly', 'Wöchentlich'),
            ('biweekly', 'Alle 2 Wochen')
        ],
        default='weekly'
    )

    recurrence_weekdays = SelectMultipleField(
        'An Wochentagen',
        choices=[
            ('MO', 'Mo'),
            ('TU', 'Di'),
            ('WE', 'Mi'),
            ('TH', 'Do'),
            ('FR', 'Fr'),
            ('SA', 'Sa'),
            ('SU', 'So')
        ]
    )

    recurrence_end_date = DateField(
        'Serie endet am',
        validators=[Optional()]
    )

    def validate_start_date(self, field):
        """Ensure the start date is not before the accepted range."""
        _check_lower_bound(field.data)

    def validate_end_date(self, field):
        """Ensure end date is after start date and within planning horizon."""
        if field.data is None:
            return
        _check_lower_bound(field.data)
        if self.start_date.data and field.data < self.start_date.data:
            raise ValidationError('Enddatum darf nicht vor dem Startdatum liegen.')
        # A series takes its length from the recurrence end, not from end_date.
        if not self.is_recurring.data:
            _check_span(self.start_date.data, field.data)
        _check_planning_horizon(field.data, 'Enddatum')

    def validate_end_time(self, field):
        """Ensure end time is after start time when custom times are used."""
        if self.time_type.data == 'custom_time':
            if self.start_time.data and field.data:
                if field.data <= self.start_time.data:
                    raise ValidationError('Endzeit muss nach der Startzeit liegen.')

    def validate_recurrence_end_date(self, field):
        """Ensure recurrence end date is within planning horizon."""
        if not self.is_recurring.data or field.data is None:
            return
        _check_lower_bound(field.data)
        _check_planning_horizon(field.data, 'Serien-Enddatum')
        if self.start_date.data and field.data < self.start_date.data:
            raise ValidationError('Serien-Enddatum darf nicht vor dem Startdatum liegen.')

    def validate_recurrence_weekdays(self, field):
        """Ensure at least one weekday is selected for weekly/biweekly patterns."""
        if self.is_recurring.data and self.recurrence_frequency.data in ('weekly', 'biweekly'):
            if not field.data:
                raise ValidationError('Mindestens ein Wochentag muss ausgewählt werden.')

    def get_time_flags(self):
        """Return time type flags for database storage."""
        time_type = self.time_type.data
        return {
            'is_all_day': time_type == 'all_day',
            'is_half_day_morning': time_type == 'half_day_morning',
            'is_half_day_afternoon': time_type == 'half_day_afternoon',
            'start_time': self.start_time.data if time_type == 'custom_time' else None,
            'end_time': self.end_time.data if time_type == 'custom_time' else None
        }

    def set_time_type_from_absence(self, absence):
        """Set time_type field based on absence data."""
        if absence.is_half_day_morning:
            self.time_type.data = 'half_day_morning'
        elif absence.is_half_day_afternoon:
            self.time_type.data = 'half_day_afternoon'
        elif absence.start_time and absence.end_time:
            self.time_type.data = 'custom_time'
            self.start_time.data = absence.start_time
            self.end_time.data = absence.end_time
        else:
            self.time_type.data = 'all_day'

    def get_recurrence_data(self):
        """Return recurrence data for database storage."""
        if not self.is_recurring.data:
            return {
                'is_recurring': False,
                'rrule': None,
                'recurrence_end_date': None
            }

        weekdays = self.recurrence_weekdays.data if self.recurrence_frequency.data in ('weekly', 'biweekly') else None

        end_date = recurrence_service.validate_recurrence_end_date(
            self.recurrence_end_date.data
        )

        rrule = recurrence_service.build_rrule_string(
            frequency=self.recurrence_frequency.data,
            weekdays=weekdays,
            end_date=end_date
        )

        return {
            'is_recurring': True,
            'rrule': rrule,
            'recurrence_end_date': end_date
        }

    def set_recurrence_from_absence(self, absence):
        """Set recurrence fields based on absence data.

        An unreadable stored pattern leaves the pattern fields empty and shows
        the message there, so saving the form writes a fresh, valid rule.
        """
        self.is_recurring.data = absence.is_recurring

        if absence.is_recurring and absence.rrule:
            self.recurrence_end_date.data = absence.recurrence_end_date
            try:
                parsed = recurrence_service.validate_rrule(absence.rrule)
            except ValueError as error:
                self.recurrence_weekdays.errors = [str(error)]
                return
            self.recurrence_frequency.data = parsed['frequency']
            self.recurrence_weekdays.data = parsed['weekdays']
            if self.recurrence_end_date.data is None:
                self.recurrence_end_date.data = parsed['end_date']


class OccurrenceEditForm(FlaskForm):
    """Form for editing a single occurrence of a recurring absence."""

    category_id = SelectField(
        'Kategorie',
        coerce=lambda x: int(x) if x and x != '' else None,
        validators=[InputRequired(message='Kategorie ist erforderlich.')]
    )

    time_type = SelectField(
        'Zeittyp',
        choices=[
            ('all_day', 'Ganztags'),
            ('half_day_morning', 'Halbtags Vormittag'),
            ('half_day_afternoon', 'Halbtags Nachmittag')
        ],
        default='all_day'
    )

    substitute_id = SelectField(
        'Vertretung',
        coerce=lambda x: int(x) if x and x != '' else None,
        validators=[Optional()]
    )

    notes = TextAreaField(
        'Notizen',
        validators=[
            Optional(),
            Length(max=1000, message='Notizen dürfen maximal 1000 Zeichen lang sein.')
        ]
    )

    def allow_series_time(self) -> None:
        """Offer keeping the series time window as a choice.

        Only a series with a custom time window has one to keep; a single
        occurrence cannot define a window of its own.
        """
        self.time_type.choices = list(self.time_type.choices) + [
            ('custom_time', 'Uhrzeit der Serie')
        ]

    def get_effective_state(self) -> dict:
        """Return the complete desired state for the occurrence.

        Maps the UI time_type selection to the stored value ('all_day',
        'morning', 'afternoon', 'custom_time'). All fields are always
        provided so the caller can store only those differing from the
        parent absence as overrides.
        """
        time_type_map = {
            'all_day': 'all_day',
            'half_day_morning': 'morning',
            'half_day_afternoon': 'afternoon',
            'custom_time': 'custom_time'
        }
        notes = self.notes.data.strip() if self.notes.data else None
        return {
            'category_id': self.category_id.data,
            'time_type': time_type_map[self.time_type.data],
            'substitute_id': self.substitute_id.data,
            'notes': notes or None
        }
