"""Dynamic CSS services.

Provides business logic for generating category CSS.
"""

import re

from modules.category.models import Category


HEX_COLOR_PATTERN = re.compile(r'^#[0-9A-Fa-f]{6}$')


def is_valid_hex_color(color: str) -> bool:
    """Validate hex color format to prevent CSS injection.

    Args:
        color: Color string to validate.

    Returns:
        True if valid #RRGGBB format, False otherwise.
    """
    return bool(color and HEX_COLOR_PATTERN.match(color))


def generate_category_css() -> str:
    """Generate CSS for all category colors.

    Creates CSS classes for each category:
    - .category-{id}: Background and text color
    - .half-day-morning.category-{id}: Left-half gradient
    - .half-day-afternoon.category-{id}: Right-half gradient

    Colors are validated to prevent CSS injection.

    Returns:
        Generated CSS content string.
    """
    categories = Category.query.all()
    css_lines = []

    for cat in categories:
        if not is_valid_hex_color(cat.color):
            continue
        if not is_valid_hex_color(cat.text_color):
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

    return '\n'.join(css_lines)
