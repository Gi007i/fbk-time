"""Dynamic CSS views.

Provides server-generated CSS for database-driven styles (category colors).
This approach is CSP-compliant as it avoids inline styles.
"""

from flask import Blueprint, make_response
from flask_login import login_required

from .services import generate_category_css

bp = Blueprint('css', __name__, url_prefix='/css')


@bp.route('/categories.css')
@login_required
def categories_css():
    """Generate CSS for all category colors.

    Returns CSS classes for each category:
    - .category-{id}: Background and text color
    - .half-day-morning.category-{id}: Left-half gradient
    - .half-day-afternoon.category-{id}: Right-half gradient

    Colors are validated to prevent CSS injection.
    Caching is handled by Nginx (no-store on dynamic responses).

    Returns:
        CSS response with text/css content type.
    """
    css_content = generate_category_css()
    response = make_response(css_content)
    response.headers['Content-Type'] = 'text/css; charset=utf-8'
    return response
