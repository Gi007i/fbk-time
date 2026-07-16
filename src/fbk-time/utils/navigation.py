"""Origin-based navigation for breadcrumbs and return links.

The current page's address is carried forward via a ``ref`` query parameter
(stateless, tab-safe) instead of server-side session state. Only real internal
overview routes are accepted as an origin; anything else is discarded, which
also prevents open redirects.
"""

from urllib.parse import urljoin, urlparse, urlsplit

from flask import current_app, request, url_for


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


def _endpoint_for_path(path: str | None) -> str | None:
    """Resolve an internal GET path to its endpoint, or None.

    Rejects external and protocol-relative targets and any path that does not
    match a real route, so only genuine internal routes pass.
    """
    if not path or not path.startswith('/') or path.startswith('//'):
        return None
    route = urlsplit(path).path
    try:
        adapter = current_app.url_map.bind('localhost')
        endpoint, _ = adapter.match(route, method='GET')
        return endpoint
    except Exception:
        return None


def _current_ref() -> str:
    """Return the current request's full path (with query), without a trailing '?'."""
    path = request.full_path
    return path[:-1] if path.endswith('?') else path


def _valid_origin_ref() -> str | None:
    """Return the ref to forward: an existing valid origin, else the current
    page when it is itself an origin, else None."""
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

    Appends the forwarded ``ref`` when a valid origin exists, so the return
    target survives navigation across detail/edit chains. Emits a plain URL
    when there is no origin to carry (direct access).
    """
    ref = _valid_origin_ref()
    if ref:
        return url_for(endpoint, ref=ref, **values)
    return url_for(endpoint, **values)


def back_url(default_endpoint: str, **values) -> str:
    """Return the URL to jump back to.

    The origin's URL when one is known (one-click return to where the user
    came from), otherwise the given default target. Used for cancel buttons
    and post-save/delete redirects.
    """
    origin = resolve_origin()
    if origin:
        return origin['url']
    return url_for(default_endpoint, **values)


def is_safe_redirect_url(target: str | None) -> bool:
    """Validate redirect URL to prevent open redirect attacks.

    Args:
        target: URL to validate.

    Returns:
        True if URL is safe (same host), False otherwise.
    """
    if not target:
        return False

    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))

    return (
        test_url.scheme in ('http', 'https') and
        ref_url.netloc == test_url.netloc
    )
