/**
 * Centralized toast notification system.
 * @module toast
 */

var Toast = (function() {
    'use strict';

    var MAX_VISIBLE = 3;
    var MAX_QUEUED = 10;
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
     * Count visible toasts that expire on their own. Errors and warnings
     * stay until closed and are left out of the limit, otherwise three of
     * them would block every later message from ever appearing.
     * @returns {number} Number of self-expiring toasts on screen.
     */
    function getVisibleCount() {
        var c = getContainer();
        if (!c) {
            return 0;
        }
        return c.querySelectorAll(
            '.toast:not(.toast-fadeout):not(.alert-danger):not(.alert-warning)'
        ).length;
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
     * Arm auto-dismissal for a toast. Errors and warnings stay until closed,
     * and the timer pauses while the message is hovered or focused so it
     * cannot vanish mid-read (WCAG 2.2.1).
     * @param {HTMLElement} toast - Toast element.
     * @param {string} type - Toast type.
     */
    function armDismiss(toast, type) {
        if (type === 'danger' || type === 'warning') {
            return;
        }

        var timer = null;

        function stop() {
            if (timer !== null) {
                clearTimeout(timer);
                timer = null;
            }
        }

        function start() {
            stop();
            timer = setTimeout(function() {
                if (toast.parentNode && !toast.contains(document.activeElement)) {
                    dismiss(toast);
                }
            }, AUTO_DISMISS_MS);
        }

        toast.addEventListener('mouseenter', stop);
        toast.addEventListener('focusin', stop);
        toast.addEventListener('mouseleave', start);
        toast.addEventListener('focusout', start);
        start();
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
        toast.className = 'toast alert-' + type;

        var span = document.createElement('span');
        span.textContent = message;

        var closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.className = 'toast-close';
        closeBtn.setAttribute('aria-label', 'Meldung schließen');
        closeBtn.textContent = '\u00D7';
        closeBtn.addEventListener('click', function() {
            dismiss(toast);
        });

        toast.appendChild(span);
        toast.appendChild(closeBtn);
        c.appendChild(toast);

        armDismiss(toast, type);
    }

    /**
     * Dismiss a toast with animation.
     * @param {HTMLElement} toast - Toast element to dismiss.
     */
    function dismiss(toast) {
        // Errors do not expire, so closing by keyboard is the regular
        // way out; removing the node would drop focus to the document.
        var hadFocus = toast.contains(document.activeElement);

        toast.classList.add('toast-fadeout');
        setTimeout(function() {
            if (toast.parentNode) {
                toast.remove();
            }
            if (hadFocus) {
                var next = getContainer();
                var closeBtn = next
                    ? next.querySelector('.toast:not(.toast-fadeout) .toast-close')
                    : null;
                var fallback = document.getElementById('main-content');
                // Without preventScroll a mouse user closing a toast far
                // down the page would be thrown back to the top.
                if (closeBtn) {
                    closeBtn.focus({ preventScroll: true });
                } else if (fallback) {
                    fallback.focus({ preventScroll: true });
                }
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
            if (queue.length < MAX_QUEUED) {
                queue.push({ message: message, type: type });
            }
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

            // Messages present when the live region is parsed are not
            // announced; re-inserting them makes it an update.
            c.appendChild(toast);

            // An unknown class must not silently fall back to a type that
            // auto-dismisses; such a message stays until it is closed.
            var type = 'danger';
            ['success', 'info', 'warning'].forEach(function(name) {
                if (toast.classList.contains('alert-' + name)) {
                    type = name;
                }
            });
            armDismiss(toast, type);
        });
    }

    /**
     * Initialize the toast system.
     */
    function init() {
        showStored();
        initExisting();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    return {
        show: show,
        success: success,
        error: error,
        warning: warning,
        store: store,
        showStored: showStored
    };
})();
