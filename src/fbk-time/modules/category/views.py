"""Category CRUD views with two-step deletion."""

from flask import Blueprint, render_template, redirect, request, abort, flash

from core.auth import login_is_fresh, login_required, current_user
from core.db import db
from utils.decorators import manager_required, reauthentication_response
from utils.response_helpers import ajax_response, api_success, is_ajax_request
from utils.navigation import back_url, back_url_focused
from utils.pagination import get_pagination
from utils.request_validators import validate_int_param
from .forms import CategoryForm, CategoryDeleteForm
from .helpers import CATEGORY_ICONS
from .services import (
    get_categories_list,
    create_category,
    update_category,
    get_absence_count,
    delete_category_with_absences,
    transfer_absences_and_delete,
    toggle_category_active,
    get_categories_excluding,
    get_category_or_404
)
from modules.absence.services import count_occurrences_by_category, default_list_range

bp = Blueprint('categories', __name__, url_prefix='/categories')


@bp.before_request
@login_required
def require_login():
    """Require login for all category routes."""
    pass


@bp.route('/')
@manager_required
def list_categories():
    """Display list of all categories."""
    status_filter = request.args.get('status') or 'active'

    try:
        _, total = get_categories_list(status_filter=status_filter)
    except ValueError:
        abort(400, 'Invalid filter parameter')

    pagination, redirect_response = get_pagination(total, 'categories.list_categories')

    if redirect_response:
        return redirect_response

    categories, _ = get_categories_list(
        status_filter=status_filter,
        page=pagination.page,
        per_page=pagination.per_page
    )

    range_from, range_to = default_list_range()

    return render_template(
        'categories/list.html',
        categories=categories,
        status_filter=status_filter,
        absence_counts=count_occurrences_by_category(range_from, range_to),
        open_row=validate_int_param('open', min_value=1),
        pagination=pagination.to_dict()
    )


@bp.route('/create', methods=['GET', 'POST'])
@manager_required
def create():
    """Create a new category (Manager+ only)."""
    form = CategoryForm()

    if form.validate_on_submit():
        category, error = create_category(
            name=form.name.data,
            color=form.color.data,
            text_color=form.text_color.data,
            icon=form.icon.data,
            requires_substitute=form.requires_substitute.data,
            is_present=form.is_present.data,
            sort_order=form.sort_order.data or 0,
            active=form.active.data
        )

        if error:
            if is_ajax_request():
                return ajax_response(success=False, message=error)
            flash(error, 'danger')
            return render_template('categories/create.html', form=form, category_icons=CATEGORY_ICONS)

        db.session.commit()

        message = f'Kategorie "{category.name}" wurde erstellt.'
        return_to = back_url('categories.list_categories')
        if is_ajax_request():
            return ajax_response(success=True, message=message, redirect=return_to)
        return redirect(return_to)

    if request.method == 'GET':
        form.text_color.data = current_user.default_text_color

    if request.method == 'POST' and is_ajax_request():
        errors = {field.name: field.errors[0] for field in form if field.errors}
        first_error = next(iter(errors.values()), 'Validierungsfehler')
        return ajax_response(success=False, message=first_error, errors=errors)

    return render_template('categories/create.html', form=form, category_icons=CATEGORY_ICONS)


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@manager_required
def edit(id):
    """Edit an existing category (Manager+ only)."""
    category = get_category_or_404(id)
    form = CategoryForm(obj=category)

    if form.validate_on_submit():
        success, error = update_category(
            category=category,
            name=form.name.data,
            color=form.color.data,
            text_color=form.text_color.data,
            icon=form.icon.data,
            requires_substitute=form.requires_substitute.data,
            is_present=form.is_present.data,
            sort_order=form.sort_order.data or 0,
            active=form.active.data
        )

        if not success:
            if is_ajax_request():
                return ajax_response(success=False, message=error)
            flash(error, 'danger')
            return render_template('categories/edit.html', form=form, category=category, category_icons=CATEGORY_ICONS)

        db.session.commit()

        message = f'Kategorie "{category.name}" wurde aktualisiert.'
        return_to = back_url_focused('categories.list_categories', category.id)
        if is_ajax_request():
            return ajax_response(success=True, message=message, redirect=return_to)
        return redirect(return_to)

    if request.method == 'POST' and is_ajax_request():
        errors = {field.name: field.errors[0] for field in form if field.errors}
        first_error = next(iter(errors.values()), 'Validierungsfehler')
        return ajax_response(success=False, message=first_error, errors=errors)

    return render_template('categories/edit.html', form=form, category=category, category_icons=CATEGORY_ICONS)


