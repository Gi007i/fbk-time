"""Handbook helpers.

Reads code-defined values (form choices, validator bounds, module
constants) so that handbook templates stay synchronized with code
without touching the application modules themselves.
"""

from wtforms.validators import NumberRange


def read_pagination_choice_labels() -> str:
    """Return pagination labels from the settings form as a comma-joined string.

    The 'Alle' sentinel ('0') is excluded so that the numeric options can
    be listed separately in the handbook.
    """
    from modules.settings.forms import SettingsForm
    choices = SettingsForm.pagination.kwargs.get('choices', [])
    labels = [label for value, label in choices if value != '0']
    return ', '.join(labels)


def read_sort_order_bounds() -> tuple[int, int] | None:
    """Return (min, max) of the category sort_order NumberRange validator.

    Returns None if no NumberRange validator is configured.
    """
    from modules.category.forms import CategoryForm
    validators = CategoryForm.sort_order.kwargs.get('validators', [])
    for validator in validators:
        if isinstance(validator, NumberRange):
            return validator.min, validator.max
    return None


def read_half_day_time_ranges() -> tuple[str, str]:
    """Return (morning, afternoon) half-day time ranges as 'HH:MM–HH:MM' strings.

    Sources the bounds from the iCal exporter so the handbook reflects the
    actual times used in exported calendar events.
    """
    from modules.export import ical
    morning = (
        f'{ical._MORNING_START.strftime("%H:%M")}'
        f'–{ical._MORNING_END.strftime("%H:%M")}'
    )
    afternoon = (
        f'{ical._AFTERNOON_START.strftime("%H:%M")}'
        f'–{ical._AFTERNOON_END.strftime("%H:%M")}'
    )
    return morning, afternoon
