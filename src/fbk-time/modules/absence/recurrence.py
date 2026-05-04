"""Recurrence service for absence management.

Provides RRULE pattern handling and occurrence generation for recurring absences.
Uses python-dateutil (dependency of icalendar) for RRULE parsing and expansion.
"""

from datetime import date, datetime, timedelta
from typing import Optional, Generator

from dateutil.rrule import rrulestr

from core.extensions import db
from modules.absence.models import Absence, RecurrenceException
from utils.helpers import format_date_for_user


class RecurrenceService:
    """Handle recurring absence patterns and occurrence expansion."""

    FREQUENCY_MAP = {
        'daily': 'DAILY',
        'weekly': 'WEEKLY',
        'biweekly': 'WEEKLY'  # Uses INTERVAL=2
    }

    WEEKDAY_CODES = ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU']

    @property
    def max_future_date(self):
        """Return the latest allowed date based on planning horizon."""
        from core.settings_manager import settings_manager
        from dateutil.relativedelta import relativedelta
        months = settings_manager.get('limits_max_future_months')
        return date.today() + relativedelta(months=months)

    def _get_exception(
        self, absence: Absence, occurrence_date: date
    ) -> Optional[RecurrenceException]:
        """Return the RecurrenceException for a given date, if any."""
        return RecurrenceException.query.filter_by(
            absence_id=absence.id,
            exception_date=occurrence_date
        ).first()

    def build_rrule_string(
        self,
        frequency: str,
        weekdays: Optional[list[str]] = None,
        end_date: Optional[date] = None,
        count: Optional[int] = None
    ) -> str:
        """Build an RRULE string from UI parameters.

        Args:
            frequency: 'daily', 'weekly', or 'biweekly'.
            weekdays: List of weekday codes ['MO', 'TU', ...] for weekly/biweekly.
            end_date: End date for the series (max 1 year from start).
            count: Number of occurrences (alternative to end_date).

        Returns:
            RRULE string, e.g., "FREQ=WEEKLY;BYDAY=MO,WE,FR;UNTIL=20261231T235959".

        Raises:
            ValueError: If frequency is not one of 'daily', 'weekly', 'biweekly'.
        """
        if frequency not in self.FREQUENCY_MAP:
            raise ValueError(f'Unsupported recurrence frequency: {frequency}')
        parts = [f"FREQ={self.FREQUENCY_MAP[frequency]}"]

        if frequency == 'biweekly':
            parts.append('INTERVAL=2')

        if weekdays and frequency in ('weekly', 'biweekly'):
            valid_days = [d for d in weekdays if d in self.WEEKDAY_CODES]
            if valid_days:
                parts.append(f"BYDAY={','.join(valid_days)}")

        if end_date:
            parts.append(f"UNTIL={end_date.strftime('%Y%m%d')}T235959")
        elif count:
            parts.append(f"COUNT={count}")

        return ';'.join(parts)

    def parse_rrule_string(self, rrule_string: str) -> dict:
        """Parse an RRULE string into component parts for UI display.

        Args:
            rrule_string: RRULE string to parse.

        Returns:
            Dictionary with frequency, weekdays, end_date, count.
        """
        result = {
            'frequency': 'weekly',
            'weekdays': [],
            'end_date': None,
            'count': None
        }

        if not rrule_string:
            return result

        parts = rrule_string.split(';')
        for part in parts:
            if '=' not in part:
                continue
            key, value = part.split('=', 1)

            if key == 'FREQ':
                if value == 'DAILY':
                    result['frequency'] = 'daily'
                elif value == 'WEEKLY':
                    result['frequency'] = 'weekly'

            elif key == 'INTERVAL':
                if value == '2' and result['frequency'] == 'weekly':
                    result['frequency'] = 'biweekly'

            elif key == 'BYDAY':
                result['weekdays'] = value.split(',')

            elif key == 'UNTIL':
                try:
                    # Handle both DATE (YYYYMMDD) and DATE-TIME (YYYYMMDDTHHMMSS) formats
                    date_part = value.split('T')[0] if 'T' in value else value
                    result['end_date'] = date(
                        int(date_part[:4]),
                        int(date_part[4:6]),
                        int(date_part[6:8])
                    )
                except (ValueError, IndexError):
                    pass

            elif key == 'COUNT':
                try:
                    result['count'] = int(value)
                except ValueError:
                    pass

        return result

    def expand_occurrences(
        self,
        absence: Absence,
        range_start: date,
        range_end: Optional[date] = None
    ) -> Generator[tuple[date, Optional[RecurrenceException]], None, None]:
        """Generate occurrence dates for a recurring absence within a date range.

        Args:
            absence: The master recurring absence record.
            range_start: Start of the date range to generate occurrences.
            range_end: End of the date range (defaults to recurrence_end_date or max).

        Yields:
            Tuple of (occurrence_date, exception_or_none).
            Deleted exceptions are skipped.
        """
        if range_end is None:
            range_end = absence.recurrence_end_date or (
                self.max_future_date
            )

        if not absence.is_recurring or not absence.rrule:
            if range_start <= absence.start_date <= range_end:
                yield (absence.start_date, None)
            return

        dtstart = absence.start_date.strftime('%Y%m%dT000000')
        rrule_full = f"DTSTART:{dtstart}\nRRULE:{absence.rrule}"

        try:
            rule = rrulestr(rrule_full)
        except (ValueError, TypeError):
            if range_start <= absence.start_date <= range_end:
                yield (absence.start_date, None)
            return

        exceptions_by_date = {}
        for exc in absence.exceptions.all():
            exceptions_by_date[exc.exception_date] = exc

        effective_end = min(
            range_end,
            self.max_future_date
        )
        if absence.recurrence_end_date:
            effective_end = min(effective_end, absence.recurrence_end_date)

        dt_start = datetime.combine(range_start, datetime.min.time())
        dt_end = datetime.combine(effective_end, datetime.max.time())

        for dt in rule.between(dt_start, dt_end, inc=True):
            occurrence_date = dt.date()

            exception = exceptions_by_date.get(occurrence_date)

            if exception and exception.exception_type == 'deleted':
                continue

            yield (occurrence_date, exception)

    def get_occurrence_data(
        self,
        absence: Absence,
        occurrence_date: date
    ) -> Optional[dict]:
        """Get the effective data for a specific occurrence.

        Merges master absence data with any exception overrides.

        Args:
            absence: The master recurring absence.
            occurrence_date: The specific date to get data for.

        Returns:
            Dictionary with merged absence data, or None if occurrence is deleted.
        """
        exception = self._get_exception(absence, occurrence_date)

        if exception and exception.exception_type == 'deleted':
            return None

        data = {
            'absence_id': absence.id,
            'user_id': absence.user_id,
            'user': absence.user,
            'category_id': absence.category_id,
            'category': absence.category,
            'date': occurrence_date,
            'start_time': absence.start_time,
            'end_time': absence.end_time,
            'is_all_day': absence.is_all_day,
            'is_half_day_morning': absence.is_half_day_morning,
            'is_half_day_afternoon': absence.is_half_day_afternoon,
            'substitute_id': absence.substitute_id,
            'substitute': absence.substitute,
            'notes': absence.notes,
            'is_recurring': True,
            'is_exception': False,
            'exception': None
        }

        if exception and exception.exception_type == 'modified':
            data['is_exception'] = True
            data['exception'] = exception

            if exception.modified_category_overridden:
                data['category_id'] = exception.modified_category_id
                data['category'] = exception.modified_category

            if exception.modified_time_type is not None:
                time_type = exception.modified_time_type
                data['is_all_day'] = time_type == 'all_day'
                data['is_half_day_morning'] = time_type == 'morning'
                data['is_half_day_afternoon'] = time_type == 'afternoon'

            if exception.modified_substitute_overridden:
                data['substitute_id'] = exception.modified_substitute_id
                data['substitute'] = exception.modified_substitute

            if exception.modified_notes_overridden:
                data['notes'] = exception.modified_notes

        return data

    def is_valid_occurrence_date(self, absence: Absence, occurrence_date: date) -> bool:
        """Check if a date is a valid occurrence in the recurring series.

        Args:
            absence: The master recurring absence.
            occurrence_date: The date to validate.

        Returns:
            True if the date is a valid occurrence, False otherwise.
        """
        if not absence.is_recurring or not absence.rrule:
            return occurrence_date == absence.start_date

        for occ_date, _ in self.expand_occurrences(absence, occurrence_date, occurrence_date):
            if occ_date == occurrence_date:
                return True

        return False

    def is_date_in_rrule(self, absence: Absence, check_date: date) -> bool:
        """Check if a date is generated by the raw RRULE pattern.

        Unlike :meth:`is_valid_occurrence_date`, this method ignores
        exceptions entirely. A date with a 'deleted' exception still
        returns True if the underlying RRULE would generate it.

        Used by the orphan exception pruner: an exception is only
        orphaned when its date is no longer produced by the new
        recurrence pattern, regardless of the exception type.

        Args:
            absence: The master recurring absence.
            check_date: The date to test against the raw RRULE.

        Returns:
            True if the date is produced by the RRULE, False otherwise.
        """
        if not absence.is_recurring or not absence.rrule:
            return check_date == absence.start_date

        dtstart = absence.start_date.strftime('%Y%m%dT000000')
        rrule_full = f"DTSTART:{dtstart}\nRRULE:{absence.rrule}"

        try:
            rule = rrulestr(rrule_full)
        except (ValueError, TypeError):
            return False

        dt_start = datetime.combine(check_date, datetime.min.time())
        dt_end = datetime.combine(check_date, datetime.max.time())
        for dt in rule.between(dt_start, dt_end, inc=True):
            if dt.date() == check_date:
                return True
        return False

    def delete_occurrence(self, absence: Absence, occurrence_date: date) -> RecurrenceException:
        """Delete a single occurrence from a recurring series.

        Creates a 'deleted' exception for the specified date.

        Args:
            absence: The master recurring absence.
            occurrence_date: The specific date to delete.

        Returns:
            The created RecurrenceException.

        Raises:
            ValueError: If occurrence_date is not a valid date in the series.
        """
        exception = self._get_exception(absence, occurrence_date)

        if not exception and not self.is_valid_occurrence_date(absence, occurrence_date):
            raise ValueError(
                f'{format_date_for_user(occurrence_date)} ist kein gültiger '
                f'Termin dieser Serie'
            )

        if exception:
            exception.exception_type = 'deleted'
            exception.modified_category_id = None
            exception.modified_category_overridden = False
            exception.modified_time_type = None
            exception.modified_substitute_id = None
            exception.modified_substitute_overridden = False
            exception.modified_notes = None
            exception.modified_notes_overridden = False
        else:
            exception = RecurrenceException(
                absence_id=absence.id,
                exception_date=occurrence_date,
                exception_type='deleted'
            )
            db.session.add(exception)

        return exception

    def modify_occurrence(
        self,
        absence: Absence,
        occurrence_date: date,
        effective_state: dict
    ) -> Optional[RecurrenceException]:
        """Apply an effective state to a single occurrence of a series.

        The effective_state dictionary describes the complete desired
        state for the occurrence with four mandatory keys:

            category_id (int):        The desired category.
            time_type (str):          'all_day', 'morning', or 'afternoon'.
            substitute_id (int|None): The desired substitute, or None.
            notes (str|None):         The desired notes, or None.

        Each field is compared against the parent absence. Fields that
        match the parent are not stored as overrides. Fields that
        differ become active overrides with their override flags set.

        If every field matches the parent after this comparison, any
        existing modification exception for the date is removed so
        that the occurrence inherits the parent cleanly.

        Args:
            absence: The master recurring absence.
            occurrence_date: The specific date to modify.
            effective_state: Complete desired state dict.

        Returns:
            The RecurrenceException in use, or None if no override was
            needed and any existing exception has been removed.

        Raises:
            ValueError: If occurrence_date is invalid, was previously
                deleted, or effective_state is missing required keys.
        """
        required_keys = {'category_id', 'time_type', 'substitute_id', 'notes'}
        missing = required_keys - set(effective_state.keys())
        if missing:
            raise ValueError(
                f'effective_state missing required keys: {sorted(missing)}'
            )

        exception = self._get_exception(absence, occurrence_date)

        if exception and exception.exception_type == 'deleted':
            raise ValueError(
                f'Termin am {format_date_for_user(occurrence_date)} wurde gelöscht '
                f'und kann nicht modifiziert werden'
            )

        if not exception and not self.is_valid_occurrence_date(absence, occurrence_date):
            raise ValueError(
                f'{format_date_for_user(occurrence_date)} ist kein gültiger '
                f'Termin dieser Serie'
            )

        parent_time_type = self.parent_time_type(absence)

        needs_category = effective_state['category_id'] != absence.category_id
        needs_time = effective_state['time_type'] != parent_time_type
        needs_substitute = effective_state['substitute_id'] != absence.substitute_id
        needs_notes = effective_state['notes'] != absence.notes

        any_override = (
            needs_category or needs_time or needs_substitute or needs_notes
        )

        if not any_override:
            if exception:
                db.session.delete(exception)
            return None

        if not exception:
            exception = RecurrenceException(
                absence_id=absence.id,
                exception_date=occurrence_date,
                exception_type='modified'
            )
            db.session.add(exception)

        exception.modified_category_id = (
            effective_state['category_id'] if needs_category else None
        )
        exception.modified_category_overridden = needs_category
        exception.modified_time_type = (
            effective_state['time_type'] if needs_time else None
        )
        exception.modified_substitute_id = (
            effective_state['substitute_id'] if needs_substitute else None
        )
        exception.modified_substitute_overridden = needs_substitute
        exception.modified_notes = (
            effective_state['notes'] if needs_notes else None
        )
        exception.modified_notes_overridden = needs_notes

        return exception

    @staticmethod
    def parent_time_type(absence: Absence) -> str:
        """Return the time_type enum value of the parent absence."""
        if absence.is_half_day_morning:
            return 'morning'
        if absence.is_half_day_afternoon:
            return 'afternoon'
        return 'all_day'

    def validate_recurrence_end_date(
        self,
        start_date: date,
        end_date: Optional[date]
    ) -> date:
        """Validate and constrain recurrence end date to the configured planning horizon.

        Args:
            start_date: Start date of the recurring absence.
            end_date: Requested end date (may be None or beyond limit).

        Returns:
            Valid end date within the planning horizon.
        """
        max_end = self.max_future_date

        if end_date is None:
            return max_end

        return min(end_date, max_end)

    def count_occurrences(
        self,
        absence: Absence,
        range_start: Optional[date] = None,
        range_end: Optional[date] = None
    ) -> int:
        """Count total occurrences in a date range.

        Args:
            absence: The recurring absence to count.
            range_start: Start of range (default: absence start_date).
            range_end: End of range (default: recurrence_end_date or max).

        Returns:
            Number of occurrences (excluding deleted exceptions).
        """
        if range_start is None:
            range_start = absence.start_date

        if range_end is None:
            range_end = absence.recurrence_end_date or (
                self.max_future_date
            )

        count = 0
        for _ in self.expand_occurrences(absence, range_start, range_end):
            count += 1

        return count

    def get_recurrence_description(
        self,
        rrule_string: str,
        end_date: Optional[date] = None
    ) -> str:
        """Generate human-readable description of recurrence pattern.

        Args:
            rrule_string: The RRULE string to describe.
            end_date: Optional end date to include in description.

        Returns:
            German description like "Jeden Montag und Freitag".
        """
        parsed = self.parse_rrule_string(rrule_string)

        weekday_names = {
            'MO': 'Montag', 'TU': 'Dienstag', 'WE': 'Mittwoch',
            'TH': 'Donnerstag', 'FR': 'Freitag', 'SA': 'Samstag', 'SU': 'Sonntag'
        }

        if parsed['frequency'] == 'daily':
            desc = 'Täglich'
        elif parsed['frequency'] == 'biweekly':
            if parsed['weekdays']:
                days = [weekday_names.get(d, d) for d in parsed['weekdays']]
                desc = f"Alle 2 Wochen am {' und '.join(days)}"
            else:
                desc = 'Alle 2 Wochen'
        else:
            if parsed['weekdays']:
                days = [weekday_names.get(d, d) for d in parsed['weekdays']]
                if len(days) == 1:
                    desc = f"Jeden {days[0]}"
                else:
                    desc = f"Jeden {', '.join(days[:-1])} und {days[-1]}"
            else:
                desc = 'Wöchentlich'

        effective_end = end_date or parsed.get('end_date')
        if effective_end:
            desc += f" bis {format_date_for_user(effective_end)}"
        elif parsed.get('count'):
            desc += f" ({parsed['count']} Termine)"

        return desc

    def get_all_occurrences_for_range(
        self,
        absences: list,
        range_start: date,
        range_end: date
    ) -> list[dict]:
        """Expand all absences (recurring and non-recurring) for a date range.

        Args:
            absences: List of Absence records to expand.
            range_start: Start of the date range.
            range_end: End of the date range.

        Returns:
            List of occurrence dictionaries with date and absence data.
        """
        occurrences = []

        for absence in absences:
            if absence.is_recurring and absence.rrule:
                for occ_date, exception in self.expand_occurrences(absence, range_start, range_end):
                    occ_data = self.get_occurrence_data(absence, occ_date)
                    if occ_data:
                        occurrences.append({
                            'date': occ_date,
                            'absence': absence,
                            'user_id': absence.user_id,
                            'user': absence.user,
                            'category_id': occ_data['category_id'],
                            'category': occ_data['category'],
                            'is_all_day': occ_data['is_all_day'],
                            'is_half_day_morning': occ_data['is_half_day_morning'],
                            'is_half_day_afternoon': occ_data['is_half_day_afternoon'],
                            'start_time': occ_data['start_time'],
                            'end_time': occ_data['end_time'],
                            'substitute_id': occ_data['substitute_id'],
                            'substitute': occ_data['substitute'],
                            'notes': occ_data['notes'],
                            'is_recurring': True,
                            'is_exception': occ_data['is_exception']
                        })
            else:
                absence_start = max(range_start, absence.start_date)
                absence_end = min(range_end, absence.end_date)

                current = absence_start
                while current <= absence_end:
                    occurrences.append({
                        'date': current,
                        'absence': absence,
                        'user_id': absence.user_id,
                        'user': absence.user,
                        'category_id': absence.category_id,
                        'category': absence.category,
                        'is_all_day': absence.is_all_day,
                        'is_half_day_morning': absence.is_half_day_morning,
                        'is_half_day_afternoon': absence.is_half_day_afternoon,
                        'start_time': absence.start_time,
                        'end_time': absence.end_time,
                        'substitute_id': absence.substitute_id,
                        'substitute': absence.substitute,
                        'notes': absence.notes,
                        'is_recurring': False,
                        'is_exception': False
                    })
                    current += timedelta(days=1)

        return occurrences


# Module-level instance for convenience
recurrence_service = RecurrenceService()
