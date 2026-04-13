"""Dashboard services.

Provides business logic for dashboard statistics, warnings,
and team overview data preparation.
"""

from datetime import date, timedelta
from typing import Optional

from sqlalchemy import or_

from modules.absence.models import Absence
from modules.absence.recurrence import recurrence_service
from modules.auth.models import User, UserRole, UserStatus
from modules.category.models import Category
from modules.holidays.services import is_holiday


# SQLAlchemy expression: users whose status allows dashboard visibility.
# MANAGED users are included because an admin may track their absences
# even though they cannot log in themselves.
_ACTIVE_USER_STATUS_FILTER = User.status.in_(
    [UserStatus.ACTIVE, UserStatus.MANAGED]
)


def get_today_absences() -> tuple[list[dict], list[dict]]:
    """Get today's expanded occurrences split by presence status.

    Uses recurrence_service to correctly resolve recurring absences.

    Returns:
        Tuple of (absent_occurrences, present_occurrences).
    """
    today = date.today()

    absences = Absence.query.join(
        User, Absence.user_id == User.id
    ).join(Category).filter(
        _ACTIVE_USER_STATUS_FILTER,
        User.role == UserRole.USER,
        Category.active == True,
        or_(
            (Absence.is_recurring == False) & (Absence.start_date <= today) & (Absence.end_date >= today),
            (Absence.is_recurring == True) & (Absence.start_date <= today) & (
                (Absence.recurrence_end_date >= today) | (Absence.recurrence_end_date.is_(None))
            )
        )
    ).all()

    occurrences = recurrence_service.get_all_occurrences_for_range(
        absences, today, today
    )

    today_absent = [
        o for o in occurrences
        if o.get('category') is not None and not o['category'].is_present
    ]
    today_present = [
        o for o in occurrences
        if o.get('category') is not None and o['category'].is_present
    ]

    return today_absent, today_present


def get_week_absences() -> list[dict]:
    """Get expanded daily occurrences for current week.

    Expands multi-day and recurring absences into individual
    daily entries using recurrence_service.

    Returns:
        List of occurrence dicts sorted by date, then user name.
    """
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    absences = Absence.query.join(
        User, Absence.user_id == User.id
    ).join(Category).filter(
        _ACTIVE_USER_STATUS_FILTER,
        User.role == UserRole.USER,
        Category.active == True,
        or_(
            (Absence.is_recurring == False) & (Absence.start_date <= week_end) & (Absence.end_date >= week_start),
            (Absence.is_recurring == True) & (Absence.start_date <= week_end) & (
                (Absence.recurrence_end_date >= week_start) | (Absence.recurrence_end_date.is_(None))
            )
        )
    ).all()

    occurrences = recurrence_service.get_all_occurrences_for_range(
        absences, week_start, week_end
    )
    occurrences.sort(key=lambda o: (o['date'], o['user'].name))

    return occurrences


