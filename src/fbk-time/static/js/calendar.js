/**
 * Calendar helper functions used across the application.
 * @module calendar
 */

(function() {
    'use strict';

    /**
     * German month names.
     * @type {string[]}
     */
    var MONTH_NAMES = [
        'Januar', 'Februar', 'März', 'April', 'Mai', 'Juni',
        'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'
    ];

    /**
     * German weekday names (starting Monday).
     * @type {string[]}
     */
    var WEEKDAY_NAMES = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'];

    /**
     * German weekday full names (starting Monday).
     * @type {string[]}
     */
    var WEEKDAY_NAMES_FULL = [
        'Montag', 'Dienstag', 'Mittwoch', 'Donnerstag',
        'Freitag', 'Samstag', 'Sonntag'
    ];

    /**
     * Format a date object according to user preference.
     * @param {Date} date - Date object to format.
     * @returns {string} Formatted date based on user's date_format setting.
     */
    function formatDate(date) {
        var day = String(date.getDate()).padStart(2, '0');
        var month = String(date.getMonth() + 1).padStart(2, '0');
        var year = date.getFullYear();

        if (window.FBK && window.FBK.dateFormat === 'YYYY-MM-DD') {
            return year + '-' + month + '-' + day;
        }
        return day + '.' + month + '.' + year;
    }

    /**
     * Format a date object to ISO string.
     * @param {Date} date - Date object to format.
     * @returns {string} Formatted date (YYYY-MM-DD).
     */
    function formatDateISO(date) {
        var day = String(date.getDate()).padStart(2, '0');
        var month = String(date.getMonth() + 1).padStart(2, '0');
        var year = date.getFullYear();
        return year + '-' + month + '-' + day;
    }

    /**
     * Parse an ISO date string to Date object.
     * @param {string} dateStr - Date string (YYYY-MM-DD).
     * @returns {Date} Parsed date object.
     */
    function parseDate(dateStr) {
        var parts = dateStr.split('-');
        return new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
    }

    /**
     * Parse a German date string to Date object.
     * @param {string} dateStr - Date string (DD.MM.YYYY).
     * @returns {Date} Parsed date object.
     */
    function parseDateGerman(dateStr) {
        var parts = dateStr.split('.');
        return new Date(parseInt(parts[2]), parseInt(parts[1]) - 1, parseInt(parts[0]));
    }

    /**
     * Get month name in German.
     * @param {number} month - Month (1-12).
     * @returns {string} German month name.
     */
    function getMonthName(month) {
        return MONTH_NAMES[month - 1];
    }

    /**
     * Get weekday name in German.
     * @param {Date} date - Date object.
     * @param {boolean} full - Whether to return full name.
     * @returns {string} German weekday name.
     */
    function getWeekdayName(date, full) {
        var dayIndex = (date.getDay() + 6) % 7;
        return full ? WEEKDAY_NAMES_FULL[dayIndex] : WEEKDAY_NAMES[dayIndex];
    }

    /**
     * Check if a date is a weekend day.
     * @param {Date} date - Date object.
     * @returns {boolean} True if weekend.
     */
    function isWeekend(date) {
        var day = date.getDay();
        return day === 0 || day === 6;
    }

    /**
     * Get number of days in a month.
     * @param {number} year - Year.
     * @param {number} month - Month (1-12).
     * @returns {number} Number of days.
     */
    function getDaysInMonth(year, month) {
        return new Date(year, month, 0).getDate();
    }

    /**
     * Calculate working days between two dates (excluding weekends).
     * @param {Date} startDate - Start date.
     * @param {Date} endDate - End date.
     * @param {Object} holidays - Object with date keys (YYYY-MM-DD) to exclude.
     * @returns {number} Number of working days.
     */
    function calculateWorkingDays(startDate, endDate, holidays) {
        holidays = holidays || {};
        var count = 0;
        var current = new Date(startDate);

        while (current <= endDate) {
            if (!isWeekend(current)) {
                var dateKey = formatDateISO(current);
                if (!holidays[dateKey]) {
                    count++;
                }
            }
            current.setDate(current.getDate() + 1);
        }

        return count;
    }

    /**
     * Add days to a date.
     * @param {Date} date - Starting date.
     * @param {number} days - Number of days to add (can be negative).
     * @returns {Date} New date.
     */
    function addDays(date, days) {
        var result = new Date(date);
        result.setDate(result.getDate() + days);
        return result;
    }

    /**
     * Get start of week (Monday) for a given date.
     * @param {Date} date - Date object.
     * @returns {Date} Start of week.
     */
    function getWeekStart(date) {
        var d = new Date(date);
        var day = d.getDay();
        var diff = d.getDate() - day + (day === 0 ? -6 : 1);
        d.setDate(diff);
        return d;
    }

    /**
     * Get end of week (Sunday) for a given date.
     * @param {Date} date - Date object.
     * @returns {Date} End of week.
     */
    function getWeekEnd(date) {
        var start = getWeekStart(date);
        return addDays(start, 6);
    }

    /**
     * Check if two date ranges overlap.
     * @param {Date} start1 - Start of first range.
     * @param {Date} end1 - End of first range.
     * @param {Date} start2 - Start of second range.
     * @param {Date} end2 - End of second range.
     * @returns {boolean} True if ranges overlap.
     */
    function rangesOverlap(start1, end1, start2, end2) {
        return start1 <= end2 && end1 >= start2;
    }

    // Expose utilities to global scope
    window.FBKCalendar = {
        MONTH_NAMES: MONTH_NAMES,
        WEEKDAY_NAMES: WEEKDAY_NAMES,
        WEEKDAY_NAMES_FULL: WEEKDAY_NAMES_FULL,
        formatDate: formatDate,
        formatDateISO: formatDateISO,
        parseDate: parseDate,
        parseDateGerman: parseDateGerman,
        getMonthName: getMonthName,
        getWeekdayName: getWeekdayName,
        isWeekend: isWeekend,
        getDaysInMonth: getDaysInMonth,
        calculateWorkingDays: calculateWorkingDays,
        addDays: addDays,
        getWeekStart: getWeekStart,
        getWeekEnd: getWeekEnd,
        rangesOverlap: rangesOverlap
    };
})();
