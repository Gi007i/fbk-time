"""Dashboard views.

Provides the main dashboard view with widgets and team overview.
"""

from datetime import date, timedelta

from flask import Blueprint, render_template
from flask_login import login_required

from utils.helpers import format_date_for_user
from utils.request_validators import validate_year_param, validate_month_param, validate_date_param
from utils.filters import parse_absence_filters
from modules.holidays.services import get_holidays_for_month
from .services import (
    get_today_absences,
    get_week_overview,
    get_occurrence_categories,
    get_dashboard_warnings,
    get_today_holiday,
    get_team_overview_data
)

bp = Blueprint('dashboard', __name__)

WEEKDAY_NAMES = [
    'Montag', 'Dienstag', 'Mittwoch', 'Donnerstag',
    'Freitag', 'Samstag', 'Sonntag'
]


@bp.before_request
@login_required
def require_login():
    """Require login for all dashboard routes."""
    pass


@bp.route('/')
def index():
    """Display main dashboard with widgets."""
    today = date.today()

    today_absent, today_present = get_today_absences()
    today_categories = get_occurrence_categories(today_absent + today_present)
    week = get_week_overview()
    warnings = get_dashboard_warnings()
    today_holiday = get_today_holiday()

    return render_template(
        'dashboard/index.html',
        today=today,
        today_absent=today_absent,
        today_present=today_present,
        today_categories=today_categories,
        week_days=week['days'],
        week_categories=week['categories'],
        week_total=week['total'],
        week_start=week['week_start'],
        week_end=week['week_end'],
        weekday_names=WEEKDAY_NAMES,
        warnings=warnings,
        today_holiday=today_holiday
    )


@bp.route('/team-overview')
def team_overview():
    """Display team overview matrix (users × days) - responsive week/month view."""
    today = date.today()

    month_names = [
        'Januar', 'Februar', 'März', 'April', 'Mai', 'Juni',
        'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'
    ]
    weekday_names_full = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']
    weekday_names_short = ['Mo', 'Di', 'Mi', 'Do', 'Fr']

    year = validate_year_param()
    month = validate_month_param()

    week_start = validate_date_param('week_start')
    if week_start:
        week_start = week_start - timedelta(days=week_start.weekday())
    else:
        week_start = today - timedelta(days=today.weekday())

    week_end = week_start + timedelta(days=4)

    filters = parse_absence_filters()

    data = get_team_overview_data(year, month, week_start, week_end, filters)
    users = data['users']
    all_users = data['all_users']
    categories = data['categories']
    matrix = data['matrix']
    month_start = data['month_start']
    month_end = data['month_end']

    holidays = get_holidays_for_month(year, month)
    if week_start.month != month or week_start.year != year:
        holidays.update(get_holidays_for_month(week_start.year, week_start.month))
    if week_end.month != week_start.month:
        holidays.update(get_holidays_for_month(week_end.year, week_end.month))

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
        all_users=all_users,
        week_days=week_days,
        month_days=month_days,
        matrix=matrix,
        categories=categories,
        filters=filters,
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
