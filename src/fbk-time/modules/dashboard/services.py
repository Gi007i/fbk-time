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


def get_today_absences() -> tuple[list, list]:
    """Get today's absences split by presence status.

    Returns:
        Tuple of (absent_list, present_list).
    """
    today = date.today()
    user_status_filter = User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED])

    today_all = Absence.query.join(
        User, Absence.user_id == User.id
    ).join(Category).filter(
        user_status_filter,
        User.role == UserRole.USER,
        Category.active == True,
        Absence.start_date <= today,
        Absence.end_date >= today
    ).all()

    today_absent = [a for a in today_all if not a.category.is_present]
    today_present = [a for a in today_all if a.category.is_present]

    return today_absent, today_present


def get_week_absences() -> list:
    """Get absences for current week.

    Returns:
        List of absences in current week.
    """
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    user_status_filter = User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED])

    return Absence.query.join(
        User, Absence.user_id == User.id
    ).join(Category).filter(
        user_status_filter,
        User.role == UserRole.USER,
        Category.active == True,
        Absence.start_date <= week_end,
        Absence.end_date >= week_start
    ).order_by(Absence.start_date).all()


def get_dashboard_warnings() -> list[dict]:
    """Generate warning messages for dashboard.

    Checks for:
    - Missing substitutes for categories that require them
    - Substitute conflicts (substitute is also absent)
    - Double substitute assignments
    - Cross-substitution

    Uses recurrence_service.get_all_occurrences_for_range() for expansion.

    Returns:
        List of warning dicts with type and details.
    """
    today = date.today()
    max_range_end = today + timedelta(days=365)
    warnings = []
    user_status_filter = User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED])

    # Query for future absences (handles recurring via or_ filter)
    future_filter = or_(
        (Absence.is_recurring == False) & (Absence.end_date >= today),
        (Absence.is_recurring == True) & (
            (Absence.recurrence_end_date >= today) | (Absence.recurrence_end_date.is_(None))
        )
    )

    # Missing substitutes
    absences_needing_substitute = Absence.query.join(
        User, Absence.user_id == User.id
    ).join(Category).filter(
        user_status_filter,
        User.role == UserRole.USER,
        Category.active == True,
        Category.requires_substitute == True,
        Absence.substitute_id.is_(None),
        future_filter
    ).all()

    for absence in absences_needing_substitute:
        warnings.append({
            'type': 'missing_substitute',
            'user': absence.user.name,
            'category': absence.category.name,
            'absence_id': absence.id
        })

    # Load all future absences for conflict detection
    all_absences = Absence.query.join(
        User, Absence.user_id == User.id
    ).join(Category).filter(
        user_status_filter,
        User.role == UserRole.USER,
        Category.active == True,
        future_filter
    ).all()

    # Expand all absences using established pattern
    all_occurrences = recurrence_service.get_all_occurrences_for_range(
        all_absences, today, max_range_end
    )

    # Build lookup: user_id -> set of dates
    user_absence_dates = {}
    for occ in all_occurrences:
        user_id = occ['user_id']
        if user_id not in user_absence_dates:
            user_absence_dates[user_id] = set()
        user_absence_dates[user_id].add(occ['date'])

    # Build lookup: substitute_id -> list of (date, absence)
    substitute_assignments = {}
    for occ in all_occurrences:
        absence = occ['absence']
        if not absence.substitute_id:
            continue
        if absence.substitute_id not in substitute_assignments:
            substitute_assignments[absence.substitute_id] = []
        substitute_assignments[absence.substitute_id].append((occ['date'], absence))

    # Build lookup: absence_id -> set of dates
    absence_dates_map = {}
    for occ in all_occurrences:
        absence_id = occ['absence'].id
        if absence_id not in absence_dates_map:
            absence_dates_map[absence_id] = set()
        absence_dates_map[absence_id].add(occ['date'])

    # Check absences with substitutes
    absences_with_substitute = [a for a in all_absences if a.substitute_id]
    checked_substitute_conflicts = set()
    checked_double_assignments = set()
    checked_cross_pairs = set()

    for absence in absences_with_substitute:
        absence_dates = absence_dates_map.get(absence.id, set())
        if not absence_dates:
            continue

        # Substitute conflict: substitute is absent on same dates
        substitute_absent_dates = user_absence_dates.get(absence.substitute_id, set())
        conflict_dates = absence_dates & substitute_absent_dates

        if conflict_dates:
            conflict_key = (absence.id, absence.substitute_id)
            if conflict_key not in checked_substitute_conflicts:
                checked_substitute_conflicts.add(conflict_key)
                sorted_dates = sorted(conflict_dates)
                warnings.append({
                    'type': 'substitute_conflict',
                    'user': absence.user.name,
                    'substitute': absence.substitute.name,
                    'conflict_dates': sorted_dates[:3],
                    'conflict_count': len(sorted_dates),
                    'absence_id': absence.id
                })

        # Double assignment: substitute assigned to multiple people
        other_assignments = substitute_assignments.get(absence.substitute_id, [])
        for assign_date, other_absence in other_assignments:
            if other_absence.id == absence.id or assign_date not in absence_dates:
                continue

            pair_key = tuple(sorted([absence.id, other_absence.id]))
            if pair_key not in checked_double_assignments:
                checked_double_assignments.add(pair_key)
                warnings.append({
                    'type': 'substitute_double_assignment',
                    'user': absence.user.name,
                    'substitute': absence.substitute.name,
                    'other_user': other_absence.user.name,
                    'conflict_date': assign_date,
                    'absence_id': absence.id
                })
                break

        # Cross-substitution
        for other in absences_with_substitute:
            if other.user_id != absence.substitute_id or other.substitute_id != absence.user_id:
                continue
            if other.id == absence.id:
                continue

            pair_key = tuple(sorted([absence.id, other.id]))
            if pair_key in checked_cross_pairs:
                continue

            other_dates = absence_dates_map.get(other.id, set())
            overlap_dates = absence_dates & other_dates

            if overlap_dates:
                checked_cross_pairs.add(pair_key)
                sorted_overlap = sorted(overlap_dates)
                warnings.append({
                    'type': 'cross_substitution',
                    'user': absence.user.name,
                    'substitute': absence.substitute.name,
                    'overlap_dates': sorted_overlap[:3],
                    'overlap_count': len(sorted_overlap),
                    'absence_id': absence.id
                })

    return warnings


