"""Handbook views.

Serves the in-app user manual. Chapter bodies are regular Jinja2 templates
under ``templates/handbook/content/`` and are included at render time.
"""

from flask import Blueprint, abort, redirect, render_template, url_for
from flask_login import login_required

from utils.session_navigation import save_return_url
from .services import (
    find_chapter,
    get_adjacent_chapters,
    get_chapter_template,
    get_chapters_by_section,
    get_default_chapter,
    get_handbook_settings,
)

bp = Blueprint('handbook', __name__, url_prefix='/handbook')


@bp.before_request
@login_required
def require_login():
    """Require login for all handbook routes."""
    pass


@bp.route('/')
def index():
    """Redirect to the first chapter of the handbook."""
    default = get_default_chapter()
    return redirect(url_for('handbook.chapter', slug=default['slug']))


@bp.route('/<slug>')
def chapter(slug):
    """Render a single chapter with the chapter navigation."""
    meta = find_chapter(slug)
    if meta is None:
        abort(404)

    content_template = get_chapter_template(slug)
    if content_template is None:
        abort(404)

    previous_chapter, next_chapter = get_adjacent_chapters(slug)

    save_return_url('Handbuch')
    return render_template(
        'handbook/chapter.html',
        sections=get_chapters_by_section(),
        current=meta,
        content_template=content_template,
        previous_chapter=previous_chapter,
        next_chapter=next_chapter,
        cfg=get_handbook_settings(),
    )
