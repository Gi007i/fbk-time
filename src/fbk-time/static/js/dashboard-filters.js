/**
 * Client-side filtering for the dashboard today and week sections.
 * @module dashboard-filters
 */
(function() {
    'use strict';

    /**
     * Toggle visibility of list items by name and category.
     * @param {HTMLElement[]} items - List item elements to evaluate.
     * @param {string} query - Lowercased name search query.
     * @param {string} category - Category id to match, or empty for all.
     * @returns {number} Count of items left visible.
     */
    function filterItems(items, query, category) {
        var visible = 0;
        items.forEach(function(item) {
            var name = item.dataset.name || '';
            var itemCategory = item.dataset.category || '';
            var matches = name.indexOf(query) !== -1 &&
                (category === '' || itemCategory === category);
            item.classList.toggle('hidden', !matches);
            if (matches) {
                visible++;
            }
        });
        return visible;
    }

    /**
     * Initialize filtering for the today section (absent and present lists).
     */
    function initToday() {
        var search = document.getElementById('today-search');
        var categorySelect = document.getElementById('today-category');
        if (!search && !categorySelect) {
            return;
        }

        var lists = [
            {
                ul: document.getElementById('today-absent-list'),
                count: document.getElementById('today-absent-count'),
                empty: document.getElementById('today-absent-empty')
            },
            {
                ul: document.getElementById('today-present-list'),
                count: document.getElementById('today-present-count'),
                empty: document.getElementById('today-present-empty')
            }
        ];

        function apply() {
            var query = (search ? search.value : '').toLowerCase().trim();
            var category = categorySelect ? categorySelect.value : '';

            lists.forEach(function(list) {
                if (!list.ul) {
                    return;
                }
                var items = Array.prototype.slice.call(list.ul.querySelectorAll('li'));
                var visible = filterItems(items, query, category);
                if (list.count) {
                    list.count.textContent = visible;
                }
                if (list.empty) {
                    list.empty.classList.toggle('hidden', visible !== 0);
                }
            });
        }

        if (search) {
            search.addEventListener('input', apply);
        }
        if (categorySelect) {
            categorySelect.addEventListener('change', apply);
        }
    }

    /**
     * Initialize filtering for the week section (day groups).
     */
    function initWeek() {
        var groups = Array.prototype.slice.call(
            document.querySelectorAll('.week-day-group')
        );
        if (!groups.length) {
            return;
        }

        var search = document.getElementById('week-search');
        var daySelect = document.getElementById('week-day');
        var categorySelect = document.getElementById('week-category');
        var noResults = document.getElementById('week-no-results');

        function apply() {
            var query = (search ? search.value : '').toLowerCase().trim();
            var day = daySelect ? daySelect.value : '';
            var category = categorySelect ? categorySelect.value : '';
            var totalVisible = 0;

            groups.forEach(function(group) {
                var dayMatch = (day === '' || group.dataset.date === day);
                var dayVisible = 0;

                var statusGroups = Array.prototype.slice.call(
                    group.querySelectorAll('.week-status-group')
                );
                statusGroups.forEach(function(statusGroup) {
                    var items = Array.prototype.slice.call(
                        statusGroup.querySelectorAll('li')
                    );
                    var visible = dayMatch
                        ? filterItems(items, query, category)
                        : 0;

                    var statusCount = statusGroup.querySelector('.status-count');
                    if (statusCount && dayMatch) {
                        statusCount.textContent = visible;
                    }
                    statusGroup.classList.toggle('hidden', !dayMatch || visible === 0);
                    dayVisible += visible;
                });

                var dayCount = group.querySelector('.week-group-count');
                if (dayCount && dayMatch) {
                    dayCount.textContent = dayVisible;
                }
                group.classList.toggle('hidden', !dayMatch || dayVisible === 0);
                totalVisible += dayVisible;
            });

            if (noResults) {
                noResults.classList.toggle('hidden', totalVisible !== 0);
            }
        }

        if (search) {
            search.addEventListener('input', apply);
        }
        if (daySelect) {
            daySelect.addEventListener('change', apply);
        }
        if (categorySelect) {
            categorySelect.addEventListener('change', apply);
        }
    }

    /**
     * Wire up clear buttons for search inputs wrapped in .search-wrapper.
     * Clearing dispatches an input event so existing filters re-run.
     */
    function initSearchClear() {
        var wrappers = Array.prototype.slice.call(
            document.querySelectorAll('.search-wrapper')
        );
        wrappers.forEach(function(wrapper) {
            var input = wrapper.querySelector('input');
            var clear = wrapper.querySelector('.search-clear');
            if (!input || !clear) {
                return;
            }

            function toggle() {
                clear.classList.toggle('visible', input.value.length > 0);
            }

            input.addEventListener('input', toggle);
            clear.addEventListener('click', function() {
                input.value = '';
                clear.classList.remove('visible');
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.focus();
            });

            toggle();
        });
    }

    /**
     * Initialize all dashboard filters on DOM ready.
     */
    function init() {
        initToday();
        initWeek();
        initSearchClear();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
