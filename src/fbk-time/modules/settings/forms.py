"""Settings forms.

Provides forms for user preferences and admin system settings.
"""

import re

from flask_wtf import FlaskForm
from wtforms import StringField, SelectField, RadioField, IntegerField, BooleanField
from wtforms.validators import (
    AnyOf,
    DataRequired,
    InputRequired,
    Length,
    NumberRange,
    Regexp,
    ValidationError,
)

from core.timezone import SUPPORTED_TIMEZONES


_HHMM_PATTERN = re.compile(r'^(?:[01]\d|2[0-3]):[0-5]\d$')


class SettingsForm(FlaskForm):
    """Application settings form."""

    holiday_region = SelectField(
        'Feiertags-Region',
        choices=[
            ('none', 'Keine Feiertage'),
            ('DE-nationwide', 'Nur Bundesweit'),
            ('DE-all', 'Bundesweit + Alle Bundesländer'),
            ('DE-BW', 'Baden-Württemberg'),
            ('DE-BY', 'Bayern'),
            ('DE-BE', 'Berlin'),
            ('DE-BB', 'Brandenburg'),
            ('DE-HB', 'Bremen'),
            ('DE-HH', 'Hamburg'),
            ('DE-HE', 'Hessen'),
            ('DE-MV', 'Mecklenburg-Vorpommern'),
            ('DE-NI', 'Niedersachsen'),
            ('DE-NW', 'Nordrhein-Westfalen'),
            ('DE-RP', 'Rheinland-Pfalz'),
            ('DE-SL', 'Saarland'),
            ('DE-SN', 'Sachsen'),
            ('DE-ST', 'Sachsen-Anhalt'),
            ('DE-SH', 'Schleswig-Holstein'),
            ('DE-TH', 'Thüringen')
        ]
    )
    theme = RadioField(
        'Design',
        choices=[
            ('light', 'Hell'),
            ('dark', 'Dunkel'),
            ('auto', 'Automatisch')
        ]
    )
    date_format = SelectField(
        'Datumsformat',
        choices=[]  # Set dynamically in view with current year
    )
    pagination = SelectField(
        'Einträge pro Seite',
        choices=[
            ('5', '5'),
            ('10', '10'),
            ('25', '25'),
            ('50', '50'),
            ('100', '100'),
            ('0', 'Alle')
        ],
        coerce=int
    )
    default_text_color = StringField(
        'Standard-Schriftfarbe für Kategorien',
        validators=[
            DataRequired(message='Standard-Schriftfarbe ist erforderlich.'),
            Length(min=7, max=7, message='Schriftfarbe muss im Format #RRGGBB sein.'),
            Regexp(r'^#[0-9A-Fa-f]{6}$', message='Schriftfarbe muss im Format #RRGGBB sein.')
        ]
    )


