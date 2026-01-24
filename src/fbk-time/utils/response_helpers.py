"""Response helper utilities for AJAX endpoints."""

from flask import jsonify


def ajax_response(success=True, message=None, redirect=None, errors=None, **kwargs):
    """Create standardized JSON response for AJAX requests.

    Args:
        success: Whether the operation was successful.
        message: Success or error message to display.
        redirect: URL to redirect to after success.
        errors: Dict of field-specific validation errors.
        **kwargs: Additional data to include in response.

    Returns:
        Flask JSON response with appropriate status code.
    """
    data = {'success': success}

    if message:
        if success:
            data['message'] = message
        else:
            data['error'] = message

    if redirect:
        data['redirect'] = redirect

    if errors:
        data['errors'] = errors

    data.update(kwargs)

    status_code = 200 if success else 400
    return jsonify(data), status_code
