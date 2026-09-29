"""Category forms."""

from flask_wtf import FlaskForm
from wtforms import StringField, BooleanField, IntegerField, SelectField
from wtforms.validators import DataRequired, Length, Optional, NumberRange, Regexp, AnyOf

from .helpers import CATEGORY_ICONS


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
        default='#2563EB'
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
            AnyOf(CATEGORY_ICONS, message='Icon muss aus der Auswahlliste stammen.')
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

    # Rendered as radio buttons in the template, not a hidden input, so
    # form.hidden_tag() must not emit a second empty "action" field.
    action = StringField(
        validators=[
            DataRequired(message='Bitte wählen Sie eine Option.'),
            AnyOf(['transfer', 'delete_all', 'delete_empty'], message='Ungültige Aktion. Bitte wählen Sie eine Option.')
        ]
    )
    new_category_id = SelectField(
        'Abwesenheiten übertragen nach',
        coerce=lambda x: int(x) if x and x != '' else None,
        # A target deleted meanwhile must reach the service and its German
        # message instead of the generic choice error.
        validate_choice=False,
        validators=[Optional()]
    )