def get_dashboard_warnings() -> list[dict]:
    """Generate warning messages for dashboard.

    Operates entirely on expanded occurrences (effective, exception-merged
    state) so that modified occurrences of recurring series are evaluated
    correctly.

    Checks for:
    - Missing substitutes for occurrences whose effective category requires one
    - Substitute conflicts (substitute is also absent on that date)
    - Double substitute assignments on the same date
    - Cross-substitution within overlapping date sets

    Returns:
        List of warning dicts with type and details.
    """
    today = date.today()
    max_range_end = today + timedelta(days=365)
    warnings = []

    future_filter = or_(
        (Absence.is_recurring == False) & (Absence.end_date >= today),
        (Absence.is_recurring == True) & (
            (Absence.recurrence_end_date >= today) | (Absence.recurrence_end_date.is_(None))
        )
    )

    all_absences = Absence.query.join(
        User, Absence.user_id == User.id
    ).join(Category).filter(
        _ACTIVE_USER_STATUS_FILTER,
        User.role == UserRole.USER,
        Category.active == True,
        future_filter
    ).all()

    all_occurrences = recurrence_service.get_all_occurrences_for_range(
        all_absences, today, max_range_end
    )

    # Lookup: user_id -> set of dates where genuinely absent (effective state)
    user_absence_dates = {}
    for occ in all_occurrences:
        category = occ.get('category')
        if category is None or category.is_present:
            continue
        user_absence_dates.setdefault(occ['user_id'], set()).add(occ['date'])

    # Lookup: substitute_id -> list of assignments
    substitute_assignments = {}
    for occ in all_occurrences:
        sub_id = occ.get('substitute_id')
        if not sub_id:
            continue
        substitute_assignments.setdefault(sub_id, []).append({
            'date': occ['date'],
            'absence_id': occ['absence'].id,
            'user_id': occ['user_id'],
            'user_name': occ['user'].name
        })

    reported_missing = set()
    conflict_warnings = {}
    reported_doubles = set()
    cross_warnings = {}

    for occ in all_occurrences:
        absence = occ['absence']
        category = occ.get('category')
        if category is None:
            continue
        sub_id = occ.get('substitute_id')

        # Missing substitute for a category that requires one
        if category.requires_substitute and not sub_id:
            key = (absence.id, occ['date']) if occ.get('is_exception') else absence.id
            if key not in reported_missing:
                reported_missing.add(key)
                warnings.append({
                    'type': 'missing_substitute',
                    'user': occ['user'].name,
                    'category': category.name,
                    'absence_id': absence.id
                })

        if not sub_id or category.is_present:
            continue

        substitute = occ.get('substitute')
        if substitute is None:
            continue
        substitute_name = substitute.name

        # Substitute conflict: substitute is absent on the same date.
        # Aggregate additional conflict dates for the same (absence, sub)
        # pair into the existing warning so the template can show
        # "+X weitere" instead of always reporting one date.
        if occ['date'] in user_absence_dates.get(sub_id, set()):
            key = (absence.id, sub_id)
            warning = conflict_warnings.get(key)
            if warning is None:
                warning = {
                    'type': 'substitute_conflict',
                    'user': occ['user'].name,
                    'substitute': substitute_name,
                    'conflict_dates': [occ['date']],
                    'conflict_count': 1,
                    'absence_id': absence.id
                }
                conflict_warnings[key] = warning
                warnings.append(warning)
            elif occ['date'] not in warning['conflict_dates']:
                if len(warning['conflict_dates']) < 3:
                    warning['conflict_dates'].append(occ['date'])
                warning['conflict_count'] += 1

        # Double assignment: same substitute assigned to another person same date
        for assignment in substitute_assignments.get(sub_id, []):
            if assignment['absence_id'] == absence.id:
                continue
            if assignment['date'] != occ['date']:
                continue
            pair_key = tuple(sorted([absence.id, assignment['absence_id']]))
            if pair_key in reported_doubles:
                continue
            reported_doubles.add(pair_key)
            warnings.append({
                'type': 'substitute_double_assignment',
                'user': occ['user'].name,
                'substitute': substitute_name,
                'other_user': assignment['user_name'],
                'conflict_date': occ['date'],
                'absence_id': absence.id
            })
            break

        # Cross-substitution: sub_id covers user, and user covers sub_id
        # same date. Aggregate overlap dates per absence pair so that a
        # weekly cross-sub series reports all its collision days.
        reverse_assignments = substitute_assignments.get(occ['user_id'], [])
        for reverse in reverse_assignments:
            if reverse['user_id'] != sub_id:
                continue
            if reverse['date'] != occ['date']:
                continue
            pair_key = tuple(sorted([absence.id, reverse['absence_id']]))
            warning = cross_warnings.get(pair_key)
            if warning is None:
                warning = {
                    'type': 'cross_substitution',
                    'user': occ['user'].name,
                    'substitute': substitute_name,
                    'overlap_dates': [occ['date']],
                    'overlap_count': 1,
                    'absence_id': absence.id
                }
                cross_warnings[pair_key] = warning
                warnings.append(warning)
            elif occ['date'] not in warning['overlap_dates']:
                if len(warning['overlap_dates']) < 3:
                    warning['overlap_dates'].append(occ['date'])
                warning['overlap_count'] += 1
            break

    return warnings


