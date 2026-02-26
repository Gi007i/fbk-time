"""Absence forms.

Provides forms for absence CRUD operations.
"""

from datetime import timedelta

from flask_wtf import FlaskForm
from wtforms import (
    SelectField, TextAreaField, DateField, TimeField, BooleanField,
    SelectMultipleField
)
from wtforms.validators import DataRequired, InputRequired, Optional, Length, ValidationError


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

    def validate_end_date(self, field):
        """Ensure end date is not before start date."""
        if self.start_date.data and field.data:
            if field.data < self.start_date.data:
                raise ValidationError('Enddatum darf nicht vor dem Startdatum liegen.')

    def validate_end_time(self, field):
        """Ensure end time is after start time when custom times are used."""
        if self.time_type.data == 'custom_time':
            if self.start_time.data and field.data:
                if field.data <= self.start_time.data:
                    raise ValidationError('Endzeit muss nach der Startzeit liegen.')

    def validate_recurrence_end_date(self, field):
        """Ensure recurrence end date is within 1 year of start date."""
        if self.is_recurring.data and field.data and self.start_date.data:
            max_end = self.start_date.data + timedelta(days=365)
            if field.data > max_end:
                raise ValidationError('Serie kann maximal 1 Jahr dauern.')
            if field.data < self.start_date.data:
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

        from modules.absence.recurrence import recurrence_service

        weekdays = self.recurrence_weekdays.data if self.recurrence_frequency.data in ('weekly', 'biweekly') else None

        end_date = recurrence_service.validate_recurrence_end_date(
            self.start_date.data,
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
        """Set recurrence fields based on absence data."""
        self.is_recurring.data = absence.is_recurring

        if absence.is_recurring and absence.rrule:
            from modules.absence.recurrence import recurrence_service

            parsed = recurrence_service.parse_rrule_string(absence.rrule)
            self.recurrence_frequency.data = parsed['frequency']
            self.recurrence_weekdays.data = parsed['weekdays']
            self.recurrence_end_date.data = absence.recurrence_end_date or parsed.get('end_date')


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

    def get_modifications(self):
        """Return modifications dictionary for RecurrenceException."""
        time_type = self.time_type.data
        return {
            'category_id': self.category_id.data,
            'is_half_day_morning': time_type == 'half_day_morning',
            'is_half_day_afternoon': time_type == 'half_day_afternoon',
            'substitute_id': self.substitute_id.data,
            'notes': self.notes.data
        }


class FilterForm(FlaskForm):
    """Absence list filter form."""

    class Meta:
        csrf = False  # Not needed for GET forms

    user_id = SelectField(
        'Person',
        coerce=lambda x: int(x) if x and x != '' and x != 'all' else None,
        validators=[Optional()]
    )
    category_id = SelectField(
        'Kategorie',
        coerce=lambda x: int(x) if x and x != '' and x != 'all' else None,
        validators=[Optional()]
    )
    date_from = DateField(
        'Von',
        validators=[Optional()]
    )
    date_to = DateField(
        'Bis',
        validators=[Optional()]
    )
    has_substitute = SelectField(
        'Vertretung',
        choices=[
            ('', 'Alle'),
            ('yes', 'Mit Vertretung'),
            ('no', 'Ohne Vertretung')
        ],
        validators=[Optional()]
    )
