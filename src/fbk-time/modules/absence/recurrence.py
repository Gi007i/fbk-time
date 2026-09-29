"""RRULE handling and occurrence expansion for recurring absences.

Uses python-dateutil, already a dependency of icalendar.
"""

from datetime import date, datetime, timedelta
from typing import Generator, Optional, Union

from dateutil.relativedelta import relativedelta
from dateutil.rrule import rrule, rrulestr
from flask import current_app
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from core.db import db
from core.settings_manager import settings_manager
from modules.absence.models import Absence, RecurrenceException
from utils.helpers import format_date_for_user


# Distinguishes "caller supplied no exception" from a resolved None.
_UNRESOLVED = object()

# Keeps IN(...) lists well below the SQLite host-parameter limit.
_ID_CHUNK_SIZE = 500

_PERIOD_DAYS = {'daily': 1, 'weekly': 7, 'biweekly': 14}

RRULE_UNREADABLE = 'Das Serienmuster dieser Abwesenheit ist nicht lesbar.'

_WEEKDAY_NAMES = {
    'MO': 'Montag', 'TU': 'Dienstag', 'WE': 'Mittwoch',
    'TH': 'Donnerstag', 'FR': 'Freitag', 'SA': 'Samstag', 'SU': 'Sonntag'
}


class RecurrenceService:
    """Handle recurring absence patterns and occurrence expansion.

    Every stored RRULE comes from ``build_rrule_string``, so an unparsable rule
    means corrupted data. Write paths then raise ``ValueError`` with the
    user-facing ``RRULE_UNREADABLE`` and never delete exceptions on its basis;
    read paths log the error and render only the series start, so one broken
    record does not take down every overview.
    """

    FREQUENCY_MAP = {
        'daily': 'DAILY',
        'weekly': 'WEEKLY',
        'biweekly': 'WEEKLY'  # Uses INTERVAL=2
    }

    WEEKDAY_CODES = ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU']

    @property
    def max_future_date(self) -> date:
        """Return the latest allowed date based on planning horizon."""
        months = settings_manager.get('limits_max_future_months')
        return date.today() + relativedelta(months=months)

    def _get_exception(
        self, absence: Absence, occurrence_date: date
    ) -> Optional[RecurrenceException]:
        """Return the RecurrenceException for a given date, if any."""
        return db.session.scalars(
            select(RecurrenceException).filter_by(
                absence_id=absence.id,
                exception_date=occurrence_date
            )
        ).first()

    def build_rule(
        self,
        start_date: date,
        rrule_string: str,
        range_start: Optional[date] = None
    ) -> rrule:
        """Parse an RRULE for expansion from range_start onwards.

        The anchor moves forward by whole periods (1, 7 or 14 days) to the
        last one not after range_start, so expansion cost does not grow with
        the series age; the rules built by ``build_rrule_string`` repeat
        with that period, so every occurrence from range_start on is unchanged.

        Args:
            start_date: First day of the series.
            rrule_string: RRULE as produced by ``build_rrule_string``.
            range_start: Earliest date the caller expands; None keeps the
                series start as anchor.

        Raises:
            ValueError: If the rule cannot be parsed or is not in the form
                ``build_rrule_string`` produces.
        """
        parsed = self.validate_rrule(rrule_string)

        anchor = start_date
        if range_start is not None and range_start > start_date:
            period = _PERIOD_DAYS[parsed['frequency']]
            anchor += timedelta(
                days=(range_start - start_date).days // period * period
            )

        dtstart = anchor.strftime('%Y%m%dT000000')
        try:
            return rrulestr(f"DTSTART:{dtstart}\nRRULE:{rrule_string}")
        except (ValueError, TypeError) as error:
            current_app.logger.error('Unparsable RRULE %r: %s', rrule_string, error)
            raise ValueError(RRULE_UNREADABLE) from error

    def validate_rrule(self, rrule_string: str) -> dict:
        """Parse a stored RRULE and require the form build_rrule_string writes.

        Anything else (other frequencies, COUNT, unknown weekdays) can only be
        corrupted data and is rejected like an unparsable rule.

        Args:
            rrule_string: The stored RRULE string.

        Returns:
            Parsed components as returned by ``parse_rrule_string``.

        Raises:
            ValueError: RRULE_UNREADABLE if the rule is not canonical.
        """
        parsed = self.parse_rrule_string(rrule_string)
        canonical = self.build_rrule_string(
            parsed['frequency'], parsed['weekdays'], parsed['end_date']
        )
        if canonical != rrule_string:
            current_app.logger.error('Non-canonical RRULE %r', rrule_string)
            raise ValueError(RRULE_UNREADABLE)
        return parsed

    def build_rrule_string(
        self,
        frequency: str,
        weekdays: Optional[list[str]] = None,
        end_date: Optional[date] = None
    ) -> str:
        """Build an RRULE string from UI parameters.

        Args:
            frequency: 'daily', 'weekly', or 'biweekly'.
            weekdays: List of weekday codes ['MO', 'TU', ...] for weekly/biweekly.
            end_date: End date for the series.

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

        return ';'.join(parts)

    def parse_rrule_string(self, rrule_string: str) -> dict:
        """Parse an RRULE string into component parts for UI display.

        Args:
            rrule_string: RRULE string to parse.

        Returns:
            Dictionary with frequency, weekdays, end_date.

        Raises:
            ValueError: RRULE_UNREADABLE if the UNTIL value is not a valid date.
        """
        result = {
            'frequency': 'weekly',
            'weekdays': [],
            'end_date': None
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
                # DATE (YYYYMMDD) or DATE-TIME (YYYYMMDDTHHMMSS)
                try:
                    result['end_date'] = datetime.strptime(
                        value.split('T')[0], '%Y%m%d'
                    ).date()
                except ValueError as error:
                    current_app.logger.error(
                        'Unparsable RRULE %r: %s', rrule_string, error
                    )
                    raise ValueError(RRULE_UNREADABLE) from error

        return result

    def load_exceptions(
        self,
        absences: list,
        range_start: date,
        range_end: date
    ) -> dict[int, dict[date, RecurrenceException]]:
        """Load the exceptions of all recurring absences for a range at once.

        Args:
            absences: Absence records; non-recurring ones are ignored.
            range_start: First date of the range.
            range_end: Last date of the range.

        Returns:
            Dict mapping each recurring absence ID to a dict of
            exception date to RecurrenceException.
        """
        absence_ids = [a.id for a in absences if a.is_recurring and a.rrule]
        result = {absence_id: {} for absence_id in absence_ids}

        for offset in range(0, len(absence_ids), _ID_CHUNK_SIZE):
            chunk = absence_ids[offset:offset + _ID_CHUNK_SIZE]
            exceptions = db.session.scalars(
                select(RecurrenceException).where(
                    RecurrenceException.absence_id.in_(chunk),
                    RecurrenceException.exception_date >= range_start,
                    RecurrenceException.exception_date <= range_end
                ).options(
                    selectinload(RecurrenceException.modified_category),
                    selectinload(RecurrenceException.modified_substitute)
                )
            )
            for exc in exceptions:
                result[exc.absence_id][exc.exception_date] = exc

        return result

    def expand_occurrences(
        self,
        absence: Absence,
        range_start: date,
        range_end: Optional[date] = None,
        exceptions_by_date: Optional[dict[date, RecurrenceException]] = None
    ) -> Generator[tuple[date, Optional[RecurrenceException]], None, None]:
        """Generate occurrence dates for a recurring absence within a date range.

        This is a read path: an unparsable RRULE is logged and only the series
        start is yielded (see the class docstring).

        Args:
            absence: The master recurring absence record.
            range_start: Start of the date range to generate occurrences.
            range_end: End of the date range (defaults to recurrence_end_date or max).
            exceptions_by_date: Exceptions of this absence covering the range,
                as returned by ``load_exceptions``. Loaded here when omitted.

        Yields:
            Tuple of (occurrence_date, exception_or_none).
            Deleted exceptions are skipped.
        """
        if range_end is None:
            range_end = absence.recurrence_end_date or self.max_future_date

        def exception_for(occurrence_date):
            if exceptions_by_date is not None:
                return exceptions_by_date.get(occurrence_date)
            return self._get_exception(absence, occurrence_date)

        # The series-start fallback must resolve the exception too: callers
        # treat the yielded value as authoritative, so a placeholder None
        # would hide a deletion or an override for that date.
        rule = None
        if absence.is_recurring and absence.rrule:
            try:
                rule = self.build_rule(
                    absence.start_date, absence.rrule, range_start
                )
            except ValueError:
                current_app.logger.error(
                    'Absence %s: unreadable RRULE, showing series start only',
                    absence.id
                )

        if rule is None:
            if range_start <= absence.start_date <= range_end:
                exception = exception_for(absence.start_date)
                if not (exception and exception.exception_type == 'deleted'):
                    yield (absence.start_date, exception)
            return

        if exceptions_by_date is None:
            exceptions_by_date = {
                exc.exception_date: exc
                for exc in db.session.scalars(absence.exceptions.select())
            }

        effective_end = min(range_end, self.max_future_date)
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
        occurrence_date: date,
        exception: Union[RecurrenceException, None, object] = _UNRESOLVED
    ) -> Optional[dict]:
        """Get the effective data for a specific occurrence.

        Merges master absence data with any exception overrides.

        Args:
            absence: The master recurring absence.
            occurrence_date: The specific date to get data for.
            exception: The already-resolved exception for this date, as
                yielded by ``expand_occurrences``; avoids one query per
                occurrence when expanding a whole range. Loaded here when
                omitted.

        Returns:
            Dictionary with merged absence data, or None if occurrence is deleted.
        """
        if exception is _UNRESOLVED:
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

            # An override replaces the series time window entirely.
            if exception.modified_time_type is not None:
                time_type = exception.modified_time_type
                data['is_all_day'] = time_type == 'all_day'
                data['is_half_day_morning'] = time_type == 'morning'
                data['is_half_day_afternoon'] = time_type == 'afternoon'
                data['start_time'] = None
                data['end_time'] = None

            if exception.modified_substitute_overridden:
                data['substitute_id'] = exception.modified_substitute_id
                data['substitute'] = exception.modified_substitute

            if exception.modified_notes_overridden:
                data['notes'] = exception.modified_notes

        return data

    def is_valid_occurrence_date(self, absence: Absence, occurrence_date: date) -> bool:
        """Check if a date is a valid, not deleted occurrence of the series.

        Args:
            absence: The master recurring absence.
            occurrence_date: The date to validate.

        Returns:
            True if the date is a valid occurrence, False otherwise.

        Raises:
            ValueError: If the stored RRULE cannot be parsed.
        """
        if not absence.is_recurring or not absence.rrule:
            return occurrence_date == absence.start_date

        effective_end = self.max_future_date
        if absence.recurrence_end_date:
            effective_end = min(effective_end, absence.recurrence_end_date)
        if not absence.start_date <= occurrence_date <= effective_end:
            return False

        if not self.is_date_in_rrule(absence, occurrence_date):
            return False

        exception = self._get_exception(absence, occurrence_date)
        return not (exception and exception.exception_type == 'deleted')

    def is_date_in_rrule(self, absence: Absence, check_date: date) -> bool:
        """Check if a date is generated by the raw RRULE pattern.

        Unlike :meth:`is_valid_occurrence_date`, exceptions are ignored, so a
        deleted occurrence still counts. The orphan pruner relies on this: an
        exception is orphaned only when the pattern no longer produces its date.

        Args:
            absence: The master recurring absence.
            check_date: The date to test against the raw RRULE.

        Returns:
            True if the date is produced by the RRULE, False otherwise.

        Raises:
            ValueError: If the stored RRULE cannot be parsed.
        """
        if not absence.is_recurring or not absence.rrule:
            return check_date == absence.start_date

        rule = self.build_rule(absence.start_date, absence.rrule, check_date)

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
            time_type (str):          'all_day', 'morning', 'afternoon', or
                                      'custom_time' (keeps the series window).
            substitute_id (int|None): The desired substitute, or None.
            notes (str|None):         The desired notes, or None.

        Fields matching the parent are not stored as overrides. If every
        field matches, an existing modification exception for the date is
        removed so the occurrence inherits the parent cleanly.

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
        """Return the time_type enum value of the parent absence.

        Mirrors the slot classification: half-day flags win, a custom
        window needs both times.
        """
        if absence.is_half_day_morning:
            return 'morning'
        if absence.is_half_day_afternoon:
            return 'afternoon'
        if not absence.is_all_day and absence.start_time and absence.end_time:
            return 'custom_time'
        return 'all_day'

    def validate_recurrence_end_date(self, end_date: Optional[date]) -> date:
        """Validate and constrain recurrence end date to the configured planning horizon.

        Args:
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

    def describe_pattern(self, rrule_string: str) -> str:
        """Describe the repetition of an RRULE without its end.

        Args:
            rrule_string: The RRULE string to describe.

        Returns:
            German description like "Jeden Montag und Freitag".

        Raises:
            ValueError: RRULE_UNREADABLE if the RRULE is not valid.
        """
        parsed = self.validate_rrule(rrule_string)

        if parsed['frequency'] == 'daily':
            return 'Täglich'

        days = [_WEEKDAY_NAMES.get(d, d) for d in parsed['weekdays']]
        if parsed['frequency'] == 'biweekly':
            if days:
                return f"Alle 2 Wochen am {' und '.join(days)}"
            return 'Alle 2 Wochen'

        if not days:
            return 'Wöchentlich'
        if len(days) == 1:
            return f"Jeden {days[0]}"
        return f"Jeden {', '.join(days[:-1])} und {days[-1]}"

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
            German description like "Jeden Montag und Freitag bis 31.12.2026".

        Raises:
            ValueError: RRULE_UNREADABLE if the RRULE is not valid.
        """
        desc = self.describe_pattern(rrule_string)

        effective_end = end_date or self.validate_rrule(rrule_string)['end_date']
        if effective_end:
            desc += f" bis {format_date_for_user(effective_end)}"

        return desc

    def describe_series(self, absence: Absence) -> str:
        """Describe a stored series for display.

        This is a read path: an unparsable RRULE is logged and described as
        unreadable instead of failing the page (see the class docstring).

        Args:
            absence: The recurring absence.

        Returns:
            German description of the pattern and its end.
        """
        try:
            return self.get_recurrence_description(
                absence.rrule, absence.recurrence_end_date
            )
        except ValueError:
            current_app.logger.error(
                'Absence %s: unreadable RRULE, pattern not described', absence.id
            )
            return 'Serienmuster nicht lesbar'

    def describe_pattern_safe(self, rrule_string: str) -> str:
        """Describe a pattern, reporting an unreadable one instead of raising.

        Used for the old value in the history, so saving a repaired series is
        not blocked by the corrupted rule it replaces.
        """
        try:
            return self.describe_pattern(rrule_string)
        except ValueError:
            return 'nicht lesbar'

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
        exceptions = self.load_exceptions(absences, range_start, range_end)

        for absence in absences:
            if absence.is_recurring and absence.rrule:
                for occ_date, exception in self.expand_occurrences(
                    absence, range_start, range_end, exceptions[absence.id]
                ):
                    occ_data = self.get_occurrence_data(absence, occ_date, exception)
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


recurrence_service = RecurrenceService()
