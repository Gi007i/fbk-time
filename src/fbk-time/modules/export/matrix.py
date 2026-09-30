"""Team overview matrix (users × days) as PDF."""

from calendar import monthrange
from datetime import datetime, date, timedelta
from functools import cache
from io import BytesIO
from pathlib import Path
from typing import List, Optional
from xml.sax.saxutils import escape

from flask import current_app
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.platypus import Image as PdfImage
from sqlalchemy import select

from core.db import db
from core.timezone import get_app_timezone
from modules.auth.models import User, UserRole, UserStatus
from modules.category.helpers import twemoji_stem
from modules.holidays.services import get_holidays_for_month
from modules.absence.recurrence import recurrence_service
from modules.absence.services import (
    filter_occurrences,
    get_active_categories,
    get_legend_categories,
)
from utils.helpers import format_date_for_user
from .pdf import HalfDayCell, ICON_MAX_SIDE
from .services import build_absence_query


MONTH_NAMES = [
    '', 'Januar', 'Februar', 'März', 'April', 'Mai', 'Juni',
    'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'
]
WEEKDAY_NAMES = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']


def _hex_to_color(hex_str: str) -> colors.Color:
    """Convert a ``#RRGGBB`` hex string to a ReportLab Color.

    Args:
        hex_str: Color string with or without a leading ``#``.

    Returns:
        Equivalent ``colors.Color`` instance.
    """
    hex_value = hex_str.lstrip('#')
    return colors.Color(
        int(hex_value[0:2], 16) / 255.0,
        int(hex_value[2:4], 16) / 255.0,
        int(hex_value[4:6], 16) / 255.0
    )


@cache
def _icon_png_path(icon: Optional[str]) -> Optional[str]:
    """Resolve a category icon to its bundled Twemoji PNG path.

    Returns None when the icon has no bundled asset; those cells render
    without artwork, mirroring the web views.

    Args:
        icon: Emoji string as stored on the category.

    Returns:
        Absolute path to the PNG asset, or None.
    """
    stem = twemoji_stem(icon)
    if not stem:
        return None
    path = Path(current_app.static_folder) / 'img' / 'twemoji' / '72' / f'{stem}.png'
    return str(path) if path.is_file() else None


def _split_into_months(range_start, range_end):
    """Split a date range into per-month (first_day, last_day) tuples.

    Args:
        range_start: Start date of the range.
        range_end: End date of the range.

    Returns:
        List of (month_start, month_end) tuples.
    """
    chunks = []
    current = range_start
    while current <= range_end:
        _, days_in_month = monthrange(current.year, current.month)
        month_end = date(current.year, current.month, days_in_month)
        chunk_start = max(current, date(current.year, current.month, 1))
        chunk_end = min(range_end, month_end)
        chunks.append((chunk_start, chunk_end))
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return chunks


