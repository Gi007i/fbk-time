"""Dynamic CSS views.

Provides server-generated CSS for database-driven styles (category colors).
This approach is CSP-compliant as it avoids inline styles.
"""

import re

from flask import Blueprint, make_response

bp = Blueprint('css', __name__, url_prefix='/css')
from modules.category.models import Category

# Regex for valid hex color codes
HEX_COLOR_PATTERN = re.compile(r'^#[0-9A-Fa-f]{6}$')


def _is_valid_hex_color(color: str) -> bool:
    """
    Validate hex color format to prevent CSS injection.

    Args:
        color: Color string to validate.

    Returns:
        True if valid #RRGGBB format, False otherwise.
    """
    return bool(color and HEX_COLOR_PATTERN.match(color))


@bp.route('/categories.css')
def categories_css():
    """
    Generate CSS for all category colors.

    Returns CSS classes for each category:
    - .category-{id}: Background and text color
    - .half-day-morning.category-{id}: Left-half gradient
    - .half-day-afternoon.category-{id}: Right-half gradient

    Colors are validated to prevent CSS injection.

    Returns:
        CSS response with appropriate headers for caching.
    """
    categories = Category.query.all()
    css_lines = []

    for cat in categories:
        if not _is_valid_hex_color(cat.color):
            continue
        if not _is_valid_hex_color(cat.text_color):
            continue

        css_lines.append(
            f'.category-{cat.id} {{ '
            f'background-color: {cat.color}; '
            f'color: {cat.text_color}; '
            f'}}'
        )

        css_lines.append(
            f'.half-day-morning.category-{cat.id} {{ '
            f'background: linear-gradient(to right, {cat.color} 50%, transparent 50%); '
            f'}}'
        )

        css_lines.append(
            f'.half-day-afternoon.category-{cat.id} {{ '
            f'background: linear-gradient(to right, transparent 50%, {cat.color} 50%); '
            f'}}'
        )

    response = make_response('\n'.join(css_lines))
    response.headers['Content-Type'] = 'text/css; charset=utf-8'
    response.headers['Cache-Control'] = 'public, max-age=3600'
    return response
