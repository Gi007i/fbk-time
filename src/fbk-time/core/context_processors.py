"""Template context processors.

Provides values that should be available in every rendered template,
injected once per request rather than passed explicitly at each
``render_template`` call site.
"""

from .version import APP_VERSION


def register(application) -> None:
    """Register all context processors on the application."""

    @application.context_processor
    def inject_navigation():
        from utils.navigation import resolve_origin, origin_link, back_url
        from utils.filters import filter_url, view_switch_url

        return {
            'origin': resolve_origin(),
            'origin_url': origin_link,
            'back_url': back_url,
            'filter_url': filter_url,
            'view_switch_url': view_switch_url,
        }

    @application.context_processor
    def inject_settings():
        from flask import current_app
        from flask_login import current_user
        from utils.validators import get_password_policy_info

        context = {
            'app_version': APP_VERSION,
            'password_policy': get_password_policy_info(),
            'session_timeout_enabled': False,
        }

        if current_user.is_authenticated:
            context.update({
                'app_theme': current_user.theme,
                'app_date_format': current_user.date_format,
                'app_pagination': current_user.items_per_page,
                'app_holiday_region': current_user.holiday_region,
            })
            idle_timeout = current_app.config['SESSION_IDLE_TIMEOUT']
            if idle_timeout:
                from core.session_lifecycle import (
                    absolute_remaining_seconds,
                    remaining_session_seconds,
                )
                context.update({
                    'session_timeout_enabled': True,
                    'session_idle_seconds': int(idle_timeout.total_seconds()),
                    'session_warning_seconds': current_app.config['SESSION_IDLE_WARNING_SECONDS'],
                    'session_remaining_seconds': remaining_session_seconds(),
                    'session_absolute_seconds': absolute_remaining_seconds(),
                })
        else:
            context.update({
                'app_theme': 'light',
                'app_date_format': 'DD.MM.YYYY',
                'app_pagination': 10,
                'app_holiday_region': 'none',
            })

        return context
