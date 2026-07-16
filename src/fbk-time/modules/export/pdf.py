"""PDF export service.

Provides PDF export functionality for absences and reports.
"""

from datetime import datetime, date
from io import BytesIO
from typing import List, Optional
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Flowable, PageBreak

from core.timezone import get_app_timezone
from utils.helpers import format_date_for_user


class HalfDayCell(Flowable):
    """Custom Flowable for half-day visualization in PDF table cells."""

    def __init__(self, width, height, color, is_morning=True, color_afternoon=None):
        Flowable.__init__(self)
        self.width = width
        self.height = height
        self.color = color
        self.is_morning = is_morning
        self.color_afternoon = color_afternoon

    def draw(self):
        """Draw a half-colored rectangle (left for morning, right for afternoon)."""
        self.canv.saveState()
        if self.color_afternoon:
            self.canv.setFillColor(self.color)
            self.canv.rect(0, 0, self.width / 2, self.height, fill=1, stroke=0)
            self.canv.setFillColor(self.color_afternoon)
            self.canv.rect(self.width / 2, 0, self.width / 2, self.height, fill=1, stroke=0)
        else:
            self.canv.setFillColor(self.color)
            if self.is_morning:
                self.canv.rect(0, 0, self.width / 2, self.height, fill=1, stroke=0)
            else:
                self.canv.rect(self.width / 2, 0, self.width / 2, self.height, fill=1, stroke=0)
        self.canv.restoreState()


def export_absences_pdf(
    occurrences: List[dict],
    title: str = 'Abwesenheitsübersicht',
    include_notes: bool = False,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    date_format: str = 'DD.MM.YYYY',
    filter_summary: Optional[str] = None
) -> BytesIO:
    """Export pre-expanded occurrences to PDF document.

    The caller is responsible for loading absences, expanding them with
    recurrence_service, and applying any category/substitute filters.
    This function only renders.

    Args:
        occurrences: Pre-expanded, pre-filtered occurrence dicts.
        title: Document title.
        include_notes: Whether to include occurrence notes.
        date_from: Start date (for header display only).
        date_to: End date (for header display only).
        date_format: Date display format ('DD.MM.YYYY' or 'YYYY-MM-DD').
        filter_summary: Active filter description shown in the footer.

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

    elements.append(Paragraph(escape(title), title_style))
    elements.append(Paragraph(
        f'Erstellt am {format_date_for_user(datetime.now(get_app_timezone()), include_time=True)}',
        subtitle_style
    ))
    if date_from and date_to:
        elements.append(Paragraph(
            f'Zeitraum: {format_date_for_user(date_from)} - {format_date_for_user(date_to)}',
            subtitle_style
        ))
    elements.append(Spacer(1, 10 * mm))

    if not occurrences:
        elements.append(Paragraph('Keine Abwesenheiten gefunden.', styles['Normal']))
    else:
        if include_notes:
            headers = ['Person', 'Kategorie', 'Datum', 'Zeitraum', 'Vertretung', 'Serie', 'Notizen']
            col_widths = [35 * mm, 28 * mm, 25 * mm, 20 * mm, 30 * mm, 18 * mm, 30 * mm]
        else:
            headers = ['Person', 'Kategorie', 'Datum', 'Zeitraum', 'Vertretung', 'Serie']
            col_widths = [45 * mm, 35 * mm, 28 * mm, 25 * mm, 35 * mm, 20 * mm]

        fmt = '%d.%m.%Y' if date_format == 'DD.MM.YYYY' else '%Y-%m-%d'

        month_names = [
            '', 'Januar', 'Februar', 'März', 'April', 'Mai', 'Juni',
            'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'
        ]

        grouped = {}
        for occ in occurrences:
            key = (occ['date'].year, occ['date'].month)
            grouped.setdefault(key, []).append(occ)

        month_heading_style = ParagraphStyle(
            'MonthHeading',
            parent=styles['Heading2'],
            fontSize=12,
            spaceBefore=0,
            spaceAfter=5 * mm
        )

        table_style_commands = [
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
        ]

        use_monthly_pages = len(grouped) > 1

        for month_idx, ((year, month), month_occs) in enumerate(grouped.items()):
            if use_monthly_pages and month_idx > 0:
                elements.append(PageBreak())

            if use_monthly_pages:
                elements.append(Paragraph(
                    f'{month_names[month]} {year}',
                    month_heading_style
                ))

            data = [headers]
            for occ in month_occs:
                category = occ['category']

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

                occ_substitute = occ.get('substitute')
                occ_notes = occ.get('notes')

                row = [
                    occ['user'].name if occ['user'] else '-',
                    category_text,
                    occ['date'].strftime(fmt),
                    time_type_text,
                    occ_substitute.name if occ_substitute else '-',
                    series_text
                ]
                if include_notes:
                    notes = occ_notes[:50] + '...' if occ_notes and len(occ_notes) > 50 else (occ_notes or '-')
                    row.append(notes)

                data.append(row)

            table = Table(data, colWidths=col_widths, repeatRows=1)
            table.setStyle(TableStyle(table_style_commands))
            elements.append(table)

            if use_monthly_pages:
                present_count = sum(1 for o in month_occs if o['category'] and o['category'].is_present)
                absent_count = len(month_occs) - present_count
                elements.append(Spacer(1, 5 * mm))
                elements.append(Paragraph(
                    f'{len(month_occs)} Termine ({present_count} Anwesenheit(en), {absent_count} Abwesenheit(en))',
                    styles['Normal']
                ))

        present_count = sum(1 for occ in occurrences if occ['category'] and occ['category'].is_present)
        absent_count = len(occurrences) - present_count
        elements.append(Spacer(1, 10 * mm))
        elements.append(Paragraph(
            f'Gesamt: {len(occurrences)} Termine ({present_count} Anwesenheit(en), {absent_count} Abwesenheit(en))',
            styles['Normal']
        ))

    if filter_summary:
        elements.append(Spacer(1, 3 * mm))
        elements.append(Paragraph(
            f'<b>Gefiltert nach:</b> {escape(filter_summary)}',
            subtitle_style
        ))

    doc.build(elements)
    buffer.seek(0)
    return buffer


def export_user_absences_pdf(
    user,
    year: Optional[int] = None,
    date_format: str = 'DD.MM.YYYY'
) -> BytesIO:
    """Export all occurrences for a specific user in a given year.

    Args:
        user: User to export absences for.
        year: Optional year filter. Defaults to current year.
        date_format: Date display format ('DD.MM.YYYY' or 'YYYY-MM-DD').

    Returns:
        BytesIO buffer containing PDF data.
    """
    from .services import build_export_occurrences

    if year is None:
        year = date.today().year

    date_from = date(year, 1, 1)
    date_to = date(year, 12, 31)

    occurrences = build_export_occurrences(
        from_date=date_from,
        to_date=date_to,
        user_ids=[user.id]
    )

    return export_absences_pdf(
        occurrences,
        title=f'Abwesenheiten {user.name} - {year}',
        include_notes=True,
        date_from=date_from,
        date_to=date_to,
        date_format=date_format
    )


