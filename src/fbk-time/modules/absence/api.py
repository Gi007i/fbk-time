"""Absence API endpoints."""

from datetime import date
from typing import List

from flask import request, current_app
from sqlalchemy.exc import SQLAlchemyError

from core.extensions import db
from core.settings_manager import settings_manager
from modules.absence.models import Absence
from utils.decorators import login_required_api
from utils.request_validators import (
    parse_date_string,
    validate_int_param,
    validate_date_param
)
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
    """Delete multiple absences and/or occurrences in one request.

    Items that cannot be resolved or are not authorized for the
    current user are counted separately and reported back in the
    response so the client can surface partial failures.
    """
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return api_error('No data provided')

    ids = data.get('ids')
    if not isinstance(ids, list):
        return api_error('ids must be a list')

    if not ids:
        return api_error('No IDs provided')

    max_items = settings_manager.get('limits_bulk_delete_items')
    if len(ids) > max_items:
        return api_error(f'Too many items (max {max_items})')

    deleted = 0
    forbidden = 0
    not_found = 0
    invalid = 0

    for item_id in ids:
        item_id_str = str(item_id)

        if ':' in item_id_str:
            try:
                absence_id_str, date_str = item_id_str.split(':', 1)
                absence_id = int(absence_id_str)
            except (ValueError, TypeError):
                invalid += 1
                continue

            occurrence_date = parse_date_string(date_str)
            if not occurrence_date:
                invalid += 1
                continue

            absence = get_absence_by_id(absence_id)
            if not absence or not absence.is_recurring:
                not_found += 1
                continue
            if not can_modify_absence(absence):
                forbidden += 1
                continue

            try:
                delete_occurrence(absence, occurrence_date)
                deleted += 1
            except ValueError:
                invalid += 1
            except SQLAlchemyError:
                current_app.logger.exception(
                    'Bulk delete: unexpected error deleting occurrence '
                    '%s on %s', absence_id, occurrence_date
                )
                db.session.rollback()
                return api_error('Bulk delete failed - no items deleted')
            continue

        try:
            absence_id = int(item_id_str)
        except (ValueError, TypeError):
            invalid += 1
            continue

        absence = get_absence_by_id(absence_id)
        if not absence:
            not_found += 1
            continue
        if not can_modify_absence(absence):
            forbidden += 1
            continue

        try:
            delete_absence(absence)
            deleted += 1
        except SQLAlchemyError:
            current_app.logger.exception(
                'Bulk delete: unexpected error deleting absence %s',
                absence_id
            )
            db.session.rollback()
            return api_error('Bulk delete failed - no items deleted')

    try:
        db.session.commit()
    except SQLAlchemyError:
        current_app.logger.exception('Bulk delete: commit failed')
        db.session.rollback()
        return api_error('Bulk delete failed during commit - no items deleted')

    return api_success(data={
        'deleted': deleted,
        'forbidden': forbidden,
        'not_found': not_found,
        'invalid': invalid
    })


@bp.route('/api/expand-occurrences', methods=['GET'])
@login_required_api
def api_expand_occurrences():
    """Expand recurring absence occurrences within a date range."""
    absence_id = validate_int_param('absence_id', min_value=1)
    start_date = validate_date_param('start_date', required=True)
    end_date = validate_date_param('end_date', required=True)

    if end_date < start_date:
        return api_error('Invalid date range: end before start')

    if absence_id is not None:
        absence = get_absence_by_id(absence_id)
        if not absence:
            return api_error('Absence not found', status_code=404)

        occurrences = _serialize_absence_occurrences(
            absence, start_date, end_date
        )
        return api_success(data={'occurrences': occurrences})

    recurring_absences = get_recurring_absences_for_active_users()
    all_occurrences = []
    for absence in recurring_absences:
        all_occurrences.extend(
            _serialize_absence_occurrences(absence, start_date, end_date)
        )
    return api_success(data={'occurrences': all_occurrences})


def _serialize_absence_occurrences(
    absence: Absence,
    start_date: date,
    end_date: date
) -> List[dict]:
    """Expand one absence into a JSON-friendly occurrence list."""
    result = []
    for occ_date, _exception in recurrence_service.expand_occurrences(
        absence, start_date, end_date
    ):
        occ_data = recurrence_service.get_occurrence_data(absence, occ_date)
        if not occ_data:
            continue
        result.append({
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
    return result
