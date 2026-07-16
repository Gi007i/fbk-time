"""User management forms."""

from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SelectField
from wtforms.validators import DataRequired, Length, Optional, ValidationError

from modules.auth.models import UserRole, UserStatus
from utils.validators import validate_password_strength, EmailFormat, SafeText
from .services import username_exists, email_exists


class UserCreateForm(FlaskForm):
    """Form for creating a new user."""

    username = StringField('Benutzername', validators=[
        DataRequired(message='Benutzername ist erforderlich'),
        Length(min=3, max=80, message='Benutzername muss zwischen 3 und 80 Zeichen lang sein')
    ])

    password = PasswordField('Passwort', validators=[
        Optional()
    ])

    name = StringField('Anzeigename', validators=[
        DataRequired(message='Anzeigename ist erforderlich'),
        Length(min=2, max=100, message='Anzeigename muss zwischen 2 und 100 Zeichen lang sein'),
        SafeText(message='Anzeigename enthält ungültige Zeichen')
    ])

    email = StringField('E-Mail', validators=[
        Optional(),
        EmailFormat(message='Ungültige E-Mail-Adresse'),
        Length(max=120)
    ])

    role = SelectField('Rolle', choices=[
        (UserRole.USER.value, 'Benutzer'),
        (UserRole.MANAGER.value, 'Manager'),
        (UserRole.ADMIN.value, 'Admin')
    ], coerce=lambda x: UserRole(x) if x and not isinstance(x, UserRole) else x)

    def validate_username(self, field):
        """Check username is unique."""
        if username_exists(field.data):
            raise ValidationError('Dieser Benutzername ist bereits vergeben.')

    def validate_email(self, field):
        """Check email is unique if provided."""
        if field.data:
            if email_exists(field.data):
                raise ValidationError('Diese E-Mail-Adresse ist bereits registriert.')

    def validate_password(self, field):
        """Validate password strength if provided."""
        if field.data:
            is_valid, error_msg = validate_password_strength(field.data)
            if not is_valid:
                raise ValidationError(error_msg)


class UserEditForm(FlaskForm):
    """Form for editing an existing user."""

    name = StringField('Anzeigename', validators=[
        DataRequired(message='Anzeigename ist erforderlich'),
        Length(min=2, max=100, message='Anzeigename muss zwischen 2 und 100 Zeichen lang sein'),
        SafeText(message='Anzeigename enthält ungültige Zeichen')
    ])

    email = StringField('E-Mail', validators=[
        Optional(),
        EmailFormat(message='Ungültige E-Mail-Adresse'),
        Length(max=120)
    ])

    password = PasswordField('Neues Passwort', validators=[
        Optional()
    ])

    role = SelectField('Rolle', choices=[
        (UserRole.USER.value, 'Benutzer'),
        (UserRole.MANAGER.value, 'Manager'),
        (UserRole.ADMIN.value, 'Admin')
    ], coerce=lambda x: UserRole(x) if x and not isinstance(x, UserRole) else x)

    status = SelectField('Status', choices=[
        (UserStatus.ACTIVE.value, 'Aktiv'),
        (UserStatus.DISABLED.value, 'Deaktiviert'),
        (UserStatus.LOCKED.value, 'Gesperrt'),
        (UserStatus.PENDING.value, 'Ausstehend'),
        (UserStatus.MANAGED.value, 'Verwaltet')
    ], coerce=lambda x: UserStatus(x) if x and not isinstance(x, UserStatus) else x)

    def __init__(self, user=None, *args, **kwargs):
        """Initialize form with user for validation context."""
        super().__init__(*args, **kwargs)
        self.user = user

    def validate_email(self, field):
        """Check email is unique if changed."""
        if field.data and self.user:
            if email_exists(field.data, exclude_user_id=self.user.id):
                raise ValidationError('Diese E-Mail-Adresse ist bereits registriert.')

    def validate_password(self, field):
        """Validate password strength if provided."""
        if field.data:
            is_valid, error_msg = validate_password_strength(field.data)
            if not is_valid:
                raise ValidationError(error_msg)
