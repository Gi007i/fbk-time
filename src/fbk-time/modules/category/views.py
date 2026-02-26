"""Category views.

Provides CRUD routes for category management with two-step deletion.
"""

from flask import Blueprint, render_template, redirect, url_for, request, abort
from flask_login import login_required, current_user

from core.extensions import db
from utils.decorators import manager_required
from utils.session_navigation import is_ajax_request, save_return_url, get_return_url
from utils.response_helpers import ajax_response, api_success
from utils.pagination import get_pagination
from .forms import CategoryForm, CategoryDeleteForm
from .services import (
    get_categories_list,
    create_category,
    update_category,
    get_absence_count,
    delete_category_with_absences,
    transfer_absences_and_delete,
    toggle_category_active,
    get_categories_excluding,
    get_category_or_404,
    add_absence_counts_to_categories
)

bp = Blueprint('categories', __name__, url_prefix='/categories')


@bp.before_request
@login_required
def require_login():
    """Require login for all category routes."""
    pass


@bp.route('/')
def list_categories():
    """Display list of all categories."""
    save_return_url('Kategorien')
    show_inactive_str = request.args.get('show_inactive', 'false')
    if show_inactive_str not in ('true', 'false'):
        abort(400, 'Invalid show_inactive')
    show_inactive = show_inactive_str == 'true'

    _, total = get_categories_list(show_inactive=show_inactive)
    pagination, redirect_response = get_pagination(total, 'categories.list_categories')

    if redirect_response:
        return redirect_response

    categories, _ = get_categories_list(
        show_inactive=show_inactive,
        page=pagination.page,
        per_page=pagination.per_page
    )

    add_absence_counts_to_categories(categories)

    return render_template(
        'categories/list.html',
        categories=categories,
        show_inactive=show_inactive,
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
            return render_template('categories/create.html', form=form)

        db.session.commit()

        message = f'Kategorie "{category.name}" wurde erstellt.'
        if is_ajax_request():
            return ajax_response(
                success=True,
                message=message,
                redirect=url_for('categories.list_categories')
            )
        return redirect(url_for('categories.list_categories'))

    if request.method == 'GET':
        form.text_color.data = current_user.default_text_color

    if request.method == 'POST' and is_ajax_request():
        errors = {field.name: field.errors[0] for field in form if field.errors}
        first_error = next(iter(errors.values()), 'Validierungsfehler')
        return ajax_response(success=False, message=first_error, errors=errors)

    return render_template('categories/create.html', form=form)


@bp.route('/<int:id>')
def detail(id):
    """Display category details."""
    category = get_category_or_404(id)
    absence_count = get_absence_count(id)

    return render_template(
        'categories/detail.html',
        category=category,
        absence_count=absence_count
    )


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
            return render_template('categories/edit.html', form=form, category=category)

        db.session.commit()

        message = f'Kategorie "{category.name}" wurde aktualisiert.'
        if is_ajax_request():
            return ajax_response(
                success=True,
                message=message,
                redirect=url_for('categories.list_categories')
            )
        return redirect(url_for('categories.list_categories'))

    if request.method == 'POST' and is_ajax_request():
        errors = {field.name: field.errors[0] for field in form if field.errors}
        first_error = next(iter(errors.values()), 'Validierungsfehler')
        return ajax_response(success=False, message=first_error, errors=errors)

    return render_template('categories/edit.html', form=form, category=category)


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

    # AJAX direct delete (no form data, CSRF validated via header)
    if request.method == 'POST' and is_ajax_request() and absences_count == 0:
        message = delete_category_with_absences(category)
        db.session.commit()
        return_to = get_return_url('categories.list_categories')
        return ajax_response(success=True, message=message, redirect=return_to)

    other_categories = get_categories_excluding(id)

    form = CategoryDeleteForm()
    form.new_category_id.choices = [('', '-- Kategorie wählen --')] + [
        (str(c.id), c.name) for c in other_categories
    ]

    if form.validate_on_submit():
        action = form.action.data

        if absences_count == 0 or action == 'delete_all':
            message = delete_category_with_absences(category)
            db.session.commit()

            return_to = get_return_url('categories.list_categories')

            if is_ajax_request():
                return ajax_response(success=True, message=message, redirect=return_to)

            return redirect(return_to)

        elif action == 'transfer':
            new_category_id = form.new_category_id.data
            if new_category_id:
                try:
                    new_category_id = int(new_category_id)
                except (ValueError, TypeError):
                    abort(400, 'Invalid category_id')

            if not new_category_id:
                if is_ajax_request():
                    return ajax_response(success=False, message='Bitte eine Zielkategorie auswählen.')
                return render_template(
                    'categories/delete.html',
                    form=form,
                    category=category,
                    absences_count=absences_count,
                    other_categories=other_categories
                )

            success, message = transfer_absences_and_delete(category, new_category_id)

            if not success:
                if is_ajax_request():
                    return ajax_response(success=False, message=message)
                return render_template(
                    'categories/delete.html',
                    form=form,
                    category=category,
                    absences_count=absences_count,
                    other_categories=other_categories
                )

            db.session.commit()

            return_to = get_return_url('categories.list_categories')

            if is_ajax_request():
                return ajax_response(success=True, message=message, redirect=return_to)

            return redirect(return_to)

        else:
            error_message = 'Ungültige Aktion. Bitte wählen Sie eine Option.'
            if is_ajax_request():
                return ajax_response(success=False, message=error_message)
            abort(400, error_message)

    return render_template(
        'categories/delete.html',
        form=form,
        category=category,
        absences_count=absences_count,
        other_categories=other_categories
    )


@bp.route('/<int:id>/toggle-active', methods=['POST'])
@manager_required
def toggle_active(id):
    """Toggle category active status (Manager+ only)."""
    category = get_category_or_404(id)
    message = toggle_category_active(category)
    db.session.commit()

    return_to = get_return_url('categories.list_categories')

    if is_ajax_request():
        return ajax_response(success=True, message=message, redirect=return_to)

    return redirect(return_to)
