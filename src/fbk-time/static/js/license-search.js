/**
 * Filters license items based on search input.
 * @module license-search
 */
(function() {
    'use strict';

    var ANNOUNCE_DELAY_MS = 400;

    /**
     * Report the result count to a live region. Typing fires per keystroke,
     * so the update is delayed and skipped when unchanged; otherwise every
     * character would produce its own announcement.
     * @param {HTMLElement} region - The live region element.
     * @returns {Function} Callback taking the message to announce.
     */
    function announcer(region) {
        var timer = null;

        return function(message) {
            if (!region) {
                return;
            }
            // Cleared before the comparison: a pending timer may still hold
            // an outdated value even when the current one is unchanged.
            clearTimeout(timer);
            if (region.textContent === message) {
                return;
            }
            timer = setTimeout(function() {
                region.textContent = message;
            }, ANNOUNCE_DELAY_MS);
        };
    }

    /**
     * Initialize license search functionality.
     * @returns {void}
     */
    function init() {
        var searchInput = document.getElementById('license-search');
        var licenseItems = document.querySelectorAll('article[data-name]');
        var noResults = document.getElementById('no-results');
        var announce = announcer(document.getElementById('license-search-status'));

        if (!searchInput || !licenseItems.length) return;

        searchInput.addEventListener('input', function() {
            var query = this.value.toLowerCase().trim();
            var visibleCount = 0;

            licenseItems.forEach(function(item) {
                var name = item.dataset.name || '';
                var license = item.dataset.license || '';
                var matches = name.includes(query) || license.includes(query);

                if (matches) {
                    item.classList.remove('hidden');
                    visibleCount++;
                } else {
                    item.classList.add('hidden');
                }
            });

            if (noResults) {
                if (visibleCount === 0 && query.length > 0) {
                    noResults.classList.remove('hidden');
                } else {
                    noResults.classList.add('hidden');
                }
            }

            announce(query.length > 0 ? visibleCount + ' Treffer' : '');
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