def _build_matrix_page(
    elements, users, chunk_start, chunk_end, holidays, matrix, title_style,
    subtitle_style, is_first_page
):
    """Build matrix elements for a single date chunk (page).

    Args:
        elements: List to append platypus elements to.
        users: List of User objects.
        chunk_start: Start date for this chunk.
        chunk_end: End date for this chunk.
        holidays: Dict of holiday dates.
        matrix: Dict of (user_id, date) → occurrence.
        title_style: ParagraphStyle for title.
        subtitle_style: ParagraphStyle for subtitle.
        is_first_page: Whether this is the first page.
    """
    num_days = (chunk_end - chunk_start).days + 1

    if chunk_start.month == chunk_end.month:
        title_text = f'Team-Übersicht {MONTH_NAMES[chunk_start.month]} {chunk_start.year}'
    else:
        title_text = (
            f'Team-Übersicht {format_date_for_user(chunk_start, short=True)} - '
            f'{format_date_for_user(chunk_end)}'
        )

    elements.append(Paragraph(title_text, title_style))
    if is_first_page:
        elements.append(Paragraph(
            f'Erstellt am {format_date_for_user(datetime.now(get_app_timezone()), include_time=True)}',
            subtitle_style
        ))
    elements.append(Spacer(1, 5 * mm))

    header = ['Person']
    days_list = []
    for day_offset in range(num_days):
        current_date = chunk_start + timedelta(days=day_offset)
        days_list.append(current_date)
        weekday = WEEKDAY_NAMES[current_date.weekday()]
        header.append(f'{current_date.day}\n{weekday}')

    data = [header]

    name_width = 30 * mm if num_days > 14 else (40 * mm if num_days > 7 else 50 * mm)
    day_width = (landscape(A4)[0] - 20 * mm - name_width) / num_days
    col_widths = [name_width] + [day_width] * num_days
    cell_height = 6 * mm

    half_day_cells = set()

    for user in users:
        row = [user.name]
        for day_idx, current_date in enumerate(days_list):
            occ = matrix.get((user.id, current_date))

            category = occ['category'] if occ else None

            # An occurrence can lose its effective category when a series
            # override outlives the category it pointed at. The cell still has
            # to be emitted, otherwise the whole row shifts by one day.
            if occ and category is not None:
                if occ.get('is_combined_half_day') and occ.get('category_afternoon'):
                    color_m = _hex_to_color(category.color)
                    cat_a = occ['category_afternoon']
                    color_a = _hex_to_color(cat_a.color)
                    half_day_cell = HalfDayCell(
                        day_width, cell_height, color_m,
                        color_afternoon=color_a,
                        icon_path=_icon_png_path(category.icon),
                        icon_path_afternoon=_icon_png_path(cat_a.icon)
                    )
                    row.append(half_day_cell)
                    row_idx = len(data)
                    half_day_cells.add((row_idx, day_idx + 1))
                elif occ['is_half_day_morning'] or occ['is_half_day_afternoon']:
                    cell_color = _hex_to_color(category.color)
                    half_day_cell = HalfDayCell(
                        day_width, cell_height, cell_color,
                        is_morning=occ['is_half_day_morning'],
                        icon_path=_icon_png_path(category.icon)
                    )
                    row.append(half_day_cell)
                    row_idx = len(data)
                    half_day_cells.add((row_idx, day_idx + 1))
                else:
                    icon_path = _icon_png_path(category.icon)
                    if icon_path:
                        icon_side = min(day_width * 0.75, ICON_MAX_SIDE)
                        row.append(PdfImage(icon_path, width=icon_side, height=icon_side))
                    else:
                        row.append('•')
            else:
                row.append('')

        data.append(row)

    table = Table(data, colWidths=col_widths, repeatRows=1)

    style_commands = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2563EB')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('TOPPADDING', (0, 0), (-1, 0), 6),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 5),
        ('TOPPADDING', (0, 1), (-1, -1), 5),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F3F4F6')]),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]

    for day_idx, current_date in enumerate(days_list):
        col = day_idx + 1
        if current_date in holidays:
            style_commands.append(
                ('BACKGROUND', (col, 0), (col, 0), colors.HexColor('#FEF3C7'))
            )
            style_commands.append(
                ('TEXTCOLOR', (col, 0), (col, 0), colors.HexColor('#92400E'))
            )

    # Explicit white keeps the zebra stripe out of the unfilled half of a
    # HalfDayCell.
    for (row_idx, col) in half_day_cells:
        style_commands.append(
            ('BACKGROUND', (col, row_idx), (col, row_idx), colors.white)
        )

    for row_idx, user in enumerate(users, start=1):
        for day_idx, current_date in enumerate(days_list):
            col = day_idx + 1
            occ = matrix.get((user.id, current_date))

            if occ and occ['category']:
                if (row_idx, col) in half_day_cells:
                    continue
                category = occ['category']
                style_commands.append(
                    ('BACKGROUND', (col, row_idx), (col, row_idx), _hex_to_color(category.color))
                )
                style_commands.append(
                    ('TEXTCOLOR', (col, row_idx), (col, row_idx), _hex_to_color(category.text_color))
                )

    table.setStyle(TableStyle(style_commands))
    elements.append(table)


