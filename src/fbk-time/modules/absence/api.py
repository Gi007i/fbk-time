"""Absence API endpoints."""

from flask import request

from core.extensions import db
from utils.decorators import login_required_api
from utils.response_helpers import api_success, api_error
from .views import bp
from .recurrence import recurrence_service
from .services import (
    get_absence_by_id,
    get_recurring_absences_for_active_users,
    can_modify_absence,
    delete_absence,
    delete_occurrence
)


@bp.route('/api/bulk-delete', methods=['POST'])
@login_required_api
def api_bulk_delete():
    from utils.request_validators import parse_date_string

    data = request.get_json()

    if not data:
        return api_error('No data provided')

    ids = data.get('ids', [])
    if not ids:
        return api_error('No IDs provided')

    deleted = 0
    for item_id in ids:
        item_id_str = str(item_id)

        if ':' in item_id_str:
            try:
                absence_id_str, date_str = item_id_str.split(':', 1)
                absence_id = int(absence_id_str)
                occurrence_date = parse_date_string(date_str)

                if not occurrence_date:
                    continue

                absence = get_absence_by_id(absence_id)
                if absence and absence.is_recurring and can_modify_absence(absence):
                    delete_occurrence(absence, occurrence_date)
                    deleted += 1
            except (ValueError, TypeError):
                continue
        else:
            try:
                absence = get_absence_by_id(int(item_id_str))
                if absence and can_modify_absence(absence):
                    delete_absence(absence)
                    deleted += 1
            except (ValueError, TypeError):
                continue

    db.session.commit()
    return api_success(data={'deleted': deleted})


@bp.route('/api/expand-occurrences', methods=['GET'])
@login_required_api
def api_expand_occurrences():
    from utils.request_validators import validate_int_param, validate_date_param

    absence_id = validate_int_param('absence_id', min_value=1)
    start_date = validate_date_param('start_date', required=True)
    end_date = validate_date_param('end_date', required=True)

    if absence_id is not None:
        absence = get_absence_by_id(absence_id)
        if not absence:
            return api_error('Absence not found', status_code=404)

        occurrences = []
        for occ_date, exception in recurrence_service.expand_occurrences(absence, start_date, end_date):
            occ_data = recurrence_service.get_occurrence_data(absence, occ_date)
            if occ_data:
                occurrences.append({
                    'date': occ_date.isoformat(),
                    'absence_id': absence.id,
                    'user_id': occ_data['user_id'],
                    'user_name': occ_data['user'].name,
                    'category_id': occ_data['category_id'],
                    'category_name': occ_data['category'].name,
                    'is_exception': occ_data['is_exception'],
                    'is_half_day_morning': occ_data['is_half_day_morning'],
                    'is_half_day_afternoon': occ_data['is_half_day_afternoon']
                })

        return api_success(data={'occurrences': occurrences})
    else:
        recurring_absences = get_recurring_absences_for_active_users()
        all_occurrences = []

        for absence in recurring_absences:
            for occ_date, exception in recurrence_service.expand_occurrences(absence, start_date, end_date):
                occ_data = recurrence_service.get_occurrence_data(absence, occ_date)
                if occ_data:
                    all_occurrences.append({
                        'date': occ_date.isoformat(),
                        'absence_id': absence.id,
                        'user_id': occ_data['user_id'],
                        'user_name': occ_data['user'].name,
                        'category_id': occ_data['category_id'],
                        'category_name': occ_data['category'].name,
                        'is_exception': occ_data['is_exception'],
                        'is_half_day_morning': occ_data['is_half_day_morning'],
                        'is_half_day_afternoon': occ_data['is_half_day_afternoon']
                    })

        return api_success(data={'occurrences': all_occurrences})
