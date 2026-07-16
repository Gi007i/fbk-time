"""HTTP error handlers.

Returns JSON responses for AJAX requests, HTML error pages otherwise,
so that AJAX callers receive structured errors and full-page navigations
land on the styled error templates.
"""

import logging

from flask import flash, redirect, render_template, url_for

from utils.response_helpers import is_ajax_request

from .extensions import db


logger = logging.getLogger(__name__)


def register(application) -> None:
    """Register all error handlers on the application."""
    from flask_wtf.csrf import CSRFError
    from flask_login import current_user
    from utils.response_helpers import api_error

    @application.errorhandler(CSRFError)
    def handle_csrf_error(error):
        logger.warning('CSRF validation failed: %s', error.description)
        if is_ajax_request():
            return api_error('Sitzung abgelaufen. Bitte Seite neu laden.', status_code=400)
        if not current_user.is_authenticated:
            flash('Ihre Sitzung ist abgelaufen. Bitte erneut anmelden.', 'warning')
            return redirect(url_for('auth.login'))
        return render_template('errors/400.html'), 400

    @application.errorhandler(400)
    def bad_request(error):
        if is_ajax_request():
            return api_error('Ungültige Anfrage.', status_code=400)
        return render_template('errors/400.html'), 400

    @application.errorhandler(403)
    def forbidden(error):
        if is_ajax_request():
            return api_error('Zugriff verweigert.', status_code=403)
        return render_template('errors/403.html'), 403

    @application.errorhandler(404)
    def not_found(error):
        if is_ajax_request():
            return api_error('Ressource nicht gefunden.', status_code=404)
        return render_template('errors/404.html'), 404

    @application.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        if is_ajax_request():
            return api_error('Ein Fehler ist aufgetreten.', status_code=500)
        return render_template('errors/500.html'), 500