class AdminSettingsForm(FlaskForm):
    """Admin system settings form."""

    app_timezone = SelectField(
        'Anwendungs-Zeitzone',
        choices=[(tz, tz) for tz in SUPPORTED_TIMEZONES],
        validators=[
            DataRequired(message='Wert erforderlich.'),
            AnyOf(SUPPORTED_TIMEZONES, message='Ungültige Zeitzone.')
        ]
    )

    lockout_threshold = IntegerField(
        'Fehlversuche bis Sperrung',
        validators=[
            InputRequired(message='Wert erforderlich.'),
            NumberRange(min=1, max=100, message='Wert muss zwischen 1 und 100 liegen.')
        ]
    )
    lockout_duration_minutes = IntegerField(
        'Sperrdauer (Minuten)',
        validators=[
            InputRequired(message='Wert erforderlich.'),
            NumberRange(min=1, max=1440, message='Wert muss zwischen 1 und 1440 liegen.')
        ]
    )
    lockout_delay_enabled = BooleanField('Progressive Verzögerung aktivieren')
    lockout_delay_base_seconds = IntegerField(
        'Basis-Verzögerung (Sekunden)',
        validators=[
            InputRequired(message='Wert erforderlich.'),
            NumberRange(min=0, max=60, message='Wert muss zwischen 0 und 60 liegen.')
        ]
    )
    lockout_delay_max_seconds = IntegerField(
        'Maximale Verzögerung (Sekunden)',
        validators=[
            InputRequired(message='Wert erforderlich.'),
            NumberRange(min=0, max=300, message='Wert muss zwischen 0 und 300 liegen.')
        ]
    )
    lockout_attempt_retention_hours = IntegerField(
        'Fehlversuch-Aufbewahrung (Stunden)',
        validators=[
            InputRequired(message='Wert erforderlich.'),
            NumberRange(min=1, max=720, message='Wert muss zwischen 1 und 720 liegen.')
        ]
    )
    lockout_cleanup_enabled = BooleanField('Automatische Bereinigung aktivieren')
    lockout_cleanup_interval_hours = IntegerField(
        'Bereinigungsintervall (Stunden)',
        validators=[
            InputRequired(message='Wert erforderlich.'),
            NumberRange(min=1, max=168, message='Wert muss zwischen 1 und 168 liegen.')
        ]
    )

    password_min_length = IntegerField(
        'Minimale Passwortlänge',
        validators=[
            InputRequired(message='Wert erforderlich.'),
            NumberRange(min=8, max=128, message='Wert muss zwischen 8 und 128 liegen.')
        ]
    )
    password_max_length = IntegerField(
        'Maximale Passwortlänge',
        validators=[
            InputRequired(message='Wert erforderlich.'),
            NumberRange(min=16, max=256, message='Wert muss zwischen 16 und 256 liegen.')
        ]
    )
    password_require_uppercase = BooleanField('Grossbuchstaben erforderlich')
    password_require_lowercase = BooleanField('Kleinbuchstaben erforderlich')
    password_require_numbers = BooleanField('Zahlen erforderlich')
    password_require_symbols = BooleanField('Sonderzeichen erforderlich')
    password_force_change_on_first_login = BooleanField('Passwortänderung beim ersten Login erzwingen')

    inactive_account_auto_disable = BooleanField('Inaktive Konten automatisch deaktivieren')
    inactive_account_days = IntegerField(
        'Inaktivitätstage bis Deaktivierung',
        validators=[
            InputRequired(message='Wert erforderlich.'),
            NumberRange(min=7, max=365, message='Wert muss zwischen 7 und 365 liegen.')
        ]
    )

    self_registration_enabled = BooleanField('Selbstregistrierung erlauben')

    operation_mode = SelectField(
        'Betriebsmodus',
        choices=[
            ('single_user', 'Einzelbenutzermodus'),
            ('multi_user', 'Mehrbenutzermodus')
        ]
    )

    user_default_theme = SelectField(
        'Standard-Design für neue Benutzer',
        choices=[
            ('light', 'Hell'),
            ('dark', 'Dunkel'),
            ('auto', 'Automatisch')
        ]
    )
    user_default_date_format = SelectField(
        'Standard-Datumsformat für neue Benutzer',
        choices=[]  # Set dynamically in view with current year
    )
    user_default_items_per_page = SelectField(
        'Standard-Einträge pro Seite für neue Benutzer',
        choices=[
            ('5', '5'),
            ('10', '10'),
            ('25', '25'),
            ('50', '50'),
            ('100', '100'),
            ('0', 'Alle')
        ],
        coerce=int
    )
    user_default_holiday_region = SelectField(
        'Standard-Feiertags-Region für neue Benutzer',
        choices=[
            ('none', 'Keine Feiertage'),
            ('DE-nationwide', 'Nur Bundesweit'),
            ('DE-all', 'Bundesweit + Alle Bundesländer'),
            ('DE-BW', 'Baden-Württemberg'),
            ('DE-BY', 'Bayern'),
            ('DE-BE', 'Berlin'),
            ('DE-BB', 'Brandenburg'),
            ('DE-HB', 'Bremen'),
            ('DE-HH', 'Hamburg'),
            ('DE-HE', 'Hessen'),
            ('DE-MV', 'Mecklenburg-Vorpommern'),
            ('DE-NI', 'Niedersachsen'),
            ('DE-NW', 'Nordrhein-Westfalen'),
            ('DE-RP', 'Rheinland-Pfalz'),
            ('DE-SL', 'Saarland'),
            ('DE-SN', 'Sachsen'),
            ('DE-ST', 'Sachsen-Anhalt'),
            ('DE-SH', 'Schleswig-Holstein'),
            ('DE-TH', 'Thüringen')
        ]
    )
    user_default_text_color = StringField(
        'Standard-Schriftfarbe für neue Benutzer',
        validators=[
            DataRequired(message='Wert erforderlich.'),
            Length(min=7, max=7, message='Farbe muss im Format #RRGGBB sein.'),
            Regexp(r'^#[0-9A-Fa-f]{6}$', message='Farbe muss im Format #RRGGBB sein.')
        ]
    )

    limits_max_future_months = IntegerField(
        'Maximaler Planungshorizont (Monate)',
        validators=[
            InputRequired(message='Wert erforderlich.'),
            NumberRange(min=1, max=24, message='Wert muss zwischen 1 und 24 liegen.')
        ]
    )
    limits_bulk_delete_items = IntegerField(
        'Maximale Anzahl Einträge pro Massenlöschung',
        validators=[
            InputRequired(message='Wert erforderlich.'),
            NumberRange(min=10, max=1000, message='Wert muss zwischen 10 und 1000 liegen.')
        ]
    )

    backup_scheduled_enabled = BooleanField(
        'Automatische Datensicherung'
    )
    backup_time = StringField(
        'Sicherungszeitpunkt',
        validators=[
            InputRequired(message='Wert erforderlich.'),
            Length(min=5, max=5, message='Format HH:MM erwartet.')
        ]
    )
    backup_retention_count = IntegerField(
        'Aufzubewahrende Sicherungen',
        validators=[
            InputRequired(message='Wert erforderlich.'),
            NumberRange(min=1, max=365, message='Wert muss zwischen 1 und 365 liegen.')
        ]
    )

    def validate_backup_time(self, field):
        """Reject anything that is not a strict HH:MM 24-hour value."""
        if not _HHMM_PATTERN.fullmatch(field.data or ''):
            raise ValidationError('Ungültiges Format, erwartet HH:MM (00:00–23:59).')
