"""Pagination utilities.

Provides standardized pagination for list views with fail-fast validation.
"""

from dataclasses import dataclass
from typing import Optional, Tuple

from flask import request, redirect, url_for, abort
from flask_login import current_user


@dataclass
class PaginationResult:
    """Pagination calculation result.

    Attributes:
        page: Current page number (1-indexed).
        per_page: Items per page (0 = show all).
        total: Total number of items.
        total_pages: Total number of pages.
        has_prev: Whether previous page exists.
        has_next: Whether next page exists.
        offset: SQL offset for current page.
        limit: SQL limit for current page.
    """

    page: int
    per_page: int
    total: int
    total_pages: int
    has_prev: bool
    has_next: bool
    offset: int
    limit: int

    def to_dict(self) -> dict:
        """Convert to dict for template context."""
        return {
            'page': self.page,
            'per_page': self.per_page,
            'total': self.total,
            'total_pages': self.total_pages,
            'has_prev': self.has_prev,
            'has_next': self.has_next
        }


def get_pagination(
    total: int,
    endpoint: str,
    per_page: Optional[int] = None,
    **endpoint_kwargs
) -> Tuple[PaginationResult, Optional[any]]:
    """Calculate pagination and validate page parameter.

    Uses current_user.items_per_page if per_page not specified.
    When per_page is 0, all items are shown on a single page.

    Args:
        total: Total number of items.
        endpoint: Flask endpoint name for redirect URL construction.
        per_page: Items per page (None = use user preference, 0 = show all).
        **endpoint_kwargs: Additional kwargs for url_for when building redirect.

    Returns:
        Tuple of (PaginationResult, redirect_response or None).
        If redirect_response is not None, caller should return it immediately.

    Raises:
        abort(400) on invalid page number.
    """
    if per_page is None:
        per_page = current_user.items_per_page

    # per_page == 0 means show all (no pagination)
    if per_page == 0:
        return PaginationResult(
            page=1,
            per_page=0,
            total=total,
            total_pages=1,
            has_prev=False,
            has_next=False,
            offset=0,
            limit=total if total > 0 else 1
        ), None

    total_pages = (total + per_page - 1) // per_page if total > 0 else 1

    page_str = request.args.get('page')
    if page_str is None:
        page = 1
    else:
        try:
            page = int(page_str)
        except ValueError:
            abort(400, 'Invalid page number')

        if page < 1:
            abort(400, 'Invalid page number')

    # Redirect to last valid page if current page exceeds total
    if page > total_pages:
        args = request.args.to_dict()
        args['page'] = str(total_pages)
        args.update(endpoint_kwargs)
        return None, redirect(url_for(endpoint, **args))

    offset = (page - 1) * per_page

    return PaginationResult(
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
        has_prev=page > 1,
        has_next=page < total_pages,
        offset=offset,
        limit=per_page
    ), None


def paginate_list(
    items: list,
    endpoint: str,
    per_page: Optional[int] = None,
    **endpoint_kwargs
) -> Tuple[list, PaginationResult, Optional[any]]:
    """Paginate a Python list and return the slice for current page.

    Args:
        items: Full list of items to paginate.
        endpoint: Flask endpoint name for redirect URL construction.
        per_page: Items per page (None = use user preference).
        **endpoint_kwargs: Additional kwargs for url_for.

    Returns:
        Tuple of (paginated_items, PaginationResult, redirect_response or None).
        If redirect_response is not None, caller should return it immediately.
    """
    total = len(items)
    pagination, redirect_response = get_pagination(
        total, endpoint, per_page, **endpoint_kwargs
    )

    if redirect_response is not None:
        return [], pagination, redirect_response

    if pagination.per_page == 0:
        return items, pagination, None

    start = pagination.offset
    end = start + pagination.limit
    paginated_items = items[start:end]

    return paginated_items, pagination, None
