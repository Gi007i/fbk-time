"""Absence API endpoints."""

from flask import request, current_app
from sqlalchemy.exc import SQLAlchemyError

from core.db import db
from core.settings_manager import settings_manager
from utils.request_validators import parse_date_string
from utils.response_helpers import api_success, api_error
from .views import bp
from .services import (
    get_absence_by_id,
    can_modify_absence,
    delete_absence,
    delete_occurrence
)

_BULK_DELETE_FAILED = 'Löschen fehlgeschlagen, es wurde nichts gelöscht.'


@bp.route('/api/bulk-delete', methods=['POST'])
def api_bulk_delete():
    """Delete multiple absences and/or occurrences in one request.

    Items that cannot be resolved or are not authorized for the
    current user are counted separately and reported back in the
    response so the client can surface partial failures.
    """
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return api_error('Keine Daten übermittelt.')

    ids = data.get('ids')
    if not isinstance(ids, list):
        return api_error('Ungültige Auswahl.')

    if not ids:
        return api_error('Keine Einträge ausgewählt.')

    max_items = settings_manager.get('limits_bulk_delete_items')
    if len(ids) > max_items:
        return api_error(f'Zu viele Einträge ausgewählt (höchstens {max_items}).')

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
                return api_error(_BULK_DELETE_FAILED)
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
            return api_error(_BULK_DELETE_FAILED)

    try:
        db.session.commit()
    except SQLAlchemyError:
        current_app.logger.exception('Bulk delete: commit failed')
        db.session.rollback()
        return api_error(_BULK_DELETE_FAILED)

    return api_success(data={
        'deleted': deleted,
        'forbidden': forbidden,
        'not_found': not_found,
        'invalid': invalid
    })
