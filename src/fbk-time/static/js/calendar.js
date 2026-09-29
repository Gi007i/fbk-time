/**
 * Renders month/week calendar views with absence data.
 * @module calendar
 */
(function() {
    'use strict';

    var MONTH_NAMES = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'];

    var calendarData = null;
    var currentWeekStart = null;
    var isWeekView = false;

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
        var input = document.querySelector('#filter-panel input[name="week_start"]');
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
     * Create an element with class name and optional text content.
     * @param {string} tag - Tag name.
     * @param {string} className - CSS class name.
     * @param {string} [text] - Text content.
     * @returns {HTMLElement} Created element.
     */
    function createEl(tag, className, text) {
        var el = document.createElement(tag);
        if (className) el.className = className;
        if (text !== undefined) el.textContent = text;
        return el;
    }

    /**
     * Render a single calendar cell.
     * @param {Date} date - Date for this cell.
     * @param {number} col - Column index (0-6, Mon-Sun).
     * @returns {HTMLTableCellElement} Table cell for the date.
     */
    function renderCell(date, col) {
        var dateStr = formatDateStr(date);
        var isWeekend = col >= 5;
        var isToday = dateStr === calendarData.today;
        var isCurrentMonth = date.getMonth() + 1 === calendarData.month;

        var cell = document.createElement('td');
        if (!isCurrentMonth && !isWeekView) cell.classList.add('day-other-month');
        if (isWeekend) cell.classList.add('day-weekend');
        if (isToday) cell.classList.add('day-today');
        cell.dataset.date = dateStr;

        cell.appendChild(createEl('span', 'day-number', String(date.getDate())));

        // The current day is marked visually only.
        if (isToday) {
            cell.appendChild(createEl('span', 'visually-hidden', 'Heute'));
        }

        if (calendarData.holidays[dateStr]) {
            var holidayWrap = document.createElement('span');
            holidayWrap.dataset.tooltip = calendarData.holidays[dateStr];
            var holidayMark = createEl('mark', 'absence-item');
            holidayMark.appendChild(createEl('small', '', calendarData.holidays[dateStr]));
            holidayWrap.appendChild(holidayMark);
            cell.appendChild(holidayWrap);
        }

        var dayOccurrences = getOccurrencesForDate(dateStr);
        var maxShow = isWeekView ? 5 : 3;
        for (var i = 0; i < Math.min(dayOccurrences.length, maxShow); i++) {
            var occ = dayOccurrences[i];
            var absenceId = parseInt(occ.absenceId, 10);
            var categoryId = parseInt(occ.categoryId, 10);
            if (isNaN(absenceId) || isNaN(categoryId)) continue;

            var isInternal = typeof occ.detailUrl === 'string' && /^\/(?![\/\\])/.test(occ.detailUrl);
            var link = createEl('a', 'absence-item category-' + categoryId);
            link.setAttribute('href', isInternal ? occ.detailUrl : '#');

            if (occ.categoryIconUrl) {
                var iconSpan = createEl('span', 'category-icon');
                var iconImg = document.createElement('img');
                iconImg.src = occ.categoryIconUrl;
                iconImg.alt = '';
                iconSpan.appendChild(iconImg);
                link.appendChild(iconSpan);
            }

            // The entry shows the category as colour and icon only.
            link.appendChild(createEl('span', 'visually-hidden', occ.categoryName + ': '));

            var repeatMark = '';
            if (isWeekView) {
                link.appendChild(createEl('span', 'absence-name', occ.userName));
                var meta = '';
                if (occ.isHalfDayMorning) meta += '(VM) ';
                else if (occ.isHalfDayAfternoon) meta += '(NM) ';
                if (meta) link.appendChild(createEl('span', 'absence-meta', meta));
                repeatMark = '🔁';
            } else {
                var text = occ.userName;
                if (occ.isHalfDayMorning) text += ' (VM)';
                else if (occ.isHalfDayAfternoon) text += ' (NM)';
                link.appendChild(document.createTextNode(occ.categoryIconUrl ? ' ' + text : text));
                repeatMark = ' 🔁';
            }

            // The symbol alone carries the series information.
            if (occ.isRecurring) {
                var repeat = createEl('span', 'absence-meta', repeatMark);
                repeat.setAttribute('aria-hidden', 'true');
                link.appendChild(repeat);
                link.appendChild(createEl('span', 'visually-hidden', ', Serie'));
            }

            var wrap = document.createElement('span');
            wrap.dataset.tooltip = occ.categoryName + ': ' + occ.userName + (occ.isHalfDayMorning ? ' (VM)' : occ.isHalfDayAfternoon ? ' (NM)' : '') + (occ.isPresent ? ' (A)' : ' (X)') + (occ.isRecurring ? ' (Serie)' : '');
            wrap.appendChild(link);
            cell.appendChild(wrap);
        }
        if (dayOccurrences.length > maxShow) {
            var moreLines = [];
            for (var j = maxShow; j < dayOccurrences.length; j++) {
                var hidden = dayOccurrences[j];
                var line = hidden.categoryName + ': ' + hidden.userName;
                if (hidden.isHalfDayMorning) line += ' (VM)';
                else if (hidden.isHalfDayAfternoon) line += ' (NM)';
                line += hidden.isPresent ? ' (A)' : ' (X)';
                if (hidden.isRecurring) line += ' (Serie)';
                moreLines.push(line);
            }
            var more = createEl('span', 'absence-more', '+' + (dayOccurrences.length - maxShow) + ' weitere');
            more.dataset.tooltip = moreLines.join('\n');
            // Without a tab stop the hidden entries stay pointer-only.
            more.setAttribute('tabindex', '0');
            more.appendChild(createEl('span', 'visually-hidden', ': ' + moreLines.join(', ')));
            cell.appendChild(more);
        }

        return cell;
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

        var fragment = document.createDocumentFragment();
        var dayCount = 1;
        var nextMonthDay = 1;
        var totalCells = startDayOfWeek + daysInMonth;
        var rows = Math.ceil(totalCells / 7);

        for (var row = 0; row < rows; row++) {
            var tr = document.createElement('tr');
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

                tr.appendChild(renderCell(date, col));
            }
            fragment.appendChild(tr);
        }

        document.getElementById('calendar-body').replaceChildren(fragment);
    }

    /**
     * Render week view calendar.
     * @returns {void}
     */
    function renderWeekView() {
        var tr = document.createElement('tr');
        for (var col = 0; col < 5; col++) {
            var date = new Date(currentWeekStart);
            date.setDate(date.getDate() + col);
            tr.appendChild(renderCell(date, col));
        }

        document.getElementById('calendar-body').replaceChildren(tr);
        document.getElementById('calendar').classList.add('calendar-week');
    }

    /**
     * Render calendar based on current viewport.
     * @returns {void}
     */
    function renderCalendar() {
        var restoreFocus = captureFocus();

        isWeekView = window.FBKTime.isMobile();
        updateNavVisibility();
        updateWeekStartInput();

        document.getElementById('calendar').classList.remove('calendar-week');

        if (isWeekView) {
            renderWeekView();
        } else {
            renderMonthView();
        }

        restoreFocus();
    }

    /**
     * Remember which day cell holds focus so it can be restored after the
     * body is rebuilt; a resize would otherwise drop focus to the document.
     * @returns {Function} Callback that restores the focus position.
     */
    function captureFocus() {
        var active = document.activeElement;
        var cell = active && typeof active.closest === 'function'
            ? active.closest('#calendar-body td[data-date]')
            : null;

        if (!cell) {
            return function() {};
        }

        var dateStr = cell.dataset.date;
        var focusables = cell.querySelectorAll('a[href], [tabindex="0"]');
        var index = Array.prototype.indexOf.call(focusables, active);

        return function() {
            var target = document.querySelector('#calendar-body td[data-date="' + dateStr + '"]');
            // Switching between month and week view drops most days; fall
            // back to the first reachable entry instead of losing focus.
            var scope = target || document.getElementById('calendar-body');
            if (!scope) return;
            var candidates = scope.querySelectorAll('a[href], [tabindex="0"]');
            var next = (target && candidates[index]) || candidates[0];
            if (next) next.focus();
        };
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
            var extra = window.FBKTime.buildFilterQuery(calendarData.filters);

            if (isWeekView) {
                var weekStart = formatDateStr(currentWeekStart);
                var weekEnd = new Date(currentWeekStart);
                weekEnd.setDate(weekEnd.getDate() + 4);
                var weekEndStr = formatDateStr(weekEnd);

                switch (type) {
                    case 'pdf-matrix':
                        window.FBKTime.downloadFile(urls.matrix + '?week_start=' + weekStart + '&week_end=' + weekEndStr + extra);
                        break;
                    case 'pdf-list':
                        window.FBKTime.downloadFile(urls.pdf + '?date_from=' + weekStart + '&date_to=' + weekEndStr + extra);
                        break;
                    case 'ical':
                        window.FBKTime.downloadFile(urls.ical + '?date_from=' + weekStart + '&date_to=' + weekEndStr + extra);
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
                        window.FBKTime.downloadFile(urls.matrix + '?week_start=' + firstDay + '&week_end=' + lastDay + extra);
                        break;
                    case 'pdf-list':
                        window.FBKTime.downloadFile(urls.pdf + '?date_from=' + firstDay + '&date_to=' + lastDay + extra);
                        break;
                    case 'ical':
                        window.FBKTime.downloadFile(urls.ical + '?date_from=' + firstDay + '&date_to=' + lastDay + extra);
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
                td.appendChild(createEl('mark', 'text-negative', 'Fehler beim Laden der Kalenderdaten. Bitte Seite neu laden.'));
                tr.appendChild(td);
                calendarBody.replaceChildren(tr);
            }
            return;
        }

        var weekStartParts = calendarData.weekStart.split('-');
        currentWeekStart = new Date(
            parseInt(weekStartParts[0], 10),
            parseInt(weekStartParts[1], 10) - 1,
            parseInt(weekStartParts[2], 10)
        );

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
