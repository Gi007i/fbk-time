"""Origin-based navigation for breadcrumbs and return links.

The current page's address is carried forward via a ``ref`` query parameter
(stateless, tab-safe) instead of server-side session state. Only real internal
overview routes are accepted as an origin; anything else is discarded, which
also prevents open redirects.
"""

from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlsplit, urlunsplit

from flask import current_app, request, url_for
from werkzeug.exceptions import HTTPException
from werkzeug.routing import RoutingException


# Endpoints that may act as a breadcrumb origin, mapped to their display label.
# Only overview/landing pages appear here - detail/edit pages are never origins.
ORIGIN_LABELS = {
    'dashboard.index': 'Dashboard',
    'dashboard.team_overview': 'Team-Übersicht',
    'absences.list_absences': 'Abwesenheiten',
    'absences.calendar': 'Kalender',
    'users.list_users': 'Mitarbeitende',
    'categories.list_categories': 'Kategorien',
}


# Maps a user's stored start_page value to the endpoint shown after login.
START_PAGE_ENDPOINTS = {
    'dashboard': 'dashboard.index',
    'calendar': 'absences.calendar',
    'team': 'dashboard.team_overview',
    'list': 'absences.list_absences',
}


def _endpoint_for_path(path: str | None) -> str | None:
    """Resolve an internal GET path to its endpoint, or None.

    Rejects external and protocol-relative targets and any path that does not
    match a real route, so only genuine internal routes pass.
    """
    if not path or not path.startswith('/') or path.startswith('//'):
        return None
    # urlsplit strips tab and newline, as browsers do. The caller forwards the
    # raw value, so '/\t/host/' would pass the prefix check here and turn into
    # a protocol-relative '//host/' in the browser.
    if any(char in path for char in '\t\r\n'):
        return None
    route = urlsplit(path).path
    try:
        adapter = current_app.url_map.bind('localhost')
        endpoint, _ = adapter.match(route, method='GET')
        return endpoint
    except (HTTPException, RoutingException):
        return None


def _current_ref() -> str:
    """Return the current request's full path (with query), without a trailing '?'."""
    path = request.full_path
    return path[:-1] if path.endswith('?') else path


def _valid_origin_ref() -> str | None:
    """Return the ref to forward, or None.

    Prefers an existing valid origin, falling back to the current page when
    that is itself an origin.
    """
    current = request.args.get('ref')
    if current and _endpoint_for_path(current) in ORIGIN_LABELS:
        return current

    here = _current_ref()
    if _endpoint_for_path(here) in ORIGIN_LABELS:
        return here

    return None


def resolve_origin() -> dict | None:
    """Return ``{'url', 'label'}`` for a valid origin ref, or None.

    Used to render the first breadcrumb level. Returns None on direct access
    (no ref) so callers omit the breadcrumb entirely rather than inventing one.
    """
    ref = request.args.get('ref')
    if not ref:
        return None
    endpoint = _endpoint_for_path(ref)
    if endpoint in ORIGIN_LABELS:
        return {'url': ref, 'label': ORIGIN_LABELS[endpoint]}
    return None


def origin_link(endpoint: str, **values) -> str:
    """Build a forward URL that carries the origin along.

    The ``ref`` keeps the return target across detail/edit chains; without
    a valid origin the URL stays plain.
    """
    ref = _valid_origin_ref()
    if ref:
        return url_for(endpoint, ref=ref, **values)
    return url_for(endpoint, **values)


def back_url(default_endpoint: str, **values) -> str:
    """Return the URL to jump back to.

    The origin's URL when one is known, otherwise the default target.
    """
    origin = resolve_origin()
    if origin:
        return origin['url']
    return url_for(default_endpoint, **values)


def back_url_focused(default_endpoint: str, row_id: int, **values) -> str:
    """Return the back URL with the given overview row reopened and scrolled to.

    Overview rows are collapsible, so a plain return leaves the row the user
    just worked on collapsed and the page scrolled to the top.
    """
    parts = urlsplit(back_url(default_endpoint, **values))
    # The origin may already carry an 'open' from an earlier return; appending
    # a second one would leave the previously edited row expanded instead.
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != 'open']
    query.append(('open', str(row_id)))
    return urlunsplit((
        parts.scheme, parts.netloc, parts.path,
        urlencode(query), f'row-{row_id}'
    ))


def is_safe_redirect_url(target: str | None) -> bool:
    """Validate redirect URL to prevent open redirect attacks.

    Args:
        target: URL to validate.

    Returns:
        True if URL is safe (same host), False otherwise.
    """
    if not target:
        return False

    # Browsers fold backslashes to slashes, turning '/\evil.com' into a
    # protocol-relative redirect the netloc check below misses.
    if '\\' in target:
        return False

    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))

    return (
        test_url.scheme in ('http', 'https') and
        ref_url.netloc == test_url.netloc
    )


def start_page_url(start_page: str | None) -> str:
    """Return the post-login landing URL for a user's start_page preference.

    Falls back to the dashboard for an unknown or missing value, so a stale
    preference can never break the login redirect.
    """
    endpoint = START_PAGE_ENDPOINTS.get(start_page, 'dashboard.index')
    return url_for(endpoint)
