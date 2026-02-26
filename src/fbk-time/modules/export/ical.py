"""iCal export service.

Provides iCal/ICS export functionality for absences.
"""

from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import List, Optional

from icalendar import Calendar, Event


def export_absences_ical(
    absences: List,
    calendar_name: str = 'FBK-Time Abwesenheiten'
) -> BytesIO:
    """Export absences to iCal format.

    Supports recurring absences with RRULE, EXDATE for deleted exceptions,
    and separate events with RECURRENCE-ID for modified exceptions.

    Args:
        absences: List of Absence records to export.
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

    for absence in absences:
        event = Event()

        user_name = absence.user.name if absence.user else 'Unbekannt'
        category_name = absence.category.name if absence.category else 'Abwesenheit'
        event.add('summary', f'{user_name}: {category_name}')

        event.add('dtstart', absence.start_date)
        event.add('dtend', absence.end_date + timedelta(days=1))  # iCal DTEND is exclusive

        event.add('uid', f'absence-{absence.id}@fbk-time')

        if absence.is_recurring and absence.rrule:
            rrule_dict = _parse_rrule_to_dict(absence.rrule)
            if rrule_dict:
                event.add('rrule', rrule_dict)

            deleted_dates = []

            for exc in absence.exceptions.all():
                if exc.exception_type == 'deleted':
                    deleted_dates.append(exc.exception_date)

            if deleted_dates:
                for del_date in deleted_dates:
                    event.add('exdate', del_date)

        description_parts = []
        description_parts.append(f'Person: {user_name}')
        description_parts.append(f'Kategorie: {category_name}')

        if absence.is_recurring:
            description_parts.append('Serie: Ja')

        if absence.is_half_day_morning:
            description_parts.append('Zeitraum: Halbtags Vormittag')
        elif absence.is_half_day_afternoon:
            description_parts.append('Zeitraum: Halbtags Nachmittag')
        elif absence.start_time and absence.end_time:
            description_parts.append(
                f'Zeitraum: {absence.start_time.strftime("%H:%M")} - '
                f'{absence.end_time.strftime("%H:%M")}'
            )

        if absence.substitute:
            description_parts.append(f'Vertretung: {absence.substitute.name}')

        if absence.notes:
            description_parts.append(f'Notizen: {absence.notes}')

        event.add('description', '\n'.join(description_parts))

        event.add('dtstamp', datetime.now(timezone.utc))
        if absence.created_at:
            event.add('created', absence.created_at)
        if absence.updated_at:
            event.add('last-modified', absence.updated_at)
        history_count = absence.history.count() if absence.history else 0
        event.add('sequence', max(0, history_count - 1))

        cal.add_component(event)

        if absence.is_recurring and absence.rrule:
            for exc in absence.exceptions.filter_by(exception_type='modified').all():
                exc_event = Event()

                exc_category = exc.modified_category if exc.modified_category else absence.category
                exc_category_name = exc_category.name if exc_category else category_name

                exc_event.add('summary', f'{user_name}: {exc_category_name}')
                exc_event.add('dtstart', exc.exception_date)
                exc_event.add('dtend', exc.exception_date + timedelta(days=1))
                exc_event.add('uid', f'absence-{absence.id}@fbk-time')
                exc_event.add('recurrence-id', exc.exception_date)

                exc_desc_parts = [
                    f'Person: {user_name}',
                    f'Kategorie: {exc_category_name}',
                    'Geänderte Instanz einer Serie'
                ]

                if exc.modified_is_half_day_morning:
                    exc_desc_parts.append('Zeitraum: Halbtags Vormittag')
                elif exc.modified_is_half_day_afternoon:
                    exc_desc_parts.append('Zeitraum: Halbtags Nachmittag')
                elif absence.is_half_day_morning:
                    exc_desc_parts.append('Zeitraum: Halbtags Vormittag')
                elif absence.is_half_day_afternoon:
                    exc_desc_parts.append('Zeitraum: Halbtags Nachmittag')

                exc_substitute = exc.modified_substitute if exc.modified_substitute else absence.substitute
                if exc_substitute:
                    exc_desc_parts.append(f'Vertretung: {exc_substitute.name}')

                exc_notes = exc.modified_notes if exc.modified_notes is not None else absence.notes
                if exc_notes:
                    exc_desc_parts.append(f'Notizen: {exc_notes}')

                exc_event.add('description', '\n'.join(exc_desc_parts))

                exc_event.add('dtstamp', datetime.now(timezone.utc))
                exc_event.add('sequence', max(0, history_count - 1))

                cal.add_component(exc_event)

    buffer = BytesIO()
    buffer.write(cal.to_ical())
    buffer.seek(0)
    return buffer


def _parse_rrule_to_dict(rrule_string: str) -> Optional[dict]:
    """Parse RRULE string into dictionary for icalendar.

    Args:
        rrule_string: RRULE string like "FREQ=WEEKLY;BYDAY=MO,WE".

    Returns:
        Dictionary suitable for icalendar rrule, or None if invalid.
    """
    if not rrule_string:
        return None

    result = {}
    parts = rrule_string.split(';')

    for part in parts:
        if '=' not in part:
            continue
        key, value = part.split('=', 1)

        if key == 'FREQ':
            result['freq'] = value.lower()
        elif key == 'INTERVAL':
            result['interval'] = int(value)
        elif key == 'BYDAY':
            result['byday'] = value.split(',')
        elif key == 'UNTIL':
            try:
                # Handle both DATE (YYYYMMDD) and DATE-TIME (YYYYMMDDTHHMMSS) formats
                # Use datetime with 23:59:59 UTC for Outlook compatibility
                date_part = value.split('T')[0] if 'T' in value else value
                until_datetime = datetime(
                    int(date_part[:4]),
                    int(date_part[4:6]),
                    int(date_part[6:8]),
                    23, 59, 59,
                    tzinfo=timezone.utc
                )
                result['until'] = until_datetime
            except (ValueError, IndexError):
                pass
        elif key == 'COUNT':
            result['count'] = int(value)

    return result if result else None
