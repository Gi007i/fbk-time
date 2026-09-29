"""PDF export of absence lists and the half-day cell flowable."""

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


# Cap category icons at roughly a 9pt text line so matrix rows keep a
# uniform height and full-day and half-day icons render the same size.
ICON_MAX_SIDE = 11


class HalfDayCell(Flowable):
    """Custom Flowable for half-day visualization in PDF table cells."""

    def __init__(self, width, height, color, is_morning=True, color_afternoon=None,
                 icon_path=None, icon_path_afternoon=None):
        Flowable.__init__(self)
        self.width = width
        self.height = height
        self.color = color
        self.is_morning = is_morning
        self.color_afternoon = color_afternoon
        self.icon_path = icon_path
        self.icon_path_afternoon = icon_path_afternoon

    def _draw_icon(self, icon_path, center_x):
        """Draw a category icon centered on center_x within the cell height."""
        side = min(min(self.width / 2, self.height) * 0.75, ICON_MAX_SIDE)
        self.canv.drawImage(
            icon_path, center_x - side / 2, (self.height - side) / 2,
            side, side, mask='auto'
        )

    def draw(self):
        """Draw half-colored rectangles (left morning, right afternoon) with icons."""
        self.canv.saveState()
        if self.color_afternoon:
            self.canv.setFillColor(self.color)
            self.canv.rect(0, 0, self.width / 2, self.height, fill=1, stroke=0)
            self.canv.setFillColor(self.color_afternoon)
            self.canv.rect(self.width / 2, 0, self.width / 2, self.height, fill=1, stroke=0)
            if self.icon_path:
                self._draw_icon(self.icon_path, self.width * 0.25)
            if self.icon_path_afternoon:
                self._draw_icon(self.icon_path_afternoon, self.width * 0.75)
        else:
            self.canv.setFillColor(self.color)
            if self.is_morning:
                self.canv.rect(0, 0, self.width / 2, self.height, fill=1, stroke=0)
                if self.icon_path:
                    self._draw_icon(self.icon_path, self.width * 0.25)
            else:
                self.canv.rect(self.width / 2, 0, self.width / 2, self.height, fill=1, stroke=0)
                if self.icon_path:
                    self._draw_icon(self.icon_path, self.width * 0.75)
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

    Only renders; loading, expanding and filtering are left to the caller.

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
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2563EB')),
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
