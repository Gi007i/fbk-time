"""Category icon and colour helpers.

Single source of truth for the curated category icon set. The emoji
picker grid, the server-side form validation, the web rendering and the
PDF export all resolve icons through this module so every surface shows
the identical bundled Twemoji artwork (static/img/twemoji/, CC-BY 4.0)
instead of platform-dependent native emoji.
"""

import re
from typing import Optional

CATEGORY_ICONS = (
    '🏖️', '🌴', '✈️', '🧳', '🏝️', '☀️', '🌊', '⛱️',
    '🤒', '🏥', '💊', '🩺', '🤕', '😷', '🩹', '❤️‍🩹',
    '🏠', '💻', '🖥️', '📱', '🏢', '🏗️', '🛠️', '🔧',
    '📚', '🎓', '📖', '✏️', '🎯', '💡', '🧠', '📝',
    '👶', '👨‍👩‍👧', '🍼', '🧸', '👪', '❤️', '💑', '🏡',
    '🎉', '🎊', '🎁', '🎂', '💒', '⛪', '🎄', '🎃',
    '⚽', '🏃', '🚴', '🏋️', '🧘', '🏊', '⛷️', '🎿',
    '🚗', '🚕', '🚌', '🚂', '🚀', '⏰', '📅',
)

_ICON_SET = frozenset(CATEGORY_ICONS)


def twemoji_stem(icon: Optional[str]) -> Optional[str]:
    """Return the Twemoji asset filename stem for a curated icon.

    Icons outside the curated set return None; callers omit the icon
    entirely so only bundled artwork is ever rendered.

    Args:
        icon: Emoji string as stored on the category.

    Returns:
        Filename stem matching the bundled assets, or None.
    """
    if not icon or icon not in _ICON_SET:
        return None
    codepoints = [ord(char) for char in icon]
    # Twemoji naming rule: VS16 (U+FE0F) is dropped from filenames unless
    # the emoji is a ZWJ (U+200D) sequence.
    if 0x200D not in codepoints:
        codepoints = [cp for cp in codepoints if cp != 0xFE0F]
    return '-'.join(format(cp, 'x') for cp in codepoints)


# WCAG 2.2 (1.4.3) minimum for regular-size text.
MIN_CONTRAST_RATIO = 4.5

_HEX_COLOR_PATTERN = re.compile(r'^#[0-9A-Fa-f]{6}$')


def _relative_luminance(color: str) -> float:
    """Return the relative luminance of a colour per WCAG 2.2.

    Args:
        color: Colour in #RRGGBB notation.

    Returns:
        Luminance between 0 (black) and 1 (white).
    """
    channels = []
    for offset in (1, 3, 5):
        value = int(color[offset:offset + 2], 16) / 255
        channels.append(
            value / 12.92 if value <= 0.03928
            else ((value + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(foreground: str, background: str) -> Optional[float]:
    """Return the contrast ratio between two colours.

    Colours outside the #RRGGBB notation return None; they are not
    rendered as category styling either.

    Args:
        foreground: Text colour in #RRGGBB notation.
        background: Background colour in #RRGGBB notation.

    Returns:
        Ratio between 1 (identical) and 21 (black on white), or None.
    """
    if not (foreground and background):
        return None
    if not (_HEX_COLOR_PATTERN.match(foreground) and _HEX_COLOR_PATTERN.match(background)):
        return None

    first = _relative_luminance(foreground)
    second = _relative_luminance(background)
    return (max(first, second) + 0.05) / (min(first, second) + 0.05)