def _build_legend(elements, styles, available_width, categories):
    """Build category legend and presence hint.

    Args:
        elements: List to append platypus elements to.
        styles: Base stylesheet.
        available_width: Frame width; the legend wraps into as many rows as
            needed to stay within it.
        categories: Categories to list, in display order.
    """
    elements.append(Spacer(1, 5 * mm))

    label_width = 25 * mm
    entry_width = 35 * mm
    per_row = max(1, int((available_width - label_width) // entry_width))

    # The table TEXTCOLOR command does not reach into Paragraphs, hence the
    # per-entry textColor style.
    def entry_style(name, text_color):
        return ParagraphStyle(
            name,
            parent=styles['Normal'],
            fontSize=8,
            alignment=TA_CENTER,
            textColor=text_color
        )

    entries = []
    for cat in categories:
        presence = '(A)' if cat.is_present else '(X)'
        icon_path = _icon_png_path(cat.icon)
        icon_markup = (
            f'<img src="{icon_path}" width="8" height="8" valign="-1"/> '
            if icon_path else ''
        )
        entries.append((
            Paragraph(
                f'{icon_markup}{escape(cat.name)} {presence}',
                entry_style(f'LegendCategory{cat.id}', _hex_to_color(cat.text_color))
            ),
            _hex_to_color(cat.color)
        ))
    entries.append((
        Paragraph('Feiertag', entry_style('LegendHoliday', colors.black)),
        colors.HexColor('#FEF3C7')
    ))

    label_style = ParagraphStyle(
        'LegendLabel',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8
    )

    legend_data = []
    legend_styles = [
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ]
    for row_idx, offset in enumerate(range(0, len(entries), per_row)):
        chunk = entries[offset:offset + per_row]
        row = [Paragraph('Legende:', label_style) if row_idx == 0 else '']
        for col, (paragraph, background) in enumerate(chunk, start=1):
            row.append(paragraph)
            legend_styles.append(('BACKGROUND', (col, row_idx), (col, row_idx), background))
        row.extend([''] * (per_row - len(chunk)))
        legend_data.append(row)

    legend_table = Table(legend_data, colWidths=[label_width] + [entry_width] * per_row)
    legend_table.setStyle(TableStyle(legend_styles))
    elements.append(legend_table)

    presence_hint_style = ParagraphStyle(
        'PresenceHint',
        parent=styles['Normal'],
        fontSize=7,
        textColor=colors.grey
    )
    elements.append(Spacer(1, 2 * mm))
    elements.append(Paragraph('(A) = Anwesend, (X) = Abwesend', presence_hint_style))


def export_team_matrix_pdf(
    week_start: date,
    week_end: date,
    users: Optional[List[User]] = None,
    user_ids: Optional[List[int]] = None,
    category_ids: Optional[List[int]] = None,
    has_substitute: Optional[str] = None,
    filter_summary: Optional[str] = None
) -> BytesIO:
    """Export team overview matrix (users × days) as PDF.

    A range spanning several months gets one page per month.

    Args:
        week_start: Start date of the range.
        week_end: End date of the range.
        users: Optional list of users. If None, gets all active.
        user_ids: Optional person filter (any of the given IDs; applied only
            when ``users`` is None).
        category_ids: Optional category filter on the occurrence level.
        has_substitute: Optional 'yes'/'no' substitute filter.
        filter_summary: Active filter description shown in the footer.

    Returns:
        BytesIO buffer containing PDF data.
    """
    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=10 * mm,
        leftMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=14,
        spaceAfter=5 * mm
    )
    subtitle_style = ParagraphStyle(
        'CustomSubtitle',
        parent=styles['Normal'],
        fontSize=9,
        textColor=colors.grey
    )

    elements = []

    if users is None:
        user_query = select(User).where(
            User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED]),
            User.role == UserRole.USER
        )
        if user_ids:
            user_query = user_query.where(User.id.in_(user_ids))
        users = db.session.scalars(user_query.order_by(User.name)).all()

    if not users:
        elements.append(Paragraph('Keine Mitarbeitenden gefunden.', styles['Normal']))
        doc.build(elements)
        buffer.seek(0)
        return buffer

    holidays = {}
    current_month_check = date(week_start.year, week_start.month, 1)
    end_month_check = date(week_end.year, week_end.month, 1)
    while current_month_check <= end_month_check:
        holidays.update(get_holidays_for_month(current_month_check.year, current_month_check.month))
        if current_month_check.month == 12:
            current_month_check = date(current_month_check.year + 1, 1, 1)
        else:
            current_month_check = date(current_month_check.year, current_month_check.month + 1, 1)

    # Restrict to the rendered rows to avoid loading unrelated absences.
    user_ids = [u.id for u in users]
    absences = db.session.scalars(
        build_absence_query(
            from_date=week_start,
            to_date=week_end,
            user_ids=user_ids
        )
    ).all()

    occurrences = recurrence_service.get_all_occurrences_for_range(
        absences, week_start, week_end
    )
    occurrences = filter_occurrences(
        occurrences, category_ids=category_ids, has_substitute=has_substitute
    )

    matrix = {}
    for occ in occurrences:
        key = (occ['user_id'], occ['date'])
        if key in matrix:
            existing = matrix[key]
            if existing.get('is_combined_half_day'):
                continue
            if existing['is_half_day_morning'] and occ['is_half_day_afternoon']:
                existing['is_half_day_afternoon'] = True
                existing['is_combined_half_day'] = True
                existing['absence_afternoon'] = occ['absence']
                existing['category_afternoon'] = occ['category']
                existing['is_recurring_afternoon'] = occ['is_recurring']
                continue
            if existing['is_half_day_afternoon'] and occ['is_half_day_morning']:
                existing['is_half_day_morning'] = True
                existing['is_combined_half_day'] = True
                existing['absence_afternoon'] = existing['absence']
                existing['category_afternoon'] = existing['category']
                existing['is_recurring_afternoon'] = existing['is_recurring']
                existing['absence'] = occ['absence']
                existing['category'] = occ['category']
                existing['is_recurring'] = occ['is_recurring']
                continue
        matrix[key] = occ

    # Merging a half day moves a category into the afternoon slot.
    legend_categories = get_legend_categories(get_active_categories(), (
        category
        for entry in matrix.values()
        for category in (entry['category'], entry.get('category_afternoon'))
    ))
    month_chunks = _split_into_months(week_start, week_end)
    use_monthly_pages = len(month_chunks) > 1

    if use_monthly_pages:
        for chunk_idx, (chunk_start, chunk_end) in enumerate(month_chunks):
            if chunk_idx > 0:
                elements.append(PageBreak())
            _build_matrix_page(
                elements, users, chunk_start, chunk_end, holidays, matrix,
                title_style, subtitle_style,
                is_first_page=(chunk_idx == 0)
            )
            _build_legend(elements, styles, doc.width, legend_categories)
    else:
        _build_matrix_page(
            elements, users, week_start, week_end, holidays, matrix,
            title_style, subtitle_style,
            is_first_page=True
        )
        _build_legend(elements, styles, doc.width, legend_categories)

    present_count = sum(1 for occ in occurrences if occ['category'] and occ['category'].is_present)
    absent_count = len(occurrences) - present_count
    elements.append(Spacer(1, 3 * mm))
    elements.append(Paragraph(
        f'Gesamt: {len(users)} Personen, {len(occurrences)} Termine ({present_count} Anwesenheit(en), {absent_count} Abwesenheit(en))',
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
