"""Category forms.

Provides forms for category CRUD operations.
"""

from flask_wtf import FlaskForm
from wtforms import StringField, BooleanField, IntegerField, SelectField, HiddenField
from wtforms.validators import DataRequired, Length, Optional, NumberRange, Regexp


class CategoryForm(FlaskForm):
    """Category create/edit form."""

    name = StringField(
        'Name',
        validators=[
            DataRequired(message='Name ist erforderlich.'),
            Length(min=2, max=50, message='Name muss 2-50 Zeichen lang sein.')
        ]
    )
    color = StringField(
        'Farbe',
        validators=[
            DataRequired(message='Farbe ist erforderlich.'),
            Regexp(
                r'^#[0-9A-Fa-f]{6}$',
                message='Farbe muss im Format #RRGGBB sein.'
            )
        ],
        default='#3B82F6'
    )
    text_color = StringField(
        'Schriftfarbe',
        validators=[
            DataRequired(message='Schriftfarbe ist erforderlich.'),
            Regexp(
                r'^#[0-9A-Fa-f]{6}$',
                message='Schriftfarbe muss im Format #RRGGBB sein.'
            )
        ],
        default='#FFFFFF'
    )
    icon = StringField(
        'Icon',
        validators=[
            Optional(),
            Length(max=50, message='Icon-Name darf maximal 50 Zeichen lang sein.')
        ]
    )
    requires_substitute = BooleanField('Vertretung erforderlich', default=False)
    is_present = BooleanField('Anwesend', default=False)
    sort_order = IntegerField(
        'Sortierung',
        validators=[
            Optional(),
            NumberRange(min=0, max=999, message='Sortierung muss zwischen 0 und 999 liegen.')
        ],
        default=0
    )
    active = BooleanField('Aktiv', default=True)


class CategoryDeleteForm(FlaskForm):
    """Category deletion form with transfer option."""

    action = HiddenField(
        validators=[DataRequired()]
    )
    new_category_id = SelectField(
        'Abwesenheiten übertragen nach',
        coerce=lambda x: int(x) if x and x != '' else None,
        validators=[Optional()]
    )