def get_dashboard_stats() -> dict:
    """Calculate dashboard statistics.

    Returns:
        Dict with active_users, active_percentage, today_absent,
        today_present, month_absent, month_present.
    """
    today = date.today()
    user_status_filter = User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED])

    manageable_users = User.query.filter(
        user_status_filter,
        User.role == UserRole.USER
    ).count()
    total_users = User.query.filter_by(role=UserRole.USER).count()
    active_percentage = round((manageable_users / total_users) * 100) if total_users > 0 else 0

    today_absent, today_present = get_today_absences()

    month_start = date(today.year, today.month, 1)
    if today.month == 12:
        month_end = date(today.year + 1, 1, 1) - timedelta(days=1)
    else:
        month_end = date(today.year, today.month + 1, 1) - timedelta(days=1)

    this_month_absent = Absence.query.join(
        User, Absence.user_id == User.id
    ).join(Category).filter(
        user_status_filter,
        User.role == UserRole.USER,
        Category.active == True,
        Category.is_present == False,
        Absence.start_date <= month_end,
        Absence.end_date >= month_start
    ).count()

    this_month_present = Absence.query.join(
        User, Absence.user_id == User.id
    ).join(Category).filter(
        user_status_filter,
        User.role == UserRole.USER,
        Category.active == True,
        Category.is_present == True,
        Absence.start_date <= month_end,
        Absence.end_date >= month_start
    ).count()

    return {
        'active_users': manageable_users,
        'active_percentage': active_percentage,
        'today_absent': len(today_absent),
        'today_present': len(today_present),
        'month_absent': this_month_absent,
        'month_present': this_month_present
    }


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
        matrix[key] = {
            'absence': occ['absence'],
            'category': occ['category'],
            'is_half_day_morning': occ['is_half_day_morning'],
            'is_half_day_afternoon': occ['is_half_day_afternoon'],
            'is_recurring': occ['is_recurring']
        }

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

    user_status_filter = User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED])

    users = User.query.filter(
        user_status_filter,
        User.role == UserRole.USER
    ).order_by(User.name).all()

    categories = Category.query.order_by(Category.sort_order).all()

    range_start = min(week_start, month_start)
    range_end = max(week_end, month_end)

    absences = Absence.query.join(
        User, Absence.user_id == User.id
    ).join(Category).filter(
        user_status_filter,
        User.role == UserRole.USER,
        Category.active == True,
        or_(
            (Absence.is_recurring == False) & (Absence.start_date <= range_end) & (Absence.end_date >= range_start),
            (Absence.is_recurring == True) & (Absence.start_date <= range_end) & (
                (Absence.recurrence_end_date >= range_start) | (Absence.recurrence_end_date == None)
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
