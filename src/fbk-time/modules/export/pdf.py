"""PDF export service.

Provides PDF export functionality for absences and reports.
"""

from datetime import datetime, date
from io import BytesIO
from typing import List, Optional
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Flowable

from flask_login import current_user
from modules.absence.recurrence import recurrence_service
from utils.helpers import format_date_for_user


class HalfDayCell(Flowable):
    """Custom Flowable for half-day visualization in PDF table cells."""

    def __init__(self, width, height, color, is_morning=True):
        Flowable.__init__(self)
        self.width = width
        self.height = height
        self.color = color
        self.is_morning = is_morning

    def draw(self):
        """Draw a half-colored rectangle (left for morning, right for afternoon)."""
        self.canv.saveState()
        self.canv.setFillColor(self.color)
        if self.is_morning:
            self.canv.rect(0, 0, self.width / 2, self.height, fill=1, stroke=0)
        else:
            self.canv.rect(self.width / 2, 0, self.width / 2, self.height, fill=1, stroke=0)
        self.canv.restoreState()


def export_absences_pdf(
    absences: List,
    title: str = 'Abwesenheitsübersicht',
    include_notes: bool = False,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None
) -> BytesIO:
    """Export absences to PDF document with expanded occurrences.

    Args:
        absences: List of Absence records to export.
        title: Document title.
        include_notes: Whether to include absence notes.
        date_from: Start date for occurrence expansion.
        date_to: End date for occurrence expansion.

    Returns:
        BytesIO buffer containing PDF data.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=16,
        spaceAfter=10 * mm
    )
    subtitle_style = ParagraphStyle(
        'CustomSubtitle',
        parent=styles['Normal'],
        fontSize=10,
        textColor=colors.grey
    )

    elements = []

    elements.append(Paragraph(title, title_style))
    elements.append(Paragraph(
        f'Erstellt am {format_date_for_user(datetime.now(ZoneInfo("Europe/Berlin")), include_time=True)}',
        subtitle_style
    ))
    elements.append(Spacer(1, 10 * mm))

    if date_from and date_to:
        occurrences = recurrence_service.get_all_occurrences_for_range(
            absences, date_from, date_to
        )
        occurrences.sort(key=lambda o: o['date'])
    else:
        occurrences = []

    if not occurrences:
        elements.append(Paragraph('Keine Abwesenheiten gefunden.', styles['Normal']))
    else:
        if include_notes:
            headers = ['Person', 'Kategorie', 'Datum', 'Zeitraum', 'Vertretung', 'Serie', 'Notizen']
            col_widths = [35 * mm, 28 * mm, 25 * mm, 20 * mm, 30 * mm, 18 * mm, 30 * mm]
        else:
            headers = ['Person', 'Kategorie', 'Datum', 'Zeitraum', 'Vertretung', 'Serie']
            col_widths = [45 * mm, 35 * mm, 28 * mm, 25 * mm, 35 * mm, 20 * mm]

        data = [headers]

        date_format_setting = current_user.date_format if current_user.is_authenticated else 'DD.MM.YYYY'
        fmt = '%d.%m.%Y' if date_format_setting == 'DD.MM.YYYY' else '%Y-%m-%d'

        for occ in occurrences:
            category = occ['category']
            absence = occ['absence']

            category_text = '-'
            if category:
                presence_type = '(A)' if category.is_present else '(X)'
                category_text = f'{category.name} {presence_type}'

            if occ['is_half_day_morning']:
                time_type_text = 'Vormittag'
            elif occ['is_half_day_afternoon']:
                time_type_text = 'Nachmittag'
            else:
                time_type_text = 'Ganztags'

            series_text = 'Ja' if occ['is_recurring'] else '-'
            if occ['is_exception']:
                series_text = 'Geändert'

            row = [
                occ['user'].name if occ['user'] else '-',
                category_text,
                occ['date'].strftime(fmt),
                time_type_text,
                absence.substitute.name if absence.substitute else '-',
                series_text
            ]
            if include_notes:
                notes = absence.notes[:50] + '...' if absence.notes and len(absence.notes) > 50 else (absence.notes or '-')
                row.append(notes)

            data.append(row)

        table = Table(data, colWidths=col_widths, repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#3B82F6')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('TOPPADDING', (0, 0), (-1, 0), 8),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
            ('TOPPADDING', (0, 1), (-1, -1), 6),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F3F4F6')]),
            ('ALIGN', (3, 0), (5, -1), 'CENTER'),
        ]))

        elements.append(table)

        present_count = sum(1 for occ in occurrences if occ['category'] and occ['category'].is_present)
        absent_count = len(occurrences) - present_count
        elements.append(Spacer(1, 10 * mm))
        elements.append(Paragraph(
            f'Gesamt: {len(occurrences)} Termine ({present_count} Anwesenheit(en), {absent_count} Abwesenheit(en))',
            styles['Normal']
        ))

    doc.build(elements)
    buffer.seek(0)
    return buffer


def export_user_absences_pdf(
    user,
    year: Optional[int] = None
) -> BytesIO:
    """Export all absences for a specific user.

    Args:
        user: User to export absences for.
        year: Optional year filter. Defaults to current year.

    Returns:
        BytesIO buffer containing PDF data.
    """
    from core.extensions import db
    from modules.absence.models import Absence

    if year is None:
        year = date.today().year

    date_from = date(year, 1, 1)
    date_to = date(year, 12, 31)

    absences = Absence.query.filter(
        Absence.user_id == user.id,
        Absence.start_date <= date_to
    ).filter(
        db.or_(
            Absence.end_date >= date_from,
            Absence.is_recurring == True
        )
    ).order_by(Absence.start_date).all()

    return export_absences_pdf(
        absences,
        title=f'Abwesenheiten {user.name} - {year}',
        include_notes=True,
        date_from=date_from,
        date_to=date_to
    )


def export_category_absences_pdf(
    category,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None
) -> BytesIO:
    """Export all absences for a specific category.

    Args:
        category: Category to export absences for.
        start_date: Optional start date filter.
        end_date: Optional end date filter.

    Returns:
        BytesIO buffer containing PDF data.
    """
    from core.extensions import db
    from modules.absence.models import Absence
    from modules.auth.models import User, UserRole, UserStatus

    if not start_date:
        start_date = date(date.today().year, 1, 1)
    if not end_date:
        end_date = date(date.today().year, 12, 31)

    user_status_filter = User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED])

    query = Absence.query.join(
        User, Absence.user_id == User.id
    ).filter(
        user_status_filter,
        User.role == UserRole.USER,
        Absence.category_id == category.id,
        Absence.start_date <= end_date
    ).filter(
        db.or_(
            Absence.end_date >= start_date,
            Absence.is_recurring == True
        )
    )

    absences = query.order_by(Absence.start_date).all()

    date_range = f' ({format_date_for_user(start_date)} - {format_date_for_user(end_date)})'

    return export_absences_pdf(
        absences,
        title=f'{category.name}{date_range}',
        include_notes=False,
        date_from=start_date,
        date_to=end_date
    )
