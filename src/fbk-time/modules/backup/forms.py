"""Backup forms."""

from flask_wtf import FlaskForm
from wtforms import StringField
from wtforms.validators import Optional, Length


class CreateBackupForm(FlaskForm):
    """Form for creating a manual backup."""

    description = StringField(
        'Beschreibung',
        validators=[
            Optional(),
            Length(max=255, message='Beschreibung darf maximal 255 Zeichen lang sein.')
        ]
    )