def get_today_holiday() -> Optional[str]:
    """Get holiday name if today is a holiday.

    Returns:
        Holiday name or None.
    """
    today = date.today()
    is_today_holiday, holiday_name = is_holiday(today)
    return holiday_name if is_today_holiday else None


def build_team_matrix(
    users: list,
    absences: list,
    range_start: date,
    range_end: date
) -> dict:
    """Build absence matrix for team overview.

    Args:
        users: List of users.
        absences: List of absences in range.
        range_start: Start date of range.
        range_end: End date of range.

    Returns:
        Dict mapping (user_id, date) to absence info.
    """
    expanded_occurrences = recurrence_service.get_all_occurrences_for_range(
        absences, range_start, range_end
    )

    matrix = {}
    for occ in expanded_occurrences:
        key = (occ['user_id'], occ['date'])
        entry = {
            'absence': occ['absence'],
            'category': occ['category'],
            'is_half_day_morning': occ['is_half_day_morning'],
            'is_half_day_afternoon': occ['is_half_day_afternoon'],
            'is_recurring': occ['is_recurring'],
            'is_combined_half_day': False,
            'absence_afternoon': None,
            'category_afternoon': None,
            'is_recurring_afternoon': False
        }

        if key in matrix:
            existing = matrix[key]
            if existing.get('is_combined_half_day'):
                continue
            if existing['is_half_day_morning'] and occ['is_half_day_afternoon']:
                existing['is_half_day_afternoon'] = True
                existing['is_combined_half_day'] = True
                existing['absence_afternoon'] = occ['absence']
                existing['category_afternoon'] = occ['category']
                existing['is_recurring_afternoon'] = occ['is_recurring']
                continue
            if existing['is_half_day_afternoon'] and occ['is_half_day_morning']:
                existing['is_half_day_morning'] = True
                existing['is_combined_half_day'] = True
                existing['absence_afternoon'] = existing['absence']
                existing['category_afternoon'] = existing['category']
                existing['is_recurring_afternoon'] = existing['is_recurring']
                existing['absence'] = occ['absence']
                existing['category'] = occ['category']
                existing['is_recurring'] = occ['is_recurring']
                continue

        matrix[key] = entry

    return matrix


def get_team_overview_data(
    year: int,
    month: int,
    week_start: date,
    week_end: date
) -> dict:
    """Get all data for team overview page.

    Args:
        year: Year for month view.
        month: Month for month view.
        week_start: Start of week for week view.
        week_end: End of week for week view.

    Returns:
        Dict with users, categories, absences, matrix.
    """
    month_start = date(year, month, 1)
    if month == 12:
        month_end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        month_end = date(year, month + 1, 1) - timedelta(days=1)

    users = User.query.filter(
        _ACTIVE_USER_STATUS_FILTER,
        User.role == UserRole.USER
    ).order_by(User.name).all()

    categories = Category.query.order_by(Category.sort_order).all()

    range_start = min(week_start, month_start)
    range_end = max(week_end, month_end)

    absences = Absence.query.join(
        User, Absence.user_id == User.id
    ).join(Category).filter(
        _ACTIVE_USER_STATUS_FILTER,
        User.role == UserRole.USER,
        Category.active == True,
        or_(
            (Absence.is_recurring == False) & (Absence.start_date <= range_end) & (Absence.end_date >= range_start),
            (Absence.is_recurring == True) & (Absence.start_date <= range_end) & (
                (Absence.recurrence_end_date >= range_start) | (Absence.recurrence_end_date.is_(None))
            )
        )
    ).all()

    matrix = build_team_matrix(users, absences, range_start, range_end)

    return {
        'users': users,
        'categories': categories,
        'matrix': matrix,
        'month_start': month_start,
        'month_end': month_end
    }
