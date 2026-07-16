"""iCal export service.

Provides iCal/ICS export functionality for absences. Consumes
pre-expanded occurrences so that category and substitute filters
operate on effective occurrence state.
"""

from datetime import date, datetime, time, timedelta, timezone
from io import BytesIO
from typing import List, Tuple, Union

from icalendar import Calendar, Event

from core.timezone import get_app_timezone


_MORNING_START = time(8, 0)
_MORNING_END = time(12, 0)
_AFTERNOON_START = time(12, 0)
_AFTERNOON_END = time(17, 0)


def _as_utc(value):
    """Return a timezone-aware UTC datetime.

    SQLite stores ``DateTime`` columns without timezone info, so values
    round-tripped through the DB come back naive even though the app
    writes them with ``datetime.now(timezone.utc)``. RFC 5545 requires
    CREATED/LAST-MODIFIED to be in UTC, so naive values are interpreted
    as UTC and converted to tz-aware before being added to iCal.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def export_absences_ical(
    occurrences: List[dict],
    calendar_name: str = 'FBK-Time Abwesenheiten'
) -> BytesIO:
    """Export pre-expanded occurrences to iCal format.

    Each occurrence becomes a standalone VEVENT. RRULE compression is
    not used so that category and substitute filters can be applied
    per-occurrence at the caller level.

    Half-day and custom-time occurrences are exported as timed events
    (DTSTART/DTEND with datetime + tzid) so calendar clients render
    them as partial-day blocks rather than full-day entries.

    Args:
        occurrences: Pre-expanded, pre-filtered occurrence dicts.
        calendar_name: Name for the calendar.

    Returns:
        BytesIO buffer containing iCal data.
    """
    cal = Calendar()
    cal.add('prodid', '-//FBK-Time//Abwesenheitsverwaltung//DE')
    cal.add('version', '2.0')
    cal.add('calscale', 'GREGORIAN')
    cal.add('method', 'PUBLISH')
    cal.add('x-wr-calname', calendar_name)

    for occ in occurrences:
        absence = occ['absence']
        user = occ.get('user')
        category = occ.get('category')

        user_name = user.name if user else 'Unbekannt'
        category_name = category.name if category else 'Abwesenheit'

        event = Event()
        event.add('summary', f'{user_name}: {category_name}')

        dtstart, dtend = _compute_event_bounds(occ)
        event.add('dtstart', dtstart)
        event.add('dtend', dtend)
        event.add(
            'uid',
            f'absence-{absence.id}-{occ["date"].isoformat()}@fbk-time'
        )

        description_parts = [
            f'Person: {user_name}',
            f'Kategorie: {category_name}'
        ]

        if occ.get('is_recurring'):
            if occ.get('is_exception'):
                description_parts.append('Serie: Geänderte Instanz')
            else:
                description_parts.append('Serie: Ja')

        if occ.get('is_half_day_morning'):
            description_parts.append('Zeitraum: Halbtags Vormittag')
        elif occ.get('is_half_day_afternoon'):
            description_parts.append('Zeitraum: Halbtags Nachmittag')
        elif occ.get('start_time') and occ.get('end_time'):
            description_parts.append(
                f'Zeitraum: {occ["start_time"].strftime("%H:%M")} - '
                f'{occ["end_time"].strftime("%H:%M")}'
            )

        substitute = occ.get('substitute')
        if substitute:
            description_parts.append(f'Vertretung: {substitute.name}')

        notes = occ.get('notes')
        if notes:
            description_parts.append(f'Notizen: {notes}')

        event.add('description', '\n'.join(description_parts))

        if category and category.is_present:
            event.add('transp', 'TRANSPARENT')
            event['x-microsoft-cdo-busystatus'] = 'FREE'
        else:
            event.add('transp', 'OPAQUE')
            event['x-microsoft-cdo-busystatus'] = 'OOF'

        event.add('dtstamp', datetime.now(timezone.utc))
        created_utc = _as_utc(absence.created_at)
        if created_utc is not None:
            event.add('created', created_utc)
        updated_utc = _as_utc(absence.updated_at)
        if updated_utc is not None:
            event.add('last-modified', updated_utc)
        event.add('sequence', 0)

        cal.add_component(event)

    buffer = BytesIO()
    buffer.write(cal.to_ical())
    buffer.seek(0)
    return buffer


_EventBound = Union[datetime, date]


def _compute_event_bounds(occ: dict) -> Tuple[_EventBound, _EventBound]:
    """Return (dtstart, dtend) for a single occurrence.

    - All-day occurrence: date values, exclusive DTEND = next day.
    - Half-day morning:   08:00 - 12:00 local time.
    - Half-day afternoon: 12:00 - 17:00 local time.
    - Custom start/end:   the supplied times in local timezone.
    """
    occ_date = occ['date']

    if occ.get('is_half_day_morning'):
        return (
            datetime.combine(occ_date, _MORNING_START, tzinfo=get_app_timezone()),
            datetime.combine(occ_date, _MORNING_END, tzinfo=get_app_timezone())
        )

    if occ.get('is_half_day_afternoon'):
        return (
            datetime.combine(occ_date, _AFTERNOON_START, tzinfo=get_app_timezone()),
            datetime.combine(occ_date, _AFTERNOON_END, tzinfo=get_app_timezone())
        )

    start_t = occ.get('start_time')
    end_t = occ.get('end_time')
    if start_t and end_t:
        return (
            datetime.combine(occ_date, start_t, tzinfo=get_app_timezone()),
            datetime.combine(occ_date, end_t, tzinfo=get_app_timezone())
        )

    return (occ_date, occ_date + timedelta(days=1))
