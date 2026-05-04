"""Profile views.

Provides user profile page for viewing and editing own account information.
"""

from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from utils.session_navigation import save_return_url
from .forms import ProfileEditForm
from .services import get_profile_data, update_profile

bp = Blueprint('profile', __name__, url_prefix='/profile')


@bp.before_request
@login_required
def require_login():
    """Require login for all profile routes."""
    pass


@bp.route('/', methods=['GET'])
def index():
    """Display user profile with account information and edit form."""
    save_return_url('Profil')
    profile = get_profile_data()
    form = ProfileEditForm(data={'name': profile['name'], 'email': profile['email']})
    return render_template('profile/index.html', profile=profile, form=form)


@bp.route('/edit', methods=['POST'])
def edit():
    """Process profile edit submission (display name and email)."""
    form = ProfileEditForm()

    if form.validate_on_submit():
        update_profile(
            user=current_user,
            name=form.name.data,
            email=form.email.data
        )
        flash('Profil erfolgreich aktualisiert.', 'success')
        return redirect(url_for('profile.index'))

    profile = get_profile_data()
    return render_template('profile/index.html', profile=profile, form=form)
