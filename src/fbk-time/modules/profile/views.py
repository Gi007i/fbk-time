"""Profile views.

Provides user profile page for viewing and editing own account information.
"""

from flask import Blueprint, flash, redirect, render_template, url_for

from core.auth import current_user, login_is_fresh, login_required
from core.db import db
from modules.auth.services import regenerate_session
from modules.user.services import end_user_sessions
from utils.decorators import reauthentication_response
from .forms import ProfileEditForm
from .services import changes_email, get_profile_data, update_profile

bp = Blueprint('profile', __name__, url_prefix='/profile')


@bp.before_request
@login_required
def require_login():
    """Require login for all profile routes."""
    pass


@bp.route('/', methods=['GET'])
def index():
    """Display user profile with account information and edit form."""
    profile = get_profile_data()
    form = ProfileEditForm(data={'name': profile['name'], 'email': profile['email']})
    return render_template('profile/index.html', profile=profile, form=form)


@bp.route('/edit', methods=['POST'])
def edit():
    """Process profile edit submission (display name and email)."""
    form = ProfileEditForm()

    if form.validate_on_submit():
        if changes_email(current_user, form.email.data) and not login_is_fresh():
            return reauthentication_response()
        update_profile(
            user=current_user,
            name=form.name.data,
            email=form.email.data
        )
        flash('Profil erfolgreich aktualisiert.', 'success')
        return redirect(url_for('profile.index'))

    profile = get_profile_data()
    return render_template('profile/index.html', profile=profile, form=form)


@bp.route('/end-sessions', methods=['POST'])
def end_sessions():
    """End every other session of the current user, keeping this one."""
    user = current_user._get_current_object()

    end_user_sessions(user)
    db.session.commit()

    regenerate_session(user)

    flash('Alle anderen Sitzungen wurden beendet.', 'success')
    return redirect(url_for('profile.index'))
