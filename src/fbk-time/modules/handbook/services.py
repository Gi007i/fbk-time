"""Handbook services.

Provides chapter metadata and template-path lookup for the in-app handbook.
Chapter content lives as plain Jinja2 templates under
``templates/handbook/content/`` and is included directly. All chapters are
visible to every authenticated user; chapters that describe functionality
limited to a certain role carry a badge for orientation.
"""

import re

from core.settings_manager import settings_manager

SLUG_PATTERN = re.compile(r'^[a-z0-9][a-z0-9-]*$')

HANDBOOK_SETTING_KEYS: tuple[str, ...] = (
    'password_min_length',
    'password_max_length',
    'password_require_uppercase',
    'password_require_lowercase',
    'password_require_numbers',
    'password_require_symbols',
    'password_force_change_on_first_login',
    'self_registration_enabled',
    'operation_mode',
    'limits_max_future_months',
    'limits_bulk_delete_items',
    'user_default_items_per_page',
)

CHAPTERS: list[dict] = [
    {
        'slug': 'introduction',
        'title': 'Einleitung',
        'section': 'Grundlagen',
        'badge': None,
    },
    {
        'slug': 'login',
        'title': 'Anmeldung und Passwort',
        'section': 'Grundlagen',
        'badge': None,
    },
    {
        'slug': 'dashboard',
        'title': 'Dashboard',
        'section': 'Grundlagen',
        'badge': None,
    },
    {
        'slug': 'absence-create',
        'title': 'Abwesenheit anlegen',
        'section': 'Abwesenheiten',
        'badge': None,
    },
    {
        'slug': 'absence-edit',
        'title': 'Abwesenheit bearbeiten und löschen',
        'section': 'Abwesenheiten',
        'badge': None,
    },
    {
        'slug': 'half-days',
        'title': 'Halbe Tage',
        'section': 'Abwesenheiten',
        'badge': None,
    },
    {
        'slug': 'recurrence',
        'title': 'Serientermine',
        'section': 'Abwesenheiten',
        'badge': None,
    },
    {
        'slug': 'substitutes',
        'title': 'Vertretungen',
        'section': 'Abwesenheiten',
        'badge': None,
    },
    {
        'slug': 'calendar',
        'title': 'Kalender, Team-Übersicht und Liste',
        'section': 'Ansichten',
        'badge': None,
    },
    {
        'slug': 'exports',
        'title': 'Exporte',
        'section': 'Ansichten',
        'badge': None,
    },
    {
        'slug': 'settings',
        'title': 'Meine Einstellungen',
        'section': 'Einstellungen',
        'badge': None,
    },
    {
        'slug': 'users',
        'title': 'Mitarbeitende verwalten',
        'section': 'Verwaltung',
        'badge': 'Manager',
    },
    {
        'slug': 'categories',
        'title': 'Kategorien verwalten',
        'section': 'Verwaltung',
        'badge': 'Manager',
    },
    {
        'slug': 'system-settings',
        'title': 'Systemeinstellungen',
        'section': 'Verwaltung',
        'badge': 'Admin',
    },
    {
        'slug': 'backup',
        'title': 'Datensicherung',
        'section': 'Verwaltung',
        'badge': 'Admin',
    },
]


def _validate_slug(slug: str) -> bool:
    """Check that a slug contains only allowed characters.

    Prevents path traversal and restricts lookup to whitelisted chapters.
    """
    return bool(slug) and bool(SLUG_PATTERN.match(slug))


def get_chapters_by_section() -> list[tuple[str, list[dict]]]:
    """Return all chapters grouped by section, preserving CHAPTERS order."""
    grouped: list[tuple[str, list[dict]]] = []
    for chapter in CHAPTERS:
        section = chapter['section']
        if grouped and grouped[-1][0] == section:
            grouped[-1][1].append(chapter)
        else:
            grouped.append((section, [chapter]))
    return grouped


def find_chapter(slug: str) -> dict | None:
    """Return chapter metadata for a slug if it exists in the whitelist."""
    if not _validate_slug(slug):
        return None
    for chapter in CHAPTERS:
        if chapter['slug'] == slug:
            return chapter
    return None


def get_default_chapter() -> dict:
    """Return the first chapter of the handbook."""
    return CHAPTERS[0]


def get_chapter_template(slug: str) -> str | None:
    """Return the Jinja template path for a chapter's body content."""
    chapter = find_chapter(slug)
    if chapter is None:
        return None
    return f'handbook/content/{chapter["slug"]}.html'


def get_adjacent_chapters(slug: str) -> tuple[dict | None, dict | None]:
    """Return the previous and next chapter relative to the given slug."""
    for index, chapter in enumerate(CHAPTERS):
        if chapter['slug'] == slug:
            previous = CHAPTERS[index - 1] if index > 0 else None
            following = CHAPTERS[index + 1] if index < len(CHAPTERS) - 1 else None
            return previous, following
    return None, None


def get_handbook_settings() -> dict:
    """Return the whitelisted settings available to handbook templates.

    Only non-sensitive settings are exposed. Security-relevant keys such
    as lockout thresholds and inactivity deadlines are deliberately
    omitted so that concrete values are not advertised in the manual.

    In addition to settings, structural values are read directly from
    forms and the iCal exporter so that the handbook stays in sync with
    code changes without touching application code.

    Returns:
        Dict mapping whitelisted setting keys and derived values to
        their current state.
    """
    from .helpers import (
        read_half_day_time_ranges,
        read_pagination_choice_labels,
        read_sort_order_bounds,
    )

    settings = {key: settings_manager.get(key) for key in HANDBOOK_SETTING_KEYS}

    settings['pagination_choices'] = read_pagination_choice_labels()

    sort_bounds = read_sort_order_bounds()
    if sort_bounds is not None:
        settings['sort_order_min'], settings['sort_order_max'] = sort_bounds

    morning, afternoon = read_half_day_time_ranges()
    settings['half_day_morning_range'] = morning
    settings['half_day_afternoon_range'] = afternoon

    return settings
