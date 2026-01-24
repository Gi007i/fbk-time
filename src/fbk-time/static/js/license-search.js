/**
 * Filters license items based on search input.
 * @module license-search
 */
(function() {
    'use strict';

    /**
     * Initialize license search functionality.
     * @returns {void}
     */
    function init() {
        var searchInput = document.getElementById('license-search');
        var licenseItems = document.querySelectorAll('article[data-name]');
        var noResults = document.getElementById('no-results');

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
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
