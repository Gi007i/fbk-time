/**
 * Checkbox selection and bulk delete operations for absence list.
 * @module absence-list
 */

(function() {
    'use strict';

    var selectedIds = [];
    var selectAllCheckbox;
    var checkboxes;
    var bulkActionsBar;
    var selectionCount;
    var bulkDeleteBtn;
    var clearSelectionBtn;

    function init() {
        selectAllCheckbox = document.getElementById('select-all');
        checkboxes = document.querySelectorAll('input[type="checkbox"][data-bulk-item]');
        bulkActionsBar = document.querySelector('.bulk-actions-bar');
        selectionCount = document.getElementById('selection-count');
        bulkDeleteBtn = document.getElementById('bulk-delete-btn');
        clearSelectionBtn = document.getElementById('clear-selection-btn');

        initExport();
        initTruncationTooltips();

        if (!checkboxes.length) {
            return;
        }

        initCheckboxListeners();
        initBulkActions();
    }

    /**
     * Attach a tooltip with the full text to any cell whose label is
     * truncated, and remove it where the label fits. Mirrors the team overview
     * behaviour; on the card layout labels are shown in full and get none.
     * @returns {void}
     */
    function refreshTruncationTooltips() {
        var labels = document.querySelectorAll('.list-name span, .list-cards .list-truncate, .list-cards .category-badge');
        labels.forEach(function(label) {
            var cell = label.closest('td');
            if (!cell) return;
            if (label.scrollWidth > label.clientWidth) {
                cell.setAttribute('data-tooltip', label.textContent.trim());
                // A tab stop is only needed where the cell has no focusable
                // child of its own; otherwise it just adds a dead station.
                if (!cell.querySelector('a[href], button')) {
                    cell.setAttribute('tabindex', '0');
                }
            } else {
                cell.removeAttribute('data-tooltip');
                if (document.activeElement !== cell) {
                    cell.removeAttribute('tabindex');
                }
            }
        });
    }

    /**
     * Wire up truncation tooltips on load and recompute them after viewport changes.
     * @returns {void}
     */
    function initTruncationTooltips() {
        refreshTruncationTooltips();
        var resizeTimeout;
        window.addEventListener('resize', function() {
            clearTimeout(resizeTimeout);
            resizeTimeout = setTimeout(refreshTruncationTooltips, 150);
        });
    }

    function initExport() {
        var exportBtn = document.getElementById('list-export-btn');
        var exportType = document.getElementById('export-type');
        var dataEl = document.getElementById('list-export-data');

        if (!exportBtn || !dataEl) return;

        /**
         * Build an export query with date bounds and the active subject filter.
         * @param {string} startKey - Query key for the range start.
         * @param {string} endKey - Query key for the range end.
         * @returns {string} Query string ('?...'), empty when no parameters.
         */
        function buildQuery(startKey, endKey) {
            var params = [];
            if (dataEl.dataset.dateFrom) params.push(startKey + '=' + encodeURIComponent(dataEl.dataset.dateFrom));
            if (dataEl.dataset.dateTo) params.push(endKey + '=' + encodeURIComponent(dataEl.dataset.dateTo));

            var query = params.length ? '?' + params.join('&') : '';
            var filters = window.FBKTime.buildFilterQuery(dataEl.dataset);
            if (filters) {
                query += query ? filters : '?' + filters.substring(1);
            }
            return query;
        }

        exportBtn.addEventListener('click', function() {
            var type = exportType.value;
            var urlPdf = dataEl.dataset.urlPdf;
            var urlIcal = dataEl.dataset.urlIcal;
            var urlMatrix = dataEl.dataset.urlMatrix;

            var query = buildQuery('date_from', 'date_to');
            var matrixQuery = buildQuery('week_start', 'week_end');

            switch (type) {
                case 'pdf-list':
                    window.FBKTime.downloadFile(urlPdf + query);
                    break;
                case 'pdf-matrix':
                    window.FBKTime.downloadFile(urlMatrix + matrixQuery);
                    break;
                case 'ical':
                    window.FBKTime.downloadFile(urlIcal + query);
                    break;
            }
        });
    }

    function initCheckboxListeners() {
        checkboxes.forEach(function(checkbox) {
            checkbox.addEventListener('change', function() {
                updateSelectedIds();
                updateSelectAllState();
                updateBulkActionsUI();
            });
        });

        if (selectAllCheckbox) {
            selectAllCheckbox.addEventListener('change', function() {
                var isChecked = selectAllCheckbox.checked;
                checkboxes.forEach(function(checkbox) {
                    checkbox.checked = isChecked;
                });
                updateSelectedIds();
                updateBulkActionsUI();
            });
        }
    }

    function initBulkActions() {
        if (bulkDeleteBtn) {
            bulkDeleteBtn.addEventListener('click', bulkDelete);
        }

        if (clearSelectionBtn) {
            clearSelectionBtn.addEventListener('click', clearSelection);
        }
    }

    function updateSelectedIds() {
        selectedIds = [];
        checkboxes.forEach(function(checkbox) {
            if (checkbox.checked) {
                selectedIds.push(checkbox.value);
            }
        });
    }

    function updateSelectAllState() {
        if (!selectAllCheckbox) {
            return;
        }

        var allChecked = Array.from(checkboxes).every(function(cb) {
            return cb.checked;
        });
        selectAllCheckbox.checked = allChecked && checkboxes.length > 0;
    }

    function updateBulkActionsUI() {
        if (!bulkActionsBar) {
            return;
        }

        if (selectedIds.length > 0) {
            bulkActionsBar.classList.remove('hidden');
            if (selectionCount) {
                selectionCount.textContent = selectedIds.length;
            }
        } else {
            bulkActionsBar.classList.add('hidden');
        }
    }

    function clearSelection() {
        checkboxes.forEach(function(checkbox) {
            checkbox.checked = false;
        });
        if (selectAllCheckbox) {
            selectAllCheckbox.checked = false;
        }
        updateSelectedIds();
        updateBulkActionsUI();
    }

    function bulkDelete() {
        if (selectedIds.length === 0) {
            return;
        }

        var message = selectedIds.length === 1
            ? '1 Eintrag wirklich löschen?'
            : selectedIds.length + ' Einträge wirklich löschen?';

        window.FBKTime.showConfirmDialog(message, executeBulkDelete);
    }

    function executeBulkDelete() {
        bulkDeleteBtn.disabled = true;
        bulkDeleteBtn.setAttribute('aria-busy', 'true');

        fetch('/absences/api/bulk-delete', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-Requested-With': 'XMLHttpRequest',
                'X-CSRFToken': window.FBKTime.getCSRFToken()
            },
            body: JSON.stringify({ ids: selectedIds })
        })
        .then(function(response) {
            return response.json().then(function(data) {
                return { ok: response.ok, status: response.status, data: data };
            });
        })
        .then(function(result) {
            if (result.status === 401 && result.data && result.data.redirect) {
                window.location.href = result.data.redirect;
                return;
            }
            if (result.ok && result.data.success) {
                var counts = result.data.data;
                var message = counts.deleted + ' Abwesenheit' + (counts.deleted !== 1 ? 'en' : '') + ' gelöscht.';
                var skipped = [];
                if (counts.forbidden > 0) {
                    skipped.push(counts.forbidden + ' ohne Berechtigung');
                }
                if (counts.not_found > 0) {
                    skipped.push(counts.not_found + ' nicht mehr vorhanden');
                }
                if (counts.invalid > 0) {
                    skipped.push(counts.invalid + ' ungültig');
                }
                if (skipped.length) {
                    message += ' Übersprungen: ' + skipped.join(', ') + '.';
                }
                Toast.store(message, skipped.length ? 'warning' : 'success');
                location.reload();
            } else {
                Toast.error('Fehler: ' + (result.data.error || 'Unbekannter Fehler'));
                bulkDeleteBtn.disabled = false;
                bulkDeleteBtn.removeAttribute('aria-busy');
            }
        })
        .catch(function(error) {
            Toast.error('Fehler beim Löschen: ' + error.message);
            bulkDeleteBtn.disabled = false;
            bulkDeleteBtn.removeAttribute('aria-busy');
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