@bp.route('/<int:id>/delete', methods=['GET', 'POST'])
@manager_required
def delete(id):
    """Delete a category with two-step process (Manager+ only).

    If absences exist with this category:
    - Option A: Transfer absences to another category
    - Option B: Delete all absences with their history

    AJAX check endpoint: GET with ?check=1 returns JSON with has_absences flag.
    """
    category = get_category_or_404(id)
    absences_count = get_absence_count(id)

    if request.method == 'GET' and request.args.get('check') == '1':
        return api_success(data={'has_absences': absences_count > 0})

    # Deleting can remove every absence of the category; only the read-only
    # pre-check above runs without a recent password entry.
    if not login_is_fresh():
        return reauthentication_response()

    # AJAX direct delete (no form data, CSRF validated via header)
    if request.method == 'POST' and is_ajax_request() and 'action' not in request.form:
        if absences_count > 0:
            return ajax_response(
                success=False,
                message='Der Kategorie wurden inzwischen Abwesenheiten zugeordnet. '
                        'Bitte erneut löschen, um eine Option zu wählen.'
            )
        message = delete_category_with_absences(category)
        db.session.commit()
        return_to = back_url('categories.list_categories')
        return ajax_response(success=True, message=message, redirect=return_to)

    other_categories = get_categories_excluding(id)

    form = CategoryDeleteForm()
    form.new_category_id.choices = [('', '-- Kategorie wählen --')] + [
        (str(c.id), c.name) for c in other_categories
    ]

    def render_delete_page():
        return render_template(
            'categories/delete.html',
            form=form,
            category=category,
            absences_count=absences_count,
            other_categories=other_categories
        )

    def fail(message):
        if is_ajax_request():
            return ajax_response(success=False, message=message)
        flash(message, 'danger')
        return render_delete_page()

    if form.validate_on_submit():
        action = form.action.data
        if action == 'delete_empty' and absences_count > 0:
            return fail(
                'Der Kategorie wurden inzwischen Abwesenheiten zugeordnet. '
                'Bitte wählen Sie eine Option.'
            )

        if action in ('delete_empty', 'delete_all'):
            message = delete_category_with_absences(category)
        else:
            new_category_id = form.new_category_id.data
            if not new_category_id:
                return fail('Bitte eine Zielkategorie auswählen.')

            success, message = transfer_absences_and_delete(category, new_category_id)
            if not success:
                return fail(message)

        db.session.commit()

        return_to = back_url('categories.list_categories')
        if is_ajax_request():
            return ajax_response(success=True, message=message, redirect=return_to)
        flash(message, 'success')
        return redirect(return_to)

    if request.method == 'POST' and is_ajax_request():
        errors = {field.name: field.errors[0] for field in form if field.errors}
        first_error = next(iter(errors.values()), 'Validierungsfehler')
        return ajax_response(success=False, message=first_error, errors=errors)

    return render_delete_page()


@bp.route('/<int:id>/toggle-active', methods=['POST'])
@manager_required
def toggle_active(id):
    """Toggle category active status (Manager+ only)."""
    category = get_category_or_404(id)
    message = toggle_category_active(category)
    db.session.commit()

    return_to = back_url_focused('categories.list_categories', category.id)

    if is_ajax_request():
        return ajax_response(success=True, message=message, redirect=return_to)

    return redirect(return_to)
