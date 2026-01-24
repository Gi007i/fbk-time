/**
 * Handles responsive week/month view switching and export.
 * @module team-overview
 */
(function() {
    'use strict';

    var MOBILE_BREAKPOINT = 768;

    /**
     * Initialize team overview functionality.
     * @returns {void}
     */
    function init() {
        var dataEl = document.getElementById('team-overview-data');
        if (!dataEl) return;

        var weekNav = document.querySelector('.page-nav-week');
        var monthNav = document.querySelector('.page-nav-month');
        var weekMatrix = document.querySelector('.team-matrix-week');
        var monthMatrix = document.querySelector('.team-matrix-month');
        var exportType = document.getElementById('export-type');
        var exportBtn = document.getElementById('export-btn');

        var weekStart = dataEl.dataset.weekStart;
        var weekEnd = dataEl.dataset.weekEnd;
        var year = parseInt(dataEl.dataset.year, 10);
        var month = parseInt(dataEl.dataset.month, 10);
        var urlMatrix = dataEl.dataset.urlMatrix;
        var urlPdf = dataEl.dataset.urlPdf;
        var urlIcal = dataEl.dataset.urlIcal;

        /**
         * Check if viewport is mobile width.
         * @returns {boolean} True if mobile viewport.
         */
        function isMobile() {
            return window.innerWidth < MOBILE_BREAKPOINT;
        }

        /**
         * Update visibility of week/month views based on viewport.
         * @returns {void}
         */
        function updateViewVisibility() {
            if (isMobile()) {
                weekNav.classList.remove('hidden');
                monthNav.classList.add('hidden');
                weekMatrix.classList.remove('hidden');
                monthMatrix.classList.add('hidden');
            } else {
                weekNav.classList.add('hidden');
                monthNav.classList.remove('hidden');
                weekMatrix.classList.add('hidden');
                monthMatrix.classList.remove('hidden');
            }
        }

        updateViewVisibility();

        var resizeTimeout;
        window.addEventListener('resize', function() {
            clearTimeout(resizeTimeout);
            resizeTimeout = setTimeout(updateViewVisibility, 150);
        });

        if (exportBtn) {
            exportBtn.addEventListener('click', function() {
                var type = exportType.value;

                if (isMobile()) {
                    switch (type) {
                        case 'pdf-matrix':
                            window.location.href = urlMatrix + '?week_start=' + weekStart + '&week_end=' + weekEnd;
                            break;
                        case 'pdf-list':
                            window.location.href = urlPdf + '?date_from=' + weekStart + '&date_to=' + weekEnd;
                            break;
                        case 'ical':
                            window.location.href = urlIcal + '?date_from=' + weekStart + '&date_to=' + weekEnd;
                            break;
                    }
                } else {
                    var firstDay = year + '-' + String(month).padStart(2, '0') + '-01';
                    var lastDayDate = new Date(year, month, 0);
                    var lastDay = year + '-' + String(month).padStart(2, '0') + '-' + String(lastDayDate.getDate()).padStart(2, '0');

                    switch (type) {
                        case 'pdf-matrix':
                            window.location.href = urlMatrix + '?week_start=' + firstDay + '&week_end=' + lastDay;
                            break;
                        case 'pdf-list':
                            window.location.href = urlPdf + '?date_from=' + firstDay + '&date_to=' + lastDay;
                            break;
                        case 'ical':
                            window.location.href = urlIcal + '?date_from=' + firstDay + '&date_to=' + lastDay;
                            break;
                    }
                }
            });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
