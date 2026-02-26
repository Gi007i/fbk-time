/**
 * Centralized toast notification system.
 * @module toast
 */

var Toast = (function() {
    'use strict';

    var MAX_VISIBLE = 3;
    var AUTO_DISMISS_MS = 5000;
    var FADEOUT_MS = 300;
    var STORAGE_KEY = 'toast_message';

    var container = null;
    var queue = [];

    /**
     * Get or create the toast container element.
     * @returns {HTMLElement} Toast container.
     */
    function getContainer() {
        if (!container) {
            container = document.getElementById('toast-container');
        }
        return container;
    }

    /**
     * Count currently visible toasts.
     * @returns {number} Number of visible toasts.
     */
    function getVisibleCount() {
        var c = getContainer();
        return c ? c.querySelectorAll('.toast:not(.toast-fadeout)').length : 0;
    }

    /**
     * Process the queue and show pending toasts.
     */
    function processQueue() {
        while (queue.length > 0 && getVisibleCount() < MAX_VISIBLE) {
            var item = queue.shift();
            createToast(item.message, item.type);
        }
    }

    /**
     * Create and display a toast notification.
     * @param {string} message - Message to display.
     * @param {string} type - Toast type ('success', 'danger', 'warning', 'info').
     */
    function createToast(message, type) {
        var c = getContainer();
        if (!c) return;

        var toast = document.createElement('article');
        toast.setAttribute('role', 'alert');
        toast.className = 'toast alert-' + type;

        var span = document.createElement('span');
        span.textContent = message;

        var closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.className = 'toast-close';
        closeBtn.setAttribute('aria-label', 'Schließen');
        closeBtn.textContent = '\u00D7';
        closeBtn.addEventListener('click', function() {
            dismiss(toast);
        });

        toast.appendChild(span);
        toast.appendChild(closeBtn);
        c.appendChild(toast);

        setTimeout(function() {
            if (toast.parentNode) {
                dismiss(toast);
            }
        }, AUTO_DISMISS_MS);
    }

    /**
     * Dismiss a toast with animation.
     * @param {HTMLElement} toast - Toast element to dismiss.
     */
    function dismiss(toast) {
        toast.classList.add('toast-fadeout');
        setTimeout(function() {
            if (toast.parentNode) {
                toast.remove();
            }
            processQueue();
        }, FADEOUT_MS);
    }

    /**
     * Show a toast notification.
     * @param {string} message - Message to display.
     * @param {string} type - Toast type ('success', 'danger', 'warning', 'info').
     */
    function show(message, type) {
        if (getVisibleCount() >= MAX_VISIBLE) {
            queue.push({ message: message, type: type });
        } else {
            createToast(message, type);
        }
    }

    /**
     * Show a success toast.
     * @param {string} message - Message to display.
     */
    function success(message) {
        show(message, 'success');
    }

    /**
     * Show an error toast.
     * @param {string} message - Message to display.
     */
    function error(message) {
        show(message, 'danger');
    }

    /**
     * Show a warning toast.
     * @param {string} message - Message to display.
     */
    function warning(message) {
        show(message, 'warning');
    }

    /**
     * Show an info toast.
     * @param {string} message - Message to display.
     */
    function info(message) {
        show(message, 'info');
    }

    /**
     * Store a toast message for display after redirect.
     * @param {string} message - Message to store.
     * @param {string} type - Toast type.
     */
    function store(message, type) {
        try {
            sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
                message: message,
                type: type || 'success'
            }));
        } catch (e) {
            // sessionStorage not available
        }
    }

    /**
     * Show stored toast message from sessionStorage.
     */
    function showStored() {
        try {
            var stored = sessionStorage.getItem(STORAGE_KEY);
            if (stored) {
                sessionStorage.removeItem(STORAGE_KEY);
                var data = JSON.parse(stored);
                show(data.message, data.type);
            }
        } catch (e) {
            // sessionStorage not available or invalid JSON
        }
    }

    /**
     * Initialize existing server-rendered toasts with close buttons and auto-dismiss.
     */
    function initExisting() {
        var c = getContainer();
        if (!c) return;

        var toasts = c.querySelectorAll('.toast');
        toasts.forEach(function(toast) {
            var closeBtn = toast.querySelector('.toast-close');
            if (closeBtn) {
                closeBtn.addEventListener('click', function() {
                    dismiss(toast);
                });
            }

            setTimeout(function() {
                if (toast.parentNode) {
                    dismiss(toast);
                }
            }, AUTO_DISMISS_MS);
        });
    }

    /**
     * Initialize the toast system.
     */
    function init() {
        showStored();
        initExisting();
    }

    // Auto-initialize on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Public API
    return {
        show: show,
        success: success,
        error: error,
        warning: warning,
        info: info,
        store: store,
        showStored: showStored,
        dismiss: dismiss
    };
})();
