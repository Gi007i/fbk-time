"""Team matrix PDF export service.

Provides team overview matrix (users × days) as PDF.
"""

from calendar import monthrange
from datetime import datetime, date, timedelta
from io import BytesIO
from typing import List, Optional
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak

from core.timezone import get_app_timezone
from modules.auth.models import User, UserRole, UserStatus
from modules.category.models import Category
from modules.holidays.services import get_holidays_for_month
from modules.absence.recurrence import recurrence_service
from modules.absence.services import filter_occurrences
from utils.helpers import format_date_for_user
from .pdf import HalfDayCell
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

            if occ:
                category = occ['category']
                if occ.get('is_combined_half_day'):
                    color_m = _hex_to_color(category.color)
                    cat_a = occ['category_afternoon']
                    color_a = _hex_to_color(cat_a.color)
                    half_day_cell = HalfDayCell(
                        day_width, cell_height, color_m,
                        color_afternoon=color_a
                    )
                    row.append(half_day_cell)
                    row_idx = len(data)
                    half_day_cells.add((row_idx, day_idx + 1))
                elif occ['is_half_day_morning'] or occ['is_half_day_afternoon']:
                    cell_color = _hex_to_color(category.color)
                    half_day_cell = HalfDayCell(
                        day_width, cell_height, cell_color,
                        is_morning=occ['is_half_day_morning']
                    )
                    row.append(half_day_cell)
                    row_idx = len(data)
                    half_day_cells.add((row_idx, day_idx + 1))
                else:
                    row.append('•')
            else:
                row.append('')

        data.append(row)

    table = Table(data, colWidths=col_widths, repeatRows=1)

    style_commands = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#3B82F6')),
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

    # Half-day cells must override the alternating row background so that
    # the unfilled half of the HalfDayCell does not inherit the zebra
    # stripe color. Force an explicit white background behind the flowable.
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


def _build_legend(elements, styles):
    """Build category legend and presence hint.

    Args:
        elements: List to append platypus elements to.
        styles: Base stylesheet.
    """
    elements.append(Spacer(1, 5 * mm))
    categories = Category.query.filter_by(active=True).order_by(Category.sort_order).all()

    legend_data = [['Legende:']]
    for cat in categories:
        presence = '[A]' if cat.is_present else '[X]'
        legend_data[0].append(f'{cat.name} {presence}')

    legend_data[0].append('Feiertag')

    legend_table = Table(legend_data, colWidths=[25 * mm] + [35 * mm] * (len(categories) + 1))

    legend_styles = [
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('FONTNAME', (0, 0), (0, 0), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ]

    for idx, cat in enumerate(categories, start=1):
        legend_styles.append(('BACKGROUND', (idx, 0), (idx, 0), _hex_to_color(cat.color)))
        legend_styles.append(('TEXTCOLOR', (idx, 0), (idx, 0), _hex_to_color(cat.text_color)))

    holiday_col = len(categories) + 1
    legend_styles.append(('BACKGROUND', (holiday_col, 0), (holiday_col, 0), colors.HexColor('#FEF3C7')))

    legend_table.setStyle(TableStyle(legend_styles))
    elements.append(legend_table)

    presence_hint_style = ParagraphStyle(
        'PresenceHint',
        parent=styles['Normal'],
        fontSize=7,
        textColor=colors.grey
    )
    elements.append(Spacer(1, 2 * mm))
    elements.append(Paragraph('[A] = Anwesend, [X] = Abwesend', presence_hint_style))


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

    For ranges spanning multiple months, each month gets its own page.
    For ranges within a single month, a single page is generated.

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
        user_query = User.query.filter(
            User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED]),
            User.role == UserRole.USER
        )
        if user_ids:
            user_query = user_query.filter(User.id.in_(user_ids))
        users = user_query.order_by(User.name).all()

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

    # Restrict to the explicit user list so per-user matrix exports
    # also include Admin/Manager rows that the default USER role filter
    # would otherwise drop, and to avoid loading unrelated absences.
    user_ids = [u.id for u in users]
    absences = build_absence_query(
        from_date=week_start,
        to_date=week_end,
        user_ids=user_ids
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
            _build_legend(elements, styles)
    else:
        _build_matrix_page(
            elements, users, week_start, week_end, holidays, matrix,
            title_style, subtitle_style,
            is_first_page=True
        )
        _build_legend(elements, styles)

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
