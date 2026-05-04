"""Profile forms.

Provides forms for self-service profile edits (name, email).
"""

from flask_wtf import FlaskForm
from flask_login import current_user
from wtforms import StringField
from wtforms.validators import DataRequired, Length, Optional, ValidationError

from utils.validators import EmailFormat
from modules.user.services import email_exists


class ProfileEditForm(FlaskForm):
    """Form for editing own profile (display name and email)."""

    name = StringField('Anzeigename', validators=[
        DataRequired(message='Anzeigename ist erforderlich'),
        Length(min=2, max=100, message='Anzeigename muss zwischen 2 und 100 Zeichen lang sein')
    ])

    email = StringField('E-Mail-Adresse', validators=[
        Optional(),
        EmailFormat(message='Ungültige E-Mail-Adresse'),
        Length(max=120)
    ])

    def validate_email(self, field):
        """Ensure email is unique across other users."""
        if field.data:
            if email_exists(field.data, exclude_user_id=current_user.id):
                raise ValidationError('Diese E-Mail-Adresse ist bereits registriert.')
