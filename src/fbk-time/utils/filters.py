"""Shared absence filter parsing and filter-preserving URL building.

The three absence overviews (list, calendar, team) share the same subject
filters: person, category and substitute presence. Parsing and URL
propagation live here so the behaviour stays identical across views and the
active filters survive both paging within a view and switching between views.
The list view adds its own date range on top; that range is view-specific and
is never propagated to the other views.
"""

from calendar import monthrange
from datetime import date

from flask import abort, request, url_for

from utils.request_validators import validate_int_list_param


# Overviews that address time by calendar month; the list uses a date range.
_MONTH_ENDPOINTS = ('absences.calendar', 'dashboard.team_overview')


def parse_absence_filters() -> dict:
    """Parse and validate the shared subject filters from the request.

    Returns:
        Dict with validated ``user_ids`` and ``category_ids`` (lists of ints
        from the repeated ``user_id`` / ``category_id`` params, empty when
        unset) and ``has_substitute`` ('yes', 'no' or None).
    """
    has_substitute = request.args.get('has_substitute')
    if has_substitute and has_substitute not in ('yes', 'no'):
        abort(400, 'Invalid has_substitute')

    return {
        'user_ids': validate_int_list_param('user_id', min_value=1),
        'category_ids': validate_int_list_param('category_id', min_value=1),
        'has_substitute': has_substitute,
    }


def active_filter_args() -> dict:
    """Return the non-empty subject filters from the current request.

    Person and category values are carried forward as lists so url_for
    re-expands them into repeated query parameters.
    """
    args = {}
    for key in ('user_id', 'category_id'):
        values = [v for v in request.args.getlist(key) if v]
        if values:
            args[key] = values
    has_substitute = request.args.get('has_substitute')
    if has_substitute:
        args['has_substitute'] = has_substitute
    return args


def filter_url(endpoint: str, **values) -> str:
    """Build a URL that carries the active subject filters forward.

    Explicit ``values`` (e.g. a target month or week) take precedence over
    carried filters. Used for time navigation so the filters persist while
    paging.
    """
    args = active_filter_args()
    args.update(values)
    return url_for(endpoint, **args)


def view_switch_url(
    target_endpoint: str,
    year=None,
    month=None,
    week_start=None,
    date_from=None,
    date_to=None
) -> str:
    """Build a URL to another overview, carrying filters and the time frame.

    The source view passes its own time frame either as ``year``/``month``
    (calendar, team) or as ``date_from``/``date_to`` (list). The frame is
    translated to whatever the target expects: month-based targets receive
    the month of the frame's start, the list receives the full month range.
    A ``week_start`` is forwarded to month-based targets as well so the mobile
    five-day week view lands on the same week; it is inert on desktop.
    Subject filters are carried along unchanged.
    """
    args = active_filter_args()

    if year and month:
        start = date(int(year), int(month), 1)
        end = date(int(year), int(month), monthrange(int(year), int(month))[1])
    elif date_from:
        start = date.fromisoformat(date_from)
        end = date.fromisoformat(date_to) if date_to else start
    else:
        start = None

    if start is not None:
        if target_endpoint in _MONTH_ENDPOINTS:
            args['year'] = start.year
            args['month'] = start.month
            if week_start:
                args['week_start'] = week_start
        elif target_endpoint == 'absences.list_absences':
            args['date_from'] = start.isoformat()
            args['date_to'] = end.isoformat()

    return url_for(target_endpoint, **args)
