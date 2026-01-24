"""Dashboard views.

Provides the main dashboard view with widgets and team overview.
"""

from datetime import date, timedelta
from calendar import monthrange
from flask import Blueprint, render_template, request, abort
from flask_login import login_required
from sqlalchemy import or_

from utils.session_navigation import save_return_url
from utils.helpers import format_date_for_user
from modules.absence.models import Absence
from modules.auth.models import User, UserRole, UserStatus
from modules.category.models import Category
from modules.holidays.services import get_holidays_for_month, is_holiday
from modules.absence.recurrence import recurrence_service

bp = Blueprint('dashboard', __name__)


@bp.before_request
@login_required
def require_login():
    """Require login for all dashboard routes."""
    pass


@bp.route('/')
def index():
    """Display main dashboard with widgets."""
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

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

    week_absences = Absence.query.join(
        User, Absence.user_id == User.id
    ).join(Category).filter(
        user_status_filter,
        User.role == UserRole.USER,
        Category.active == True,
        Absence.start_date <= week_end,
        Absence.end_date >= week_start
    ).order_by(Absence.start_date).all()

    warnings = []
    absences_needing_substitute = Absence.query.join(
        User, Absence.user_id == User.id
    ).join(Category).filter(
        user_status_filter,
        User.role == UserRole.USER,
        Category.active == True,
        Category.requires_substitute == True,
        Absence.substitute_id.is_(None),
        Absence.end_date >= today
    ).all()

    for absence in absences_needing_substitute:
        warnings.append({
            'type': 'missing_substitute',
            'user': absence.user.name,
            'category': absence.category.name,
            'absence_id': absence.id
        })

    all_future_absences = Absence.query.join(
        User, Absence.user_id == User.id
    ).join(Category).filter(
        user_status_filter,
        User.role == UserRole.USER,
        Category.active == True,
        Absence.end_date >= today
    ).order_by(Absence.user_id, Absence.start_date).all()

    checked_pairs = set()
    for absence in all_future_absences:
        overlapping = [
            other for other in all_future_absences
            if other.user_id == absence.user_id
            and other.id != absence.id
            and other.start_date <= absence.end_date
            and other.end_date >= absence.start_date
        ]

        for conflict in overlapping:
            pair_key = tuple(sorted([absence.id, conflict.id]))
            if pair_key not in checked_pairs:
                checked_pairs.add(pair_key)
                warnings.append({
                    'type': 'user_overlap',
                    'user': absence.user.name,
                    'absence1_category': absence.category.name,
                    'absence1_start': absence.start_date,
                    'absence1_end': absence.end_date,
                    'absence2_category': conflict.category.name,
                    'absence2_start': conflict.start_date,
                    'absence2_end': conflict.end_date,
                    'absence_id': absence.id
                })

    future_absences = Absence.query.join(
        User, Absence.user_id == User.id
    ).join(Category).filter(
        user_status_filter,
        User.role == UserRole.USER,
        Category.active == True,
        Absence.end_date >= today,
        Absence.substitute_id.isnot(None)
    ).all()

    for absence in future_absences:
        substitute_conflicts = Absence.query.filter(
            Absence.user_id == absence.substitute_id,
            Absence.start_date <= absence.end_date,
            Absence.end_date >= absence.start_date,
            Absence.id != absence.id
        ).all()

        for conflict in substitute_conflicts:
            warnings.append({
                'type': 'substitute_conflict',
                'user': absence.user.name,
                'substitute': absence.substitute.name,
                'conflict_start': conflict.start_date,
                'conflict_end': conflict.end_date,
                'absence_id': absence.id
            })

        other_assignments = Absence.query.filter(
            Absence.substitute_id == absence.substitute_id,
            Absence.start_date <= absence.end_date,
            Absence.end_date >= absence.start_date,
            Absence.id != absence.id
        ).all()

        for other in other_assignments:
            warnings.append({
                'type': 'substitute_double_assignment',
                'user': absence.user.name,
                'substitute': absence.substitute.name,
                'other_user': other.user.name,
                'other_start': other.start_date,
                'other_end': other.end_date,
                'absence_id': absence.id
            })

    manageable_users = User.query.filter(
        user_status_filter,
        User.role == UserRole.USER
    ).count()
    total_users = User.query.filter_by(role=UserRole.USER).count()
    active_percentage = round((manageable_users / total_users) * 100) if total_users > 0 else 0

    today_absent_count = len(today_absent)
    today_present_count = len(today_present)

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

    stats = {
        'active_users': manageable_users,
        'active_percentage': active_percentage,
        'today_absent': today_absent_count,
        'today_present': today_present_count,
        'month_absent': this_month_absent,
        'month_present': this_month_present
    }

    is_today_holiday, holiday_name = is_holiday(today)
    today_holiday = holiday_name if is_today_holiday else None

    return render_template(
        'dashboard/index.html',
        today=today,
        today_absent=today_absent,
        today_present=today_present,
        week_absences=week_absences,
        warnings=warnings,
        stats=stats,
        today_holiday=today_holiday,
        week_start=week_start,
        week_end=week_end
    )


