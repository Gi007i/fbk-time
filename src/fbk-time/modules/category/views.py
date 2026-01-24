"""Category views.

Provides CRUD routes for category management with two-step deletion.
"""

from flask import Blueprint, render_template, redirect, url_for, request, abort, jsonify
from flask_login import login_required, current_user
from sqlalchemy import func

from core.extensions import db
from utils.decorators import manager_required
from utils.session_navigation import is_ajax_request, save_return_url, get_return_url
from utils.response_helpers import ajax_response
from .models import Category
from .forms import CategoryForm, CategoryDeleteForm
from modules.absence.models import Absence

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
    show_inactive = request.args.get('show_inactive', 'false') == 'true'

    query = Category.query

    if not show_inactive:
        query = query.filter(Category.active == True)

    per_page = current_user.items_per_page
    total = query.count()

    # per_page == 0 means show all (no pagination)
    if per_page == 0:
        page = 1
        total_pages = 1
        categories = query.order_by(Category.sort_order, Category.name).all()
    else:
        page_str = request.args.get('page')
        if page_str:
            try:
                page = int(page_str)
            except ValueError:
                abort(400, 'Invalid page number')
        else:
            page = 1

        total_pages = (total + per_page - 1) // per_page if total > 0 else 1

        if page < 1:
            abort(400, 'Invalid page number')

        # Redirect to last valid page if current page no longer exists
        if page > total_pages:
            args = request.args.to_dict()
            args['page'] = str(total_pages)
            return redirect(url_for('categories.list_categories', **args))

        categories = query.order_by(Category.sort_order, Category.name).offset((page - 1) * per_page).limit(per_page).all()

    for cat in categories:
        cat.absence_count = cat.absences.count()

    return render_template(
        'categories/list.html',
        categories=categories,
        show_inactive=show_inactive,
        pagination={
            'page': page,
            'total': total,
            'total_pages': total_pages,
            'has_prev': page > 1 if per_page > 0 else False,
            'has_next': page < total_pages if per_page > 0 else False
        }
    )


@bp.route('/create', methods=['GET', 'POST'])
@manager_required
def create():
    """Create a new category (Manager+ only)."""
    form = CategoryForm()

    if form.validate_on_submit():
        existing = Category.query.filter_by(name=form.name.data.strip()).first()
        if existing:
            if is_ajax_request():
                return ajax_response(
                    success=False,
                    message='Eine Kategorie mit diesem Namen existiert bereits.'
                )
            return render_template('categories/create.html', form=form)

        category = Category(
            name=form.name.data.strip(),
            color=form.color.data.strip().upper(),
            text_color=form.text_color.data.strip().upper(),
            icon=form.icon.data.strip() if form.icon.data else None,
            requires_substitute=form.requires_substitute.data,
            is_present=form.is_present.data,
            sort_order=form.sort_order.data or 0,
            active=form.active.data
        )

        db.session.add(category)
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

    return render_template('categories/create.html', form=form)


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@manager_required
def edit(id):
    """Edit an existing category (Manager+ only)."""
    category = Category.query.get_or_404(id)
    form = CategoryForm(obj=category)

    if form.validate_on_submit():
        existing = Category.query.filter(
            Category.name == form.name.data.strip(),
            Category.id != id
        ).first()
        if existing:
            if is_ajax_request():
                return ajax_response(
                    success=False,
                    message='Eine Kategorie mit diesem Namen existiert bereits.'
                )
            return render_template('categories/edit.html', form=form, category=category)

        category.name = form.name.data.strip()
        category.color = form.color.data.strip().upper()
        category.text_color = form.text_color.data.strip().upper()
        category.icon = form.icon.data.strip() if form.icon.data else None
        category.requires_substitute = form.requires_substitute.data
        category.is_present = form.is_present.data
        category.sort_order = form.sort_order.data or 0
        category.active = form.active.data

        db.session.commit()

        message = f'Kategorie "{category.name}" wurde aktualisiert.'
        if is_ajax_request():
            return ajax_response(
                success=True,
                message=message,
                redirect=url_for('categories.list_categories')
            )
        return redirect(url_for('categories.list_categories'))

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
    category = Category.query.get_or_404(id)
    absences_count = Absence.query.filter_by(category_id=id).count()

    # AJAX check endpoint for direct delete vs. dialog decision
    if request.method == 'GET' and request.args.get('check') == '1':
        return jsonify({'has_absences': absences_count > 0})

    other_categories = Category.query.filter(Category.id != id).order_by(Category.name).all()

    form = CategoryDeleteForm()
    form.new_category_id.choices = [('', '-- Kategorie wählen --')] + [
        (str(c.id), c.name) for c in other_categories
    ]

    if request.method == 'POST':
        action = request.form.get('action')

        if absences_count == 0 or action == 'delete_all':
            if absences_count > 0:
                Absence.query.filter_by(category_id=id).delete()

            name = category.name
            db.session.delete(category)
            db.session.commit()

            if absences_count > 0:
                message = f'Kategorie "{name}" und {absences_count} Abwesenheit(en) wurden gelöscht.'
            else:
                message = f'Kategorie "{name}" wurde gelöscht.'

            return_to = get_return_url('categories.list_categories')

            if is_ajax_request():
                return ajax_response(success=True, message=message, redirect=return_to)

            return redirect(return_to)

        elif action == 'transfer':
            new_category_id_str = request.form.get('new_category_id')
            if new_category_id_str:
                try:
                    new_category_id = int(new_category_id_str)
                except ValueError:
                    abort(400, 'Invalid category_id')
            else:
                new_category_id = None

            if new_category_id and new_category_id != id:
                target_category = db.session.get(Category, new_category_id)
                if target_category:
                    Absence.query.filter_by(category_id=id).update({'category_id': new_category_id})
                    name = category.name
                    db.session.delete(category)
                    db.session.commit()
                    message = (
                        f'{absences_count} Abwesenheit(en) nach "{target_category.name}" übertragen, '
                        f'Kategorie "{name}" gelöscht.'
                    )

                    return_to = get_return_url('categories.list_categories')

                    if is_ajax_request():
                        return ajax_response(success=True, message=message, redirect=return_to)

                    return redirect(return_to)
                else:
                    if is_ajax_request():
                        return ajax_response(success=False, message='Zielkategorie nicht gefunden.')
            else:
                if is_ajax_request():
                    return ajax_response(success=False, message='Bitte eine Zielkategorie auswählen.')

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
    category = Category.query.get_or_404(id)
    category.active = not category.active
    db.session.commit()

    status = 'aktiviert' if category.active else 'deaktiviert'
    message = f'Kategorie "{category.name}" wurde {status}.'
    return_to = get_return_url('categories.list_categories')

    if is_ajax_request():
        return ajax_response(success=True, message=message, redirect=return_to)

    return redirect(return_to)
