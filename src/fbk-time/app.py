"""WSGI entry point.

Provides the Flask application instance for Gunicorn.

Usage:
    gunicorn -c gunicorn.conf.py app:app
    python app.py  (local execution)
"""

from flask import Flask, render_template, g, jsonify

from config import Config, get_config
from utils.session_navigation import is_ajax_request
from core.extensions import db, login_manager, csrf
from core.cleanup import schedule_cleanup
from core.licenses import ensure_licenses_current
from core.settings_manager import settings_manager


def create_app(config_class=None, cli_mode=False):
    """Create and configure the Flask application.

    Args:
        config_class: Configuration class to use. Defaults to environment-based config.
        cli_mode: If True, skip web-specific initialization (blueprints, scheduler).

    Returns:
        Configured Flask application instance.
    """
    application = Flask(__name__)

    if config_class is None:
        config_class = get_config()
    application.config.from_object(config_class)

    config_class.init_app(application)

    db.init_app(application)
    login_manager.init_app(application)
    csrf.init_app(application)

    login_manager.login_view = 'auth.login'
    login_manager.login_message = None

    if not cli_mode:
        from modules.auth.views import bp as auth_bp
        from modules.user.views import bp as user_bp
        from modules.category.views import bp as category_bp
        from modules.absence.views import bp as absence_bp
        from modules.dashboard.views import bp as dashboard_bp
        from modules.export.views import bp as export_bp
        from modules.settings.views import bp as settings_bp
        from modules.css.views import bp as css_bp
        from modules.licenses.views import bp as licenses_bp
        from modules.profile.views import bp as profile_bp

        application.register_blueprint(auth_bp)
        application.register_blueprint(user_bp)
        application.register_blueprint(category_bp)
        application.register_blueprint(absence_bp)
        application.register_blueprint(dashboard_bp)
        application.register_blueprint(export_bp)
        application.register_blueprint(settings_bp)
        application.register_blueprint(css_bp)
        application.register_blueprint(licenses_bp)
        application.register_blueprint(profile_bp)

    with application.app_context():
        db.create_all()

        # Initialize settings from template if database is empty
        from modules.settings.models import Setting
        if Setting.query.count() == 0:
            settings_manager.seed_defaults()
        else:
            settings_manager.load_all()

        if not cli_mode:
            if settings_manager.get('lockout_cleanup_enabled'):
                schedule_cleanup(application)

    if not cli_mode:
        ensure_licenses_current()
        register_context_processor(application)
        register_jinja_filters(application)
        register_error_handlers(application)

    return application


def register_context_processor(application):
    """Register context processor to inject settings into all templates."""

    @application.context_processor
    def inject_settings():
        from flask_login import current_user
        from utils.validators import get_password_policy_info

        context = {
            'password_policy': get_password_policy_info()
        }

        if current_user.is_authenticated:
            context.update({
                'app_theme': current_user.theme,
                'app_date_format': current_user.date_format,
                'app_pagination': current_user.items_per_page,
                'app_holiday_region': current_user.holiday_region
            })
        else:
            context.update({
                'app_theme': 'light',
                'app_date_format': 'DD.MM.YYYY',
                'app_pagination': 10,
                'app_holiday_region': 'none'
            })

        return context


def register_jinja_filters(application):
    """Register custom Jinja filters."""

    @application.template_filter('format_date')
    def format_date_filter(value, short=False, include_time=False):
        """Format date according to user setting, converting UTC to local time."""
        if value is None:
            return ''

        from datetime import datetime
        from zoneinfo import ZoneInfo
        from flask_login import current_user

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


def register_error_handlers(application):
    """Register custom error handlers.

    Returns JSON responses for AJAX requests, HTML for regular requests.
    """

    @application.errorhandler(400)
    def bad_request_error(error):
        if is_ajax_request():
            return jsonify({'error': 'Ungültige Anfrage.'}), 400
        return render_template('errors/400.html'), 400

    @application.errorhandler(403)
    def forbidden_error(error):
        if is_ajax_request():
            return jsonify({'error': 'Zugriff verweigert.'}), 403
        return render_template('errors/403.html'), 403

    @application.errorhandler(404)
    def not_found_error(error):
        if is_ajax_request():
            return jsonify({'error': 'Ressource nicht gefunden.'}), 404
        return render_template('errors/404.html'), 404

    @application.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        if is_ajax_request():
            return jsonify({'error': 'Ein Fehler ist aufgetreten.'}), 500
        return render_template('errors/500.html'), 500


app = create_app()


if __name__ == '__main__':
    app.run(debug=False, host=Config.HOST, port=Config.PORT)
