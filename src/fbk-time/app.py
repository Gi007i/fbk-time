"""WSGI entry point.

Provides the Flask application instance for Gunicorn.

Usage:
    gunicorn -c gunicorn.conf.py app:app
    python app.py  (local execution)
"""

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config, get_config
from core import (
    context_processors,
    error_handlers,
    jinja_filters,
    session_lifecycle,
)
from core.backup import backup_manager, start_auto_discovery
from core.scheduler import start_scheduler
from core.extensions import csrf, db, login_manager
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
    application.wsgi_app = ProxyFix(application.wsgi_app, x_for=1, x_proto=1)

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
        _register_blueprints(application)

    backup_manager.init_app(application)

    with application.app_context():
        _initialize_database()
        settings_manager.load_all()
        # Idempotent — inserts missing template keys on every boot, no migration needed.
        settings_manager.seed_defaults()

    if not cli_mode:
        ensure_licenses_current()
        context_processors.register(application)
        jinja_filters.register(application)
        error_handlers.register(application)
        session_lifecycle.register(application)

    return application


def _register_blueprints(application):
    """Import and register all module blueprints."""
    from modules.absence.views import bp as absence_bp
    from modules.auth.views import bp as auth_bp
    from modules.backup.views import bp as backup_bp
    from modules.category.views import bp as category_bp
    from modules.css.views import bp as css_bp
    from modules.dashboard.views import bp as dashboard_bp
    from modules.export.views import bp as export_bp
    from modules.handbook.views import bp as handbook_bp
    from modules.licenses.views import bp as licenses_bp
    from modules.profile.views import bp as profile_bp
    from modules.settings.views import bp as settings_bp
    from modules.user.views import bp as user_bp

    for blueprint in (
        auth_bp, user_bp, category_bp, absence_bp, dashboard_bp,
        export_bp, settings_bp, css_bp, licenses_bp, profile_bp, handbook_bp,
        backup_bp,
    ):
        application.register_blueprint(blueprint)


def _initialize_database():
    """Import all models and create the schema."""
    # All models must be imported before create_all so SQLAlchemy registers their tables.
    from modules.absence.models import Absence, AbsenceHistory, RecurrenceException  # noqa: F401
    from modules.auth.models import LoginAttempt, User  # noqa: F401
    from modules.backup.models import BackupRecord  # noqa: F401
    from modules.category.models import Category  # noqa: F401
    from modules.settings.models import Setting  # noqa: F401

    db.create_all()


app = create_app()


if __name__ == '__main__':
    start_scheduler(app)
    start_auto_discovery(app)
    app.run(debug=False, host=Config.HOST, port=Config.PORT)
