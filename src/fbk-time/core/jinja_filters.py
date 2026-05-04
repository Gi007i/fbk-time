"""Jinja2 template filters.

Application-wide filters registered on the Flask application. Imports
inside filter functions keep the module lightweight and avoid eager
imports of model/extension code at app-creation time.
"""

from datetime import datetime
from zoneinfo import ZoneInfo


def register(application) -> None:
    """Register all template filters on the application."""

    @application.template_filter('role_label')
    def role_label(value):
        """Return the German label for a UserRole enum or its string value."""
        from modules.auth.models import UserRole

        if value is None:
            return ''

        role_value = value.value if isinstance(value, UserRole) else value

        labels = {
            UserRole.ADMIN.value: 'Admin',
            UserRole.MANAGER.value: 'Manager',
            UserRole.USER.value: 'Benutzer',
        }
        return labels.get(role_value, role_value)

    @application.template_filter('format_date')
    def format_date(value, short=False, include_time=False):
        """Format date according to user setting, converting UTC to local time."""
        from flask_login import current_user

        if value is None:
            return ''

        if isinstance(value, datetime):
            local_tz = ZoneInfo('Europe/Berlin')
            if value.tzinfo is None:
                value = value.replace(tzinfo=ZoneInfo('UTC'))
            value = value.astimezone(local_tz)

        if current_user.is_authenticated:
            date_format = current_user.date_format
        else:
            date_format = 'DD.MM.YYYY'

        if date_format == 'YYYY-MM-DD':
            fmt = '%m-%d' if short else '%Y-%m-%d'
        else:
            fmt = '%d.%m.' if short else '%d.%m.%Y'

        if include_time:
            fmt += ' %H:%M'

        return value.strftime(fmt)

    @application.template_filter('field_range')
    def field_range(field):
        """Return 'min-max' from a field's Length or NumberRange validator.

        Used in tooltips so range texts stay synchronized with validators.
        Length uses ``-1`` as the unset sentinel; NumberRange uses ``None``.
        Both ends must be set; otherwise an empty string is returned.
        """
        from wtforms.validators import Length, NumberRange

        for validator in field.validators:
            if isinstance(validator, Length):
                if validator.min != -1 and validator.max != -1:
                    return f'{validator.min}-{validator.max}'
            elif isinstance(validator, NumberRange):
                if validator.min is not None and validator.max is not None:
                    return f'{validator.min}-{validator.max}'
        return ''
