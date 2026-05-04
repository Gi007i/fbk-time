"""Template context processors.

Provides values that should be available in every rendered template,
injected once per request rather than passed explicitly at each
``render_template`` call site.
"""

from .version import APP_VERSION


def register(application) -> None:
    """Register all context processors on the application."""

    @application.context_processor
    def inject_settings():
        from flask_login import current_user
        from utils.validators import get_password_policy_info

        context = {
            'app_version': APP_VERSION,
            'password_policy': get_password_policy_info(),
        }

        if current_user.is_authenticated:
            context.update({
                'app_theme': current_user.theme,
                'app_date_format': current_user.date_format,
                'app_pagination': current_user.items_per_page,
                'app_holiday_region': current_user.holiday_region,
            })
        else:
            context.update({
                'app_theme': 'light',
                'app_date_format': 'DD.MM.YYYY',
                'app_pagination': 10,
                'app_holiday_region': 'none',
            })

        return context
