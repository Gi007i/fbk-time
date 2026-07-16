"""Backup views.

Admin-only routes for database backup management.
List, create, verify, and delete backup archives.
Restore is intentionally CLI-only (requires the service to be stopped).
"""

from flask import Blueprint, render_template, redirect, url_for, flash, current_app
from flask_login import current_user

from utils.decorators import admin_required
from utils.pagination import get_pagination
from .forms import CreateBackupForm
from .services import (
    get_backup_list,
    get_backup_or_404,
    create_backup,
    verify_backup,
    delete_backup,
    sync_filesystem,
    get_backup_stats,
)

bp = Blueprint('backup', __name__, url_prefix='/backup')


@bp.before_request
@admin_required
def require_admin():
    """Require admin role for all backup routes."""
    pass


@bp.route('/')
def index():
    """List all backups with pagination and summary statistics."""
    stats = get_backup_stats()

    pagination, redirect_response = get_pagination(stats['total'], 'backup.index')
    if redirect_response:
        return redirect_response

    records = get_backup_list(pagination.page, pagination.per_page)
    form = CreateBackupForm()

    return render_template(
        'backup/index.html',
        records=records,
        pagination=pagination,
        stats=stats,
        form=form,
        backup_dir=current_app.config['BACKUP_DIR']
    )


@bp.route('/create', methods=['POST'])
def create():
    """Create a new manual backup."""
    form = CreateBackupForm()
    if not form.validate_on_submit():
        errors = [e for field in form for e in field.errors]
        flash(errors[0] if errors else 'Ungültige Eingabe.', 'error')
        return redirect(url_for('backup.index'))

    ok, message = create_backup(
        description=form.description.data or None,
        created_by_id=current_user.id
    )
    flash(message, 'success' if ok else 'error')
    return redirect(url_for('backup.index'))


@bp.route('/sync', methods=['POST'])
def sync():
    """Reconcile the backup directory with database records."""
    ok, message = sync_filesystem()
    flash(message, 'success' if ok else 'warning')
    return redirect(url_for('backup.index'))


@bp.route('/<int:backup_id>/verify', methods=['POST'])
def verify(backup_id: int):
    """Verify the integrity of a backup archive."""
    get_backup_or_404(backup_id)
    ok, message = verify_backup(backup_id)
    flash(message, 'success' if ok else 'error')
    return redirect(url_for('backup.index'))


@bp.route('/<int:backup_id>/delete', methods=['POST'])
def delete(backup_id: int):
    """Delete a backup record and its archive file."""
    get_backup_or_404(backup_id)
    ok, message = delete_backup(backup_id)
    flash(message, 'success' if ok else 'error')
    return redirect(url_for('backup.index'))
