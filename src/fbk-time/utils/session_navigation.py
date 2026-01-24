"""Session-based navigation for return URL management and AJAX detection."""

from flask import request, session, url_for


def is_ajax_request():
    """Check if the current request is an AJAX request.

    Returns:
        bool: True if request has X-Requested-With: XMLHttpRequest header.
    """
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def save_return_url(label):
    """Save current request URL and label as return target in session.

    Only saves for GET requests. Called by list views to remember
    the current page URL including all filter parameters.

    Args:
        label: Display label for breadcrumb navigation (e.g., 'Liste', 'Kalender').

    Security: Only saves internal URLs (validated by Flask's request.url).
    """
    if request.method == 'GET':
        session['return_to'] = {
            'url': request.url,
            'label': label
        }


def get_return_url(default_endpoint):
    """Get return URL from session for redirects.

    Retrieves the saved return URL without clearing it (peek).
    Use clear_return_url() after successful redirect if needed.

    Args:
        default_endpoint: Flask endpoint name for fallback.

    Returns:
        str: Validated internal URL or default endpoint URL.

    Security: Validates URL starts with request.host_url to prevent open redirect.
    """
    return_info = session.get('return_to')

    if return_info and isinstance(return_info, dict):
        url = return_info.get('url')
        if url and url.startswith(request.host_url):
            return url

    return url_for(default_endpoint)


def get_return_info(default_endpoint, default_label):
    """Get return URL and label from session for breadcrumbs.

    Args:
        default_endpoint: Flask endpoint name for fallback URL.
        default_label: Display label for fallback.

    Returns:
        dict: {'url': str, 'label': str} for breadcrumb navigation.

    Security: Validates URL starts with request.host_url to prevent open redirect.
    """
    return_info = session.get('return_to')

    if return_info and isinstance(return_info, dict):
        url = return_info.get('url')
        label = return_info.get('label')
        if url and url.startswith(request.host_url) and label:
            return {'url': url, 'label': label}

    return {'url': url_for(default_endpoint), 'label': default_label}


def clear_return_url():
    """Clear return URL from session after use."""
    session.pop('return_to', None)
