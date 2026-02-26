/**
 * Renders month/week calendar views with absence data.
 * @module calendar
 */
(function() {
    'use strict';

    var MOBILE_BREAKPOINT = 768;
    var MONTH_NAMES = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'];

    var calendarData = null;
    var currentWeekStart = null;
    var isWeekView = false;

    /**
     * Check if viewport is mobile width.
     * @returns {boolean} True if mobile viewport.
     */
    function isMobile() {
        return window.innerWidth < MOBILE_BREAKPOINT;
    }

    /**
     * Get Monday of the week containing the given date.
     * @param {Date} date - Any date in the week.
     * @returns {Date} Monday of that week.
     */
    function getWeekStart(date) {
        var d = new Date(date);
        var day = d.getDay();
        var diff = d.getDate() - day + (day === 0 ? -6 : 1);
        d.setDate(diff);
        d.setHours(0, 0, 0, 0);
        return d;
    }

    /**
     * Format date as ISO string (YYYY-MM-DD).
     * @param {Date} date - Date to format.
     * @returns {string} Formatted date string.
     */
    function formatDateStr(date) {
        return date.getFullYear() + '-' +
            String(date.getMonth() + 1).padStart(2, '0') + '-' +
            String(date.getDate()).padStart(2, '0');
    }

    /**
     * Format date as short format according to user preference.
     * @param {Date} date - Date to format.
     * @returns {string} Formatted date string (DD.MM. or MM-DD).
     */
    function formatDateShort(date) {
        var day = String(date.getDate()).padStart(2, '0');
        var month = String(date.getMonth() + 1).padStart(2, '0');

        if (document.documentElement.dataset.dateFormat === 'YYYY-MM-DD') {
            return month + '-' + day;
        }
        return day + '.' + month + '.';
    }

    /**
     * Update hidden week_start input for filter form.
     * @returns {void}
     */
    function updateWeekStartInput() {
        var input = document.getElementById('week_start_input');
        if (input) {
            input.value = formatDateStr(currentWeekStart);
        }
    }

    /**
     * Update navigation visibility based on current view.
     * @returns {void}
     */
    function updateNavVisibility() {
        var monthNav = document.querySelector('.page-nav-month');
        var weekNav = document.querySelector('.page-nav-week');

        if (isWeekView) {
            monthNav.classList.add('hidden');
            weekNav.classList.remove('hidden');
            updateWeekTitle();
        } else {
            monthNav.classList.remove('hidden');
            weekNav.classList.add('hidden');
        }
    }

    /**
     * Update week title in navigation.
     * @returns {void}
     */
    function updateWeekTitle() {
        var weekEnd = new Date(currentWeekStart);
        weekEnd.setDate(weekEnd.getDate() + 4);

        var title = formatDateShort(currentWeekStart) + ' - ' + formatDateShort(weekEnd);
        if (currentWeekStart.getMonth() !== weekEnd.getMonth()) {
            title += ' ' + weekEnd.getFullYear();
        } else {
            title += ' ' + MONTH_NAMES[currentWeekStart.getMonth()];
        }

        var titleEl = document.getElementById('calendar-title-week');
        if (titleEl) {
            titleEl.textContent = title;
        }
    }

    /**
     * Get occurrences for a specific date.
     * @param {string} dateStr - Date in ISO format.
     * @returns {Array} Array of occurrence objects.
     */
    function getOccurrencesForDate(dateStr) {
        return calendarData.occurrences.filter(function(occ) {
            return occ.date === dateStr;
        });
    }

    /**
     * Render a single calendar cell.
     * @param {Date} date - Date for this cell.
     * @param {number} col - Column index (0-6, Mon-Sun).
     * @returns {string} HTML string for the cell.
     */
    function renderCell(date, col) {
        var dateStr = formatDateStr(date);
        var isWeekend = col >= 5;
        var isToday = dateStr === calendarData.today;
        var isCurrentMonth = date.getMonth() + 1 === calendarData.month;

        var classes = [];
        if (!isCurrentMonth && !isWeekView) classes.push('day-other-month');
        if (isWeekend) classes.push('day-weekend');
        if (isToday) classes.push('day-today');

        var html = '<td class="' + classes.join(' ') + '" data-date="' + dateStr + '">';
        html += '<span class="day-number">' + date.getDate() + '</span>';

        if (calendarData.holidays[dateStr]) {
            var holidayName = window.FBKTime.escapeHtml(calendarData.holidays[dateStr]);
            var holidayNameAttr = window.FBKTime.escapeAttr(calendarData.holidays[dateStr]);
            html += '<span data-tooltip="' + holidayNameAttr + '"><mark class="absence-item"><small>' + holidayName + '</small></mark></span>';
        }

        var dayOccurrences = getOccurrencesForDate(dateStr);
        var maxShow = isWeekView ? 5 : 3;
        for (var i = 0; i < Math.min(dayOccurrences.length, maxShow); i++) {
            var occ = dayOccurrences[i];
            var safeAbsenceId = parseInt(occ.absenceId, 10);
            var safeCategoryId = parseInt(occ.categoryId, 10);
            if (isNaN(safeAbsenceId) || isNaN(safeCategoryId)) continue;
            var label = '';

            var safeUserName = window.FBKTime.escapeHtml(occ.userName);
            var safeCategoryIcon = window.FBKTime.escapeHtml(occ.categoryIcon);
            var safeCategoryName = window.FBKTime.escapeHtml(occ.categoryName);

            if (isWeekView) {
                if (occ.categoryIcon) label += '<span class="category-icon">' + safeCategoryIcon + '</span>';
                label += '<span class="absence-name">' + safeUserName + '</span>';
                var meta = '';
                if (occ.isHalfDayMorning) meta += '(VM) ';
                else if (occ.isHalfDayAfternoon) meta += '(NM) ';
                if (occ.isRecurring) meta += '🔁';
                if (meta) label += '<span class="absence-meta">' + meta + '</span>';
            } else {
                if (occ.categoryIcon) label += '<span class="category-icon">' + safeCategoryIcon + '</span> ';
                label += safeUserName;
                if (occ.isHalfDayMorning) label += ' (VM)';
                else if (occ.isHalfDayAfternoon) label += ' (NM)';
                if (occ.isRecurring) label += ' 🔁';
            }

            var tooltip = safeCategoryName + ': ' + safeUserName;
            if (occ.isHalfDayMorning) tooltip += ' (Vormittag)';
            else if (occ.isHalfDayAfternoon) tooltip += ' (Nachmittag)';
            tooltip += occ.isPresent ? ' - Anwesend' : ' - Abwesend';
            if (occ.isRecurring) tooltip += ' (Serie)';

            var detailUrl = occ.isRecurring
                ? '/absences/' + safeAbsenceId + '/occurrence/' + occ.date
                : '/absences/' + safeAbsenceId;

            var tooltipAttr = window.FBKTime.escapeAttr(occ.categoryName + ': ' + occ.userName + (occ.isHalfDayMorning ? ' (Vormittag)' : occ.isHalfDayAfternoon ? ' (Nachmittag)' : '') + (occ.isPresent ? ' - Anwesend' : ' - Abwesend') + (occ.isRecurring ? ' (Serie)' : ''));
            html += '<span data-tooltip="' + tooltipAttr + '"><a href="' + detailUrl + '" class="absence-item category-' + safeCategoryId + '">' + label + '</a></span>';
        }
        if (dayOccurrences.length > maxShow) {
            html += '<span class="absence-more">+' + (dayOccurrences.length - maxShow) + ' weitere</span>';
        }

        html += '</td>';
        return html;
    }

    /**
     * Render month view calendar.
     * @returns {void}
     */
    function renderMonthView() {
        var year = calendarData.year;
        var month = calendarData.month;

        var firstDay = new Date(year, month - 1, 1);
        var lastDay = new Date(year, month, 0);
        var daysInMonth = lastDay.getDate();
        var startDayOfWeek = (firstDay.getDay() + 6) % 7;

        var prevMonth = new Date(year, month - 1, 0);
        var daysFromPrevMonth = prevMonth.getDate();

        var html = '';
        var dayCount = 1;
        var nextMonthDay = 1;
        var totalCells = startDayOfWeek + daysInMonth;
        var rows = Math.ceil(totalCells / 7);

        for (var row = 0; row < rows; row++) {
            html += '<tr>';
            for (var col = 0; col < 7; col++) {
                var cellIndex = row * 7 + col;
                var date;

                if (cellIndex < startDayOfWeek) {
                    var prevMonthNum = month === 1 ? 12 : month - 1;
                    var prevYear = month === 1 ? year - 1 : year;
                    date = new Date(prevYear, prevMonthNum - 1, daysFromPrevMonth - startDayOfWeek + cellIndex + 1);
                } else if (dayCount <= daysInMonth) {
                    date = new Date(year, month - 1, dayCount);
                    dayCount++;
                } else {
                    var nextMonthNum = month === 12 ? 1 : month + 1;
                    var nextYear = month === 12 ? year + 1 : year;
                    date = new Date(nextYear, nextMonthNum - 1, nextMonthDay);
                    nextMonthDay++;
                }

                html += renderCell(date, col);
            }
            html += '</tr>';
        }

        document.getElementById('calendar-body').innerHTML = html;
    }

    /**
     * Render week view calendar.
     * @returns {void}
     */
    function renderWeekView() {
        var html = '<tr>';
        for (var col = 0; col < 5; col++) {
            var date = new Date(currentWeekStart);
            date.setDate(date.getDate() + col);
            html += renderCell(date, col);
        }
        html += '</tr>';

        document.getElementById('calendar-body').innerHTML = html;
        document.getElementById('calendar').classList.add('calendar-week');
    }

    /**
     * Render calendar based on current viewport.
     * @returns {void}
     */
    function renderCalendar() {
        isWeekView = isMobile();
        updateNavVisibility();
        updateWeekStartInput();

        document.getElementById('calendar').classList.remove('calendar-week');

        if (isWeekView) {
            renderWeekView();
        } else {
            renderMonthView();
        }
    }

    /**
     * Initialize export button handler.
     * @returns {void}
     */
    function initExport() {
        var exportType = document.getElementById('export-type');
        var exportBtn = document.getElementById('export-btn');

        if (!exportBtn) return;

        exportBtn.addEventListener('click', function() {
            var type = exportType.value;
            var urls = calendarData.urls;

            if (isWeekView) {
                var weekStart = formatDateStr(currentWeekStart);
                var weekEnd = new Date(currentWeekStart);
                weekEnd.setDate(weekEnd.getDate() + 4);
                var weekEndStr = formatDateStr(weekEnd);

                switch (type) {
                    case 'pdf-matrix':
                        window.location.href = urls.matrix + '?week_start=' + weekStart + '&week_end=' + weekEndStr;
                        break;
                    case 'pdf-list':
                        window.location.href = urls.pdf + '?date_from=' + weekStart + '&date_to=' + weekEndStr;
                        break;
                    case 'ical':
                        window.location.href = urls.ical + '?date_from=' + weekStart + '&date_to=' + weekEndStr;
                        break;
                }
            } else {
                var year = calendarData.year;
                var month = calendarData.month;
                var firstDay = year + '-' + String(month).padStart(2, '0') + '-01';
                var lastDayDate = new Date(year, month, 0);
                var lastDay = year + '-' + String(month).padStart(2, '0') + '-' + String(lastDayDate.getDate()).padStart(2, '0');

                switch (type) {
                    case 'pdf-matrix':
                        window.location.href = urls.matrix + '?week_start=' + firstDay + '&week_end=' + lastDay;
                        break;
                    case 'pdf-list':
                        window.location.href = urls.pdf + '?date_from=' + firstDay + '&date_to=' + lastDay;
                        break;
                    case 'ical':
                        window.location.href = urls.ical + '?date_from=' + firstDay + '&date_to=' + lastDay;
                        break;
                }
            }
        });
    }

    /**
     * Initialize calendar renderer.
     * @returns {void}
     */
    function init() {
        var dataEl = document.getElementById('calendar-data');
        if (!dataEl) return;

        try {
            calendarData = JSON.parse(dataEl.textContent);
        } catch (e) {
            var calendarBody = document.getElementById('calendar-body');
            if (calendarBody) {
                var tr = document.createElement('tr');
                var td = document.createElement('td');
                td.setAttribute('colspan', '7');
                var mark = document.createElement('mark');
                mark.className = 'text-negative';
                mark.textContent = 'Fehler beim Laden der Kalenderdaten. Bitte Seite neu laden.';
                td.appendChild(mark);
                tr.appendChild(td);
                calendarBody.textContent = '';
                calendarBody.appendChild(tr);
            }
            return;
        }

        currentWeekStart = calendarData.urlWeekStart
            ? getWeekStart(new Date(calendarData.urlWeekStart))
            : getWeekStart(new Date(calendarData.today));

        renderCalendar();

        var resizeTimeout;
        window.addEventListener('resize', function() {
            clearTimeout(resizeTimeout);
            resizeTimeout = setTimeout(renderCalendar, 150);
        });

        initExport();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
