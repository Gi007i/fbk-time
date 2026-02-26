"""Category model.

Provides the Category model for absence categories.
"""

from datetime import datetime, timezone

from core.extensions import db


def _utc_now():
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


class Category(db.Model):
    """Absence category with visual styling and substitute requirement.

    Deletion is restricted if absences exist (handled in application logic).
    """

    __tablename__ = 'categories'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    color = db.Column(db.String(7), nullable=False)  # e.g., "#FF5733"
    text_color = db.Column(db.String(7), nullable=False, default='#FFFFFF')
    icon = db.Column(db.String(50), nullable=True)
    requires_substitute = db.Column(db.Boolean, default=False, nullable=False)
    is_present = db.Column(db.Boolean, default=False, nullable=False)  # True = working remotely, False = absent
    sort_order = db.Column(db.Integer, default=0)
    active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=_utc_now)

    # Relationship - RESTRICT delete behavior handled in application
    absences = db.relationship(
        'Absence',
        backref='category',
        lazy='dynamic'
    )

    def __repr__(self):
        return f'<Category {self.name}>'
