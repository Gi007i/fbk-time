/**
 * Core functionality for alerts, forms, navigation, and AJAX actions.
 * @module main
 */

(function() {
    'use strict';

    /**
     * Escape HTML special characters to prevent XSS.
     * Uses textContent trick recommended by OWASP.
     * @param {string} text - String to escape.
     * @returns {string} Escaped string safe for innerHTML.
     */
    function escapeHtml(text) {
        if (typeof text !== 'string') return '';
        var div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /**
     * Get CSRF token from meta tag.
     * @returns {string} CSRF token value.
     */
    function getCSRFToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    /**
     * Set button busy state with aria-busy attribute.
     * @param {HTMLButtonElement} button - Button element.
     * @param {boolean} busy - Whether button is busy.
     */
    function setButtonBusy(button, busy) {
        button.disabled = busy;
        if (busy) {
            button.setAttribute('aria-busy', 'true');
        } else {
            button.removeAttribute('aria-busy');
        }
    }

    /**
     * Initialize form validation feedback.
     */
    function initForms() {
        var forms = document.querySelectorAll('form');

        forms.forEach(function(form) {
            form.addEventListener('submit', function() {
                var submitButton = form.querySelector('button[type="submit"]');
                if (submitButton) {
                    submitButton.disabled = true;
                    submitButton.setAttribute('aria-busy', 'true');
                }
            });
        });
    }

    // Confirmation dialog state
    var confirmDialog = null;
    var confirmMessageEl = null;
    var confirmCallback = null;

    /**
     * Create the confirmation dialog element using DOM APIs.
     */
    function createConfirmDialog() {
        if (confirmDialog) return;

        confirmDialog = document.createElement('dialog');
        confirmDialog.id = 'confirm-dialog';

        var article = document.createElement('article');

        var header = document.createElement('header');
        var closeBtn = document.createElement('button');
        closeBtn.setAttribute('aria-label', 'Schließen');
        closeBtn.setAttribute('rel', 'prev');
        var heading = document.createElement('h3');
        heading.textContent = 'Bestätigung';
        header.appendChild(closeBtn);
        header.appendChild(heading);

        confirmMessageEl = document.createElement('p');

        var footer = document.createElement('footer');
        var cancelBtn = document.createElement('button');
        cancelBtn.className = 'outline secondary';
        cancelBtn.textContent = 'Abbrechen';
        cancelBtn.autofocus = true;
        var confirmBtn = document.createElement('button');
        confirmBtn.className = 'outline contrast';
        confirmBtn.textContent = 'Löschen';
        footer.appendChild(cancelBtn);
        footer.appendChild(confirmBtn);

        article.appendChild(header);
        article.appendChild(confirmMessageEl);
        article.appendChild(footer);
        confirmDialog.appendChild(article);
        document.body.appendChild(confirmDialog);

        function closeDialog() {
            confirmDialog.close();
            confirmCallback = null;
        }

        closeBtn.addEventListener('click', closeDialog);
        cancelBtn.addEventListener('click', closeDialog);

        confirmDialog.addEventListener('click', function(event) {
            if (event.target === confirmDialog) {
                closeDialog();
            }
        });

        confirmDialog.addEventListener('keydown', function(event) {
            if (event.key === 'Escape') {
                confirmCallback = null;
            }
        });

        confirmBtn.addEventListener('click', function() {
            confirmDialog.close();
            if (confirmCallback) {
                confirmCallback();
                confirmCallback = null;
            }
        });
    }

    /**
     * Show confirmation dialog with callback.
     * @param {string} message - Confirmation message.
     * @param {Function} onConfirm - Callback when confirmed.
     */
    function showConfirmDialog(message, onConfirm) {
        createConfirmDialog();
        confirmMessageEl.textContent = message;
        confirmCallback = onConfirm;
        confirmDialog.showModal();
    }

    /**
     * Execute AJAX POST action.
     * @param {string} url - Endpoint URL.
     * @param {HTMLButtonElement} button - Button for busy state.
     */
    function executeAjaxAction(url, button) {
        setButtonBusy(button, true);

        fetch(url, {
            method: 'POST',
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                'X-CSRFToken': getCSRFToken()
            }
        })
        .then(function(response) {
            return response.json().then(function(data) {
                return { ok: response.ok, status: response.status, data: data };
            });
        })
        .then(function(result) {
            if (result.ok && result.data.success) {
                if (result.data.message) {
                    Toast.store(result.data.message, 'success');
                }
                if (result.data.redirect) {
                    location.href = result.data.redirect;
                } else {
                    location.reload();
                }
            } else {
                Toast.error('Fehler: ' + (result.data.error || 'Unbekannter Fehler'));
                setButtonBusy(button, false);
            }
        })
        .catch(function(error) {
            Toast.error('Fehler bei der Verbindung: ' + error.message);
            setButtonBusy(button, false);
        });
    }

    /**
     * Handle category delete with check for existing absences.
     * @param {HTMLButtonElement} button - Delete button.
     */
    function handleCategoryDelete(button) {
        var url = button.dataset.url;
        if (!url) return;

        fetch(url + '?check=1', {
            method: 'GET',
            headers: {
                'X-Requested-With': 'XMLHttpRequest'
            }
        })
        .then(function(response) {
            return response.json().then(function(data) {
                return { ok: response.ok, status: response.status, data: data };
            });
        })
        .then(function(result) {
            if (!result.ok) {
                Toast.error('Fehler: ' + (result.data.error || 'Unbekannter Fehler'));
                return;
            }
            if (result.data.has_absences) {
                location.href = url;
            } else {
                var message = button.getAttribute('data-confirm') || 'Wirklich löschen?';
                showConfirmDialog(message, function() {
                    executeAjaxAction(url, button);
                });
            }
        })
        .catch(function(error) {
            Toast.error('Fehler beim Prüfen: ' + error.message);
        });
    }

    /**
     * Initialize AJAX actions via event delegation.
     * Handles data-action="toggle" and data-action="delete" buttons.
     */
    function initAjaxActions() {
        document.addEventListener('click', function(event) {
            var button = event.target.closest('[data-action]');
            if (!button) return;

            var action = button.dataset.action;
            var url = button.dataset.url;
            if (!url) return;

            event.preventDefault();

            if (action === 'toggle') {
                executeAjaxAction(url, button);
            } else if (action === 'delete') {
                var isCategoryPage = location.pathname.includes('/categories');
                if (isCategoryPage) {
                    handleCategoryDelete(button);
                } else {
                    var message = button.getAttribute('data-confirm') || 'Wirklich löschen?';
                    showConfirmDialog(message, function() {
                        executeAjaxAction(url, button);
                    });
                }
            }
        });
    }

    /**
     * Initialize confirmation dialogs for form-based delete actions.
     * Only handles buttons with data-confirm but without data-action.
     */
    function initFormConfirmations() {
        document.addEventListener('click', function(event) {
            var button = event.target.closest('[data-confirm]:not([data-action])');
            if (!button) return;

            var form = button.closest('form');
            if (!form) return;

            event.preventDefault();

            var message = button.getAttribute('data-confirm') || 'Wirklich löschen?';
            showConfirmDialog(message, function() {
                form.submit();
            });
        });
    }

    /**
     * Initialize table sorting functionality.
     */
    function initTableSorting() {
        var sortableHeaders = document.querySelectorAll('th[data-sort]');

        sortableHeaders.forEach(function(header) {
            header.addEventListener('click', function() {
                var table = header.closest('table');
                var tbody = table.querySelector('tbody');
                var rows = Array.from(tbody.querySelectorAll('tr'));
                var column = header.getAttribute('data-sort');
                var columnIndex = Array.from(header.parentNode.children).indexOf(header);
                var isAscending = header.classList.contains('sort-asc');

                rows.sort(function(a, b) {
                    var aValue = a.children[columnIndex].textContent.trim();
                    var bValue = b.children[columnIndex].textContent.trim();

                    if (column === 'date') {
                        return isAscending
                            ? new Date(bValue) - new Date(aValue)
                            : new Date(aValue) - new Date(bValue);
                    }

                    return isAscending
                        ? bValue.localeCompare(aValue, 'de')
                        : aValue.localeCompare(bValue, 'de');
                });

                sortableHeaders.forEach(function(h) {
                    h.classList.remove('sort-asc', 'sort-desc');
                });
                header.classList.add(isAscending ? 'sort-desc' : 'sort-asc');

                rows.forEach(function(row) {
                    tbody.appendChild(row);
                });
            });
        });
    }

    /**
     * Initialize filter panel state persistence via sessionStorage.
     * Panel stays open until user manually closes it.
     */
    function initFilterPanelState() {
        var STORAGE_KEY = 'filterPanelOpen_' + location.pathname;
        var panel = document.getElementById('filter-panel');

        if (!panel) return;

        var savedState = sessionStorage.getItem(STORAGE_KEY);
        if (savedState === 'true') {
            panel.open = true;
        }

        panel.addEventListener('toggle', function() {
            sessionStorage.setItem(STORAGE_KEY, panel.open);
        });
    }

    /**
     * Initialize AJAX form submission for forms with data-ajax-form attribute.
     */
    function initAjaxForms() {
        document.addEventListener('submit', function(event) {
            var form = event.target;
            if (!form.hasAttribute('data-ajax-form')) return;

            event.preventDefault();

            var submitBtn = form.querySelector('button[type="submit"]');
            if (submitBtn) {
                setButtonBusy(submitBtn, true);
            }

            var formData = new FormData(form);

            fetch(form.action, {
                method: form.method || 'POST',
                headers: {
                    'X-Requested-With': 'XMLHttpRequest',
                    'X-CSRFToken': getCSRFToken()
                },
                body: new URLSearchParams(formData)
            })
            .then(function(response) {
                return response.json().then(function(data) {
                    return { ok: response.ok, status: response.status, data: data };
                });
            })
            .then(function(result) {
                if (submitBtn) {
                    setButtonBusy(submitBtn, false);
                }

                if (result.ok && result.data.success) {
                    if (result.data.message) {
                        if (result.data.redirect) {
                            Toast.store(result.data.message, 'success');
                        } else {
                            Toast.success(result.data.message);
                        }
                    }
                    if (result.data.redirect) {
                        location.href = result.data.redirect;
                    }
                } else {
                    var errorMsg = result.data.error || 'Ein Fehler ist aufgetreten.';
                    Toast.error(errorMsg);

                    if (result.data.errors) {
                        Object.keys(result.data.errors).forEach(function(fieldName) {
                            var field = form.querySelector('[name="' + fieldName + '"]');
                            if (field) {
                                showFieldError(field, result.data.errors[fieldName]);
                            }
                        });
                    }
                }
            })
            .catch(function(error) {
                if (submitBtn) {
                    setButtonBusy(submitBtn, false);
                }
                Toast.error('Fehler bei der Verbindung: ' + error.message);
            });
        });
    }

    /**
     * Initialize all functionality on DOM ready.
     */
    function init() {
        initForms();
        initAjaxActions();
        initAjaxForms();
        initFormConfirmations();
        initTableSorting();
        initFilterPanelState();
    }

    /**
     * Handle navigation buttons via event delegation.
     * Supports data-back (history.back) and data-href (internal navigation).
     * Also supports clickable table rows with data-href.
     */
    document.addEventListener('click', function(event) {
        var target = event.target.closest('[data-back], [data-href]');
        if (!target) return;

        var clickedInteractive = event.target.closest('a, button, [type="checkbox"]');
        if (clickedInteractive && clickedInteractive !== target) {
            return;
        }

        if (target.tagName === 'BUTTON' || target.tagName === 'A') {
            event.preventDefault();
        }

        if (target.hasAttribute('data-back')) {
            history.back();
        } else {
            var href = target.dataset.href;
            if (href && href.startsWith('/') && !href.startsWith('//')) {
                location.href = href;
            }
        }
    });

    /**
     * Handle auto-submit for form elements via event delegation.
     * Elements with data-autosubmit attribute submit their parent form on change.
     */
    document.addEventListener('change', function(event) {
        var target = event.target;
        if (!target.hasAttribute('data-autosubmit')) return;

        var form = target.closest('form');
        if (form) {
            form.submit();
        }
    });

    /**
     * Show inline validation error for a form field.
     * @param {HTMLElement} field - The form field element.
     * @param {string} message - Error message to display.
     */
    function showFieldError(field, message) {
        clearFieldError(field);
        field.setAttribute('aria-invalid', 'true');

        var small = document.createElement('small');
        small.className = 'js-validation-error';
        small.textContent = message;

        var insertAfter = field;
        var wrapper = field.closest('.emoji-picker-wrapper');
        if (wrapper) {
            insertAfter = wrapper;
        }

        if (insertAfter.nextSibling) {
            insertAfter.parentNode.insertBefore(small, insertAfter.nextSibling);
        } else {
            insertAfter.parentNode.appendChild(small);
        }
    }

    /**
     * Show inline validation error for a fieldset (checkbox/radio group).
     * @param {HTMLElement} fieldset - The fieldset element.
     * @param {string} message - Error message to display.
     */
    function showFieldsetError(fieldset, message) {
        clearFieldsetError(fieldset);
        fieldset.setAttribute('aria-invalid', 'true');

        var small = document.createElement('small');
        small.className = 'js-validation-error';
        small.textContent = message;
        fieldset.appendChild(small);
    }

    /**
     * Clear inline validation error for a form field.
     * @param {HTMLElement} field - The form field element.
     */
    function clearFieldError(field) {
        field.removeAttribute('aria-invalid');
        var label = field.closest('label');
        if (label) {
            var existing = label.querySelector('.js-validation-error');
            if (existing) existing.remove();
        }
    }

    /**
     * Clear inline validation error for a fieldset.
     * @param {HTMLElement} fieldset - The fieldset element.
     */
    function clearFieldsetError(fieldset) {
        fieldset.removeAttribute('aria-invalid');
        var existing = fieldset.querySelector('.js-validation-error');
        if (existing) existing.remove();
    }

    /**
     * Clear all JS validation errors from a form.
     * @param {HTMLElement} formEl - The form element.
     */
    function clearAllFieldErrors(formEl) {
        var errors = formEl.querySelectorAll('.js-validation-error');
        errors.forEach(function(el) { el.remove(); });

        var invalidFields = formEl.querySelectorAll('[aria-invalid="true"]');
        invalidFields.forEach(function(el) { el.removeAttribute('aria-invalid'); });
    }

    // Export utility functions to global scope
    window.FBKTime = window.FBKTime || {};
    window.FBKTime.escapeHtml = escapeHtml;
    window.FBKTime.getCSRFToken = getCSRFToken;
    window.FBKTime.showConfirmDialog = showConfirmDialog;
    window.FBKTime.showFieldError = showFieldError;
    window.FBKTime.showFieldsetError = showFieldsetError;
    window.FBKTime.clearFieldError = clearFieldError;
    window.FBKTime.clearFieldsetError = clearFieldsetError;
    window.FBKTime.clearAllFieldErrors = clearAllFieldErrors;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
