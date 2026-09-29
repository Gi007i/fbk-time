"""Template context processors for values every rendered template needs."""

from datetime import datetime

from .version import APP_VERSION


def register(application) -> None:
    """Register all context processors on the application."""

    @application.context_processor
    def inject_navigation():
        from utils.navigation import resolve_origin, origin_link, back_url, back_url_focused
        from utils.filters import filter_url, view_switch_url

        return {
            'origin': resolve_origin(),
            'origin_url': origin_link,
            'back_url': back_url,
            'back_url_focused': back_url_focused,
            'filter_url': filter_url,
            'view_switch_url': view_switch_url,
        }

    @application.context_processor
    def inject_settings():
        from flask import current_app

        from core.auth import current_user
        from core.timezone import get_app_timezone
        from utils.validators import get_password_policy_info

        context = {
            'app_version': APP_VERSION,
            'current_year': datetime.now(get_app_timezone()).year,
            'password_policy': get_password_policy_info(),
        }

        if current_user.is_authenticated:
            from core.session_lifecycle import (
                absolute_remaining_seconds,
                remaining_session_seconds,
            )
            idle_timeout = current_app.config['SESSION_IDLE_TIMEOUT']
            context.update({
                'app_theme': current_user.theme,
                'app_date_format': current_user.date_format,
                'session_idle_seconds': int(idle_timeout.total_seconds()),
                'session_warning_seconds': current_app.config['SESSION_IDLE_WARNING_SECONDS'],
                'session_remaining_seconds': remaining_session_seconds(),
                'session_absolute_seconds': absolute_remaining_seconds(),
            })
        else:
            from core.settings_manager import settings_manager

            context.update({
                'app_theme': settings_manager.get('user_default_theme'),
                'app_date_format': settings_manager.get('user_default_date_format'),
            })

        return context
