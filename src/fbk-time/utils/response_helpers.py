"""Response helper utilities for AJAX and API endpoints."""

from typing import Any, Optional

from flask import jsonify


def ajax_response(
    success: bool = True,
    message: Optional[str] = None,
    redirect: Optional[str] = None,
    errors: Optional[dict] = None,
    status_code: Optional[int] = None,
    **kwargs
):
    """Create standardized JSON response for AJAX form submissions.

    Response format:
        Success: {"success": true, "message": "...", "redirect": "/url", ...}
        Error:   {"success": false, "error": "...", "errors": {...}}

    Args:
        success: Whether the operation was successful.
        message: Success or error message to display.
        redirect: URL to redirect to after success.
        errors: Dict of field-specific validation errors.
        status_code: Override default status code (200 for success, 400 for error).
        **kwargs: Additional data to include in response.

    Returns:
        Flask JSON response tuple (response, status_code).
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

    if status_code is None:
        status_code = 200 if success else 400

    return jsonify(data), status_code


def api_success(
    data: Optional[Any] = None,
    message: Optional[str] = None,
    status_code: int = 200,
    **kwargs
):
    """Create standardized success response for API endpoints.

    Response format:
        {"success": true, "data": {...}, "message": "..."}

    Args:
        data: Response payload (dict, list, or primitive).
        message: Optional success message.
        status_code: HTTP status code (default 200).
        **kwargs: Additional fields to include in response.

    Returns:
        Flask JSON response tuple (response, status_code).
    """
    response = {'success': True}

    if data is not None:
        response['data'] = data

    if message:
        response['message'] = message

    response.update(kwargs)

    return jsonify(response), status_code


def api_error(
    message: str,
    status_code: int = 400,
    errors: Optional[dict] = None,
    **kwargs
):
    """Create standardized error response for API endpoints.

    Response format:
        {"success": false, "error": "Error message", "errors": {...}}

    Args:
        message: Error message describing the problem.
        status_code: HTTP status code (default 400).
        errors: Optional dict of field-specific errors.
        **kwargs: Additional fields to include in response.

    Returns:
        Flask JSON response tuple (response, status_code).
    """
    response = {
        'success': False,
        'error': message
    }

    if errors:
        response['errors'] = errors

    response.update(kwargs)

    return jsonify(response), status_code