@bp.route('/team-overview')
def team_overview():
    """Display team overview matrix (users × days) - responsive week/month view."""
    save_return_url('Team-Übersicht')
    today = date.today()

    month_names = [
        'Januar', 'Februar', 'März', 'April', 'Mai', 'Juni',
        'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'
    ]
    weekday_names_full = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']
    weekday_names_short = ['Mo', 'Di', 'Mi', 'Do', 'Fr']

    year_str = request.args.get('year')
    if year_str:
        try:
            year = int(year_str)
        except ValueError:
            abort(400, 'Invalid year')
    else:
        year = today.year

    month_str = request.args.get('month')
    if month_str:
        try:
            month = int(month_str)
        except ValueError:
            abort(400, 'Invalid month')
    else:
        month = today.month

    if month < 1 or month > 12:
        abort(400, 'Invalid month')

    current_year = today.year
    if year < current_year - 50 or year > current_year + 50:
        abort(400, 'Invalid year')

    week_start_str = request.args.get('week_start')
    if week_start_str:
        if len(week_start_str) != 10:
            abort(400, 'Invalid date format')
        try:
            week_start = date.fromisoformat(week_start_str)
            week_start = week_start - timedelta(days=week_start.weekday())
        except ValueError:
            abort(400, 'Invalid date format')
    else:
        week_start = today - timedelta(days=today.weekday())

    week_end = week_start + timedelta(days=4)

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

    holidays = get_holidays_for_month(year, month)
    if week_start.month != month or week_start.year != year:
        holidays.update(get_holidays_for_month(week_start.year, week_start.month))
    if week_end.month != week_start.month:
        holidays.update(get_holidays_for_month(week_end.year, week_end.month))

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

    week_days = []
    for day_offset in range(5):
        current_date = week_start + timedelta(days=day_offset)
        week_days.append({
            'day': current_date.day,
            'date': current_date,
            'is_weekend': False,
            'is_holiday': current_date in holidays,
            'is_today': current_date == today,
            'holiday_name': holidays.get(current_date),
            'weekday_short': weekday_names_short[day_offset]
        })

    month_days = []
    first_weekday = month_start.weekday()

    for i in range(first_weekday):
        month_days.append({'empty': True})

    current_date = month_start
    while current_date <= month_end:
        month_days.append({
            'day': current_date.day,
            'date': current_date,
            'is_weekend': current_date.weekday() >= 5,
            'is_holiday': current_date in holidays,
            'is_today': current_date == today,
            'holiday_name': holidays.get(current_date),
            'weekday_short': weekday_names_full[current_date.weekday()]
        })
        current_date += timedelta(days=1)

    prev_week = week_start - timedelta(days=7)
    next_week = week_start + timedelta(days=7)

    if month == 1:
        prev_month = {'year': year - 1, 'month': 12}
    else:
        prev_month = {'year': year, 'month': month - 1}

    if month == 12:
        next_month = {'year': year + 1, 'month': 1}
    else:
        next_month = {'year': year, 'month': month + 1}

    if week_start.month == week_end.month:
        week_title = f"{format_date_for_user(week_start, short=True)} - {format_date_for_user(week_end, short=True)} {month_names[week_start.month - 1]}"
    else:
        week_title = f"{format_date_for_user(week_start, short=True)} - {format_date_for_user(week_end, short=True)} {week_end.year}"

    month_title = f"{month_names[month - 1]} {year}"

    current_week_start = today - timedelta(days=today.weekday())
    is_current_week = week_start == current_week_start
    is_current_month = year == today.year and month == today.month

    return render_template(
        'dashboard/team-overview.html',
        users=users,
        week_days=week_days,
        month_days=month_days,
        matrix=matrix,
        categories=categories,
        holidays=holidays,
        today=today,
        year=year,
        month=month,
        week_start=week_start,
        week_end=week_end,
        week_title=week_title,
        month_title=month_title,
        prev_week=prev_week,
        next_week=next_week,
        prev_month=prev_month,
        next_month=next_month,
        is_current_week=is_current_week,
        is_current_month=is_current_month
    )
