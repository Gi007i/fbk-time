"""Authentication forms.

Provides login, re-authentication and registration forms with CSRF
protection.
"""

from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField
from wtforms.validators import DataRequired, Length, EqualTo, Optional, ValidationError

from utils.validators import validate_password_strength, EmailFormat, SafeText, UsernameFormat


class LoginForm(FlaskForm):
    """User login form."""

    username = StringField(
        'Benutzername',
        validators=[
            DataRequired(message='Benutzername ist erforderlich.'),
            Length(min=2, max=80, message='Benutzername muss 2-80 Zeichen lang sein.')
        ]
    )
    password = PasswordField(
        'Passwort',
        validators=[
            DataRequired(message='Passwort ist erforderlich.')
        ]
    )
    remember = BooleanField('Angemeldet bleiben')


class ReauthenticateForm(FlaskForm):
    """Password confirmation before a sensitive action."""

    password = PasswordField(
        'Passwort',
        validators=[
            DataRequired(message='Passwort ist erforderlich.')
        ]
    )


class RegistrationForm(FlaskForm):
    """User self-registration form."""

    username = StringField(
        'Benutzername',
        validators=[
            DataRequired(message='Benutzername ist erforderlich.'),
            Length(min=3, max=80, message='Benutzername muss 3-80 Zeichen lang sein.'),
            UsernameFormat()
        ]
    )
    name = StringField(
        'Vollständiger Name',
        validators=[
            DataRequired(message='Name ist erforderlich.'),
            Length(min=2, max=100, message='Name muss 2-100 Zeichen lang sein.'),
            SafeText(message='Name enthält ungültige Zeichen.')
        ]
    )
    email = StringField(
        'E-Mail',
        validators=[
            Optional(),
            EmailFormat(message='Ungültige E-Mail-Adresse.'),
            Length(max=120, message='E-Mail darf maximal 120 Zeichen lang sein.')
        ]
    )
    password = PasswordField(
        'Passwort',
        validators=[
            DataRequired(message='Passwort ist erforderlich.')
        ]
    )
    password_confirm = PasswordField(
        'Passwort bestätigen',
        validators=[
            DataRequired(message='Passwortbestätigung ist erforderlich.'),
            EqualTo('password', message='Passwörter stimmen nicht überein.')
        ]
    )

    def validate_password(self, field):
        """Validate password strength against configured policy."""
        is_valid, error_msg = validate_password_strength(field.data)
        if not is_valid:
            raise ValidationError(error_msg)


class ChangePasswordForm(FlaskForm):
    """Password change form for forced password changes."""

    current_password = PasswordField(
        'Aktuelles Passwort',
        validators=[
            DataRequired(message='Aktuelles Passwort ist erforderlich.')
        ]
    )
    new_password = PasswordField(
        'Neues Passwort',
        validators=[
            DataRequired(message='Neues Passwort ist erforderlich.')
        ]
    )
    new_password_confirm = PasswordField(
        'Neues Passwort bestätigen',
        validators=[
            DataRequired(message='Passwortbestätigung ist erforderlich.'),
            EqualTo('new_password', message='Passwörter stimmen nicht überein.')
        ]
    )

    def validate_new_password(self, field):
        """Validate new password strength against configured policy."""
        is_valid, error_msg = validate_password_strength(field.data)
        if not is_valid:
            raise ValidationError(error_msg)
