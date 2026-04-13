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

        if (!checkboxes.length) {
            return;
        }

        initCheckboxListeners();
        initBulkActions();
        initExport();
    }

    function initExport() {
        var exportBtn = document.getElementById('list-export-btn');
        var exportType = document.getElementById('export-type');
        var dataEl = document.getElementById('list-export-data');

        if (!exportBtn || !dataEl) return;

        exportBtn.addEventListener('click', function() {
            var type = exportType.value;
            var urlPdf = dataEl.dataset.urlPdf;
            var urlIcal = dataEl.dataset.urlIcal;
            var urlMatrix = dataEl.dataset.urlMatrix;
            var params = [];

            if (dataEl.dataset.dateFrom) params.push('date_from=' + dataEl.dataset.dateFrom);
            if (dataEl.dataset.dateTo) params.push('date_to=' + dataEl.dataset.dateTo);
            if (dataEl.dataset.userId) params.push('user_id=' + dataEl.dataset.userId);
            if (dataEl.dataset.categoryId) params.push('category_id=' + dataEl.dataset.categoryId);

            var query = params.length ? '?' + params.join('&') : '';

            var matrixParams = [];
            if (dataEl.dataset.dateFrom) matrixParams.push('week_start=' + dataEl.dataset.dateFrom);
            if (dataEl.dataset.dateTo) matrixParams.push('week_end=' + dataEl.dataset.dateTo);
            var matrixQuery = matrixParams.length ? '?' + matrixParams.join('&') : '';

            switch (type) {
                case 'pdf-list':
                    window.location.href = urlPdf + query;
                    break;
                case 'pdf-matrix':
                    window.location.href = urlMatrix + matrixQuery;
                    break;
                case 'ical':
                    window.location.href = urlIcal + query;
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
            return response.json();
        })
        .then(function(data) {
            if (data.success) {
                var count = (data.data && data.data.deleted) || 0;
                var skipped = selectedIds.length - count;
                var message = count + ' Abwesenheit' + (count !== 1 ? 'en' : '') + ' gelöscht.';
                if (skipped > 0) {
                    message += ' ' + skipped + ' übersprungen (keine Berechtigung).';
                }
                Toast.store(message, 'success');
                location.reload();
            } else {
                Toast.error('Fehler: ' + (data.error || 'Unbekannter Fehler'));
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
