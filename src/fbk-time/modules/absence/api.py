"""Absence API endpoints.

Provides JSON API endpoints for real-time conflict checking,
substitute recommendations, and bulk operations.
"""

from datetime import date, datetime
from flask import request, jsonify

from core.extensions import db
from utils.decorators import login_required_api, manager_required_api
from modules.auth.models import User, UserRole, UserStatus
from .views import bp
from .models import Absence
from .validation import check_absence_conflicts, get_available_substitutes
from .recurrence import recurrence_service


@bp.route('/api/check-conflicts', methods=['POST'])
@login_required_api
def api_check_conflicts():
    """API endpoint for real-time conflict checking."""
    data = request.get_json()

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    current_year = date.today().year

    try:
        user_id = int(data.get('user_id'))
        start_date_str = data.get('start_date')
        end_date_str = data.get('end_date')

        if not start_date_str or len(start_date_str) != 10:
            return jsonify({'error': 'Invalid date format'}), 400
        if not end_date_str or len(end_date_str) != 10:
            return jsonify({'error': 'Invalid date format'}), 400

        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()

        if start_date.year < current_year - 50 or start_date.year > current_year + 50:
            return jsonify({'error': 'Invalid date range'}), 400
        if end_date.year < current_year - 50 or end_date.year > current_year + 50:
            return jsonify({'error': 'Invalid date range'}), 400
        substitute_id = data.get('substitute_id')
        exclude_id = data.get('exclude_id')

        if substitute_id:
            substitute_id = int(substitute_id)
        if exclude_id:
            exclude_id = int(exclude_id)

    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid parameters'}), 400

    conflicts = check_absence_conflicts(
        user_id,
        start_date,
        end_date,
        exclude_absence_id=exclude_id,
        substitute_id=substitute_id
    )

    return jsonify({
        'has_conflicts': conflicts.has_conflicts,
        'messages': conflicts.messages,
        'cross_substitution_warning': conflicts.cross_substitution_warning
    })


@bp.route('/api/available-substitutes', methods=['GET'])
@login_required_api
def api_available_substitutes():
    """API endpoint to get available substitutes for a date range."""
    current_year = date.today().year

    try:
        user_id_str = request.args.get('user_id')
        if not user_id_str:
            return jsonify({'error': 'User ID required'}), 400
        try:
            user_id = int(user_id_str)
        except ValueError:
            return jsonify({'error': 'Invalid user_id'}), 400

        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')

        if not start_date_str or len(start_date_str) != 10:
            return jsonify({'error': 'Invalid date format'}), 400
        if not end_date_str or len(end_date_str) != 10:
            return jsonify({'error': 'Invalid date format'}), 400

        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()

        if start_date.year < current_year - 50 or start_date.year > current_year + 50:
            return jsonify({'error': 'Invalid date range'}), 400
        if end_date.year < current_year - 50 or end_date.year > current_year + 50:
            return jsonify({'error': 'Invalid date range'}), 400
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid parameters'}), 400

    available = get_available_substitutes(user_id, start_date, end_date)

    return jsonify({
        'substitutes': [
            {'id': e.id, 'name': e.name}
            for e in available
        ]
    })


@bp.route('/api/bulk-delete', methods=['POST'])
@manager_required_api
def api_bulk_delete():
    """API endpoint for bulk deletion of absences and occurrences (Manager+ only)."""
    data = request.get_json()

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    ids = data.get('ids', [])
    if not ids:
        return jsonify({'error': 'No IDs provided'}), 400

    current_year = date.today().year
    deleted = 0
    for item_id in ids:
        item_id_str = str(item_id)

        if ':' in item_id_str:
            try:
                absence_id_str, date_str = item_id_str.split(':', 1)
                if len(date_str) != 10:
                    continue
                absence_id = int(absence_id_str)
                occurrence_date = datetime.strptime(date_str, '%Y-%m-%d').date()

                if occurrence_date.year < current_year - 50 or occurrence_date.year > current_year + 50:
                    continue

                absence = db.session.get(Absence,absence_id)
                if absence and absence.is_recurring:
                    recurrence_service.delete_occurrence(absence, occurrence_date)
                    deleted += 1
            except (ValueError, TypeError):
                continue
        else:
            try:
                absence = db.session.get(Absence,int(item_id_str))
                if absence:
                    db.session.delete(absence)
                    deleted += 1
            except (ValueError, TypeError):
                continue

    db.session.commit()
    return jsonify({'success': True, 'deleted': deleted})


@bp.route('/api/expand-occurrences', methods=['GET'])
@login_required_api
def api_expand_occurrences():
    """API endpoint to expand recurring absence occurrences for a date range."""
    current_year = date.today().year

    try:
        absence_id_str = request.args.get('absence_id')
        if absence_id_str:
            try:
                absence_id = int(absence_id_str)
            except ValueError:
                return jsonify({'error': 'Invalid absence_id'}), 400
        else:
            absence_id = None

        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')

        if not start_date_str or len(start_date_str) != 10:
            return jsonify({'error': 'Invalid date format'}), 400
        if not end_date_str or len(end_date_str) != 10:
            return jsonify({'error': 'Invalid date format'}), 400

        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()

        if start_date.year < current_year - 50 or start_date.year > current_year + 50:
            return jsonify({'error': 'Invalid date range'}), 400
        if end_date.year < current_year - 50 or end_date.year > current_year + 50:
            return jsonify({'error': 'Invalid date range'}), 400
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid parameters'}), 400

    if absence_id is not None:
        absence = db.session.get(Absence,absence_id)
        if not absence:
            return jsonify({'error': 'Absence not found'}), 404

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

        return jsonify({'occurrences': occurrences})
    else:
        user_status_filter = User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED])

        recurring_absences = Absence.query.join(
            User, Absence.user_id == User.id
        ).filter(
            Absence.is_recurring == True,
            user_status_filter,
            User.role == UserRole.USER
        ).all()
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

        return jsonify({'occurrences': all_occurrences})
