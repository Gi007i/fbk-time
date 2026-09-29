/**
 * Session idle countdown with a keep-alive extension, a warning dialog
 * before expiry and activity mirrored across tabs, so a background tab
 * never signs out a session that is still active elsewhere.
 * @module session-timeout
 */
(function() {
    'use strict';

    var dialog = document.getElementById('session-timeout');
    if (!dialog) {
        return;
    }

    var idleSeconds = parseInt(dialog.getAttribute('data-idle'), 10);
    var warningSeconds = parseInt(dialog.getAttribute('data-warning'), 10);
    var remainingSeconds = parseInt(dialog.getAttribute('data-remaining'), 10);
    var absoluteSeconds = parseInt(dialog.getAttribute('data-absolute'), 10);
    var keepaliveUrl = dialog.getAttribute('data-keepalive-url');
    var logoutUrl = dialog.getAttribute('data-logout-url');

    var countdownEl = dialog.querySelector('[data-countdown]');
    var extendBtn = dialog.querySelector('[data-extend]');
    var logoutButton = dialog.querySelector('[data-logout]');
    var absoluteNote = dialog.querySelector('[data-absolute-note]');
    var timerEl = document.getElementById('session-timer');

    if (!countdownEl || !extendBtn || !logoutButton || !absoluteNote
            || !keepaliveUrl || !logoutUrl) {
        return;
    }
    if (!(idleSeconds > 0) || !(warningSeconds > 0) || warningSeconds >= idleSeconds
            || !Number.isFinite(remainingSeconds)) {
        return;
    }

    var absoluteDeadline = (absoluteSeconds > 0)
        ? Date.now() + absoluteSeconds * 1000
        : Infinity;

    var channel = (typeof BroadcastChannel === 'function')
        ? new BroadcastChannel('fbk-session-timeout')
        : null;

    var expireAt = 0;
    var warnAt = 0;
    var leaving = false;
    var lastExtendable = null;
    var warningDismissed = false;

    // The remaining time arrives in whole seconds rounded down; wait until
    // the session has surely expired before reloading.
    var RELOAD_GRACE_MS = 2000;

    /**
     * Apply an expiry deadline, capped at the absolute lifetime, and reset
     * the dialog. Does not notify other tabs.
     * @param {number} deadline - Target expiry as epoch milliseconds.
     * @returns {void}
     */
    function applyDeadline(deadline) {
        var next = Math.min(deadline, absoluteDeadline);
        // Only real extra time justifies warning again: at the absolute cap
        // every activity elsewhere would otherwise reopen a dismissed dialog.
        if (next > expireAt) {
            warningDismissed = false;
        }
        expireAt = next;
        warnAt = expireAt - warningSeconds * 1000;
        if (dialog.open) {
            dialog.close();
        }
    }

    /**
     * Record fresh activity: reschedule from now and inform other tabs so a
     * background tab does not expire a session that is active elsewhere.
     * @param {number} seconds - Seconds from now until expiry, before the
     *   absolute-lifetime cap.
     * @returns {void}
     */
    function registerActivity(seconds) {
        applyDeadline(Date.now() + seconds * 1000);
        if (channel) {
            channel.postMessage({ expireAt: expireAt, absoluteAt: absoluteDeadline });
        }
    }

    /**
     * Report whether extending can still gain time: once a full idle window
     * reaches past the absolute deadline, the deadline no longer moves.
     * @returns {boolean} True while a keep-alive still defers expiry.
     */
    function canExtend() {
        return Date.now() + idleSeconds * 1000 < absoluteDeadline;
    }

    /**
     * Format the absolute deadline as a local wall-clock time.
     * @returns {string} The end time as HH:MM.
     */
    function absoluteEndLabel() {
        var end = new Date(absoluteDeadline);
        var hours = end.getHours();
        var minutes = end.getMinutes();
        return (hours < 10 ? '0' : '') + hours
            + ':' + (minutes < 10 ? '0' : '') + minutes;
    }

    /**
     * Mirror the current extendability into the controls. The dialog button
     * stays usable as a plain dismiss, otherwise the modal would trap the
     * user during the final minute with unsaved work on the page.
     * @returns {void}
     */
    function refreshExtendControls() {
        var extendable = canExtend();
        if (extendable === lastExtendable) {
            return;
        }
        lastExtendable = extendable;
        extendBtn.textContent = extendable ? 'Angemeldet bleiben' : 'Fenster schließen';
        absoluteNote.classList.toggle('hidden', extendable);
        if (timerEl) {
            var label = extendable
                ? 'Sitzung verlängern'
                : 'Sitzungsende um ' + absoluteEndLabel() + ' Uhr';
            timerEl.setAttribute('aria-disabled', extendable ? 'false' : 'true');
            timerEl.setAttribute('data-tooltip', label);
            timerEl.setAttribute('aria-label', label);
        }
    }

    /**
     * Format a whole-second duration as H:MM:SS, or M:SS below one hour.
     * @param {number} totalSeconds - Duration in seconds.
     * @returns {string} The duration with zero-padded lower components.
     */
    function formatDuration(totalSeconds) {
        var hours = Math.floor(totalSeconds / 3600);
        var minutes = Math.floor((totalSeconds % 3600) / 60);
        var seconds = totalSeconds % 60;
        var ss = (seconds < 10 ? '0' : '') + seconds;
        if (hours > 0) {
            return hours + ':' + (minutes < 10 ? '0' : '') + minutes + ':' + ss;
        }
        return minutes + ':' + ss;
    }

    /**
     * Sign the user out via a CSRF-protected POST. The dialog exists only
     * when scripting is active, so building the request here is safe.
     * @returns {void}
     */
    function forceLogout() {
        if (leaving) {
            return;
        }
        leaving = true;

        var form = document.createElement('form');
        form.method = 'POST';
        form.action = logoutUrl;

        var csrfInput = document.createElement('input');
        csrfInput.type = 'hidden';
        csrfInput.name = 'csrf_token';
        csrfInput.value = window.FBKTime.getCSRFToken();
        form.appendChild(csrfInput);

        document.body.appendChild(form);
        form.submit();
    }

    /**
     * Load the current page again via GET without adding a history entry,
     * so a page that answered a form submission is not submitted twice.
     * The fragment is dropped, otherwise the browser would only scroll.
     * @returns {void}
     */
    function reloadPage() {
        window.location.replace(window.location.href.split('#')[0]);
    }

    /**
     * Reload the page once the countdown has run out, so the next page
     * reflects the current sign-in state.
     * @returns {void}
     */
    function reloadExpired() {
        if (leaving) {
            return;
        }
        leaving = true;
        reloadPage();
    }

    /**
     * Extend the session via an explicit keep-alive request and reschedule
     * from the authoritative remaining time reported by the server.
     * @returns {void}
     */
    function extend() {
        fetch(keepaliveUrl, {
            method: 'POST',
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                'X-CSRFToken': window.FBKTime.getCSRFToken()
            }
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
            if (result.ok && typeof result.data.remaining_seconds === 'number'
                    && result.data.remaining_seconds > 0) {
                if (typeof result.data.absolute_seconds === 'number'
                        && result.data.absolute_seconds > 0) {
                    absoluteDeadline = Date.now() + result.data.absolute_seconds * 1000;
                }
                registerActivity(result.data.remaining_seconds);
            } else {
                reloadPage();
            }
        })
        .catch(function() {
            reloadPage();
        });
    }

    /**
     * Evaluate the deadlines once per tick: log out, warn, and refresh the
     * visible countdown together with its emphasis state.
     * @returns {void}
     */
    function tick() {
        var now = Date.now();
        if (now >= expireAt + RELOAD_GRACE_MS) {
            reloadExpired();
            return;
        }
        refreshExtendControls();
        var label = formatDuration(Math.max(0, Math.ceil((expireAt - now) / 1000)));
        var warning = now >= warnAt;
        if (timerEl) {
            timerEl.textContent = label;
            timerEl.classList.toggle('contrast', warning);
            timerEl.classList.toggle('secondary', !warning);
        }
        if (warning) {
            if (!dialog.open && !warningDismissed) {
                dialog.showModal();
            }
            countdownEl.textContent = label;
        }
    }

    /**
     * Handle the dialog's primary button: extend while that still gains
     * time, otherwise dismiss the warning for the current deadline.
     * @returns {void}
     */
    function extendOrDismiss() {
        if (canExtend()) {
            extend();
            return;
        }
        warningDismissed = true;
        dialog.close();
    }

    extendBtn.addEventListener('click', extendOrDismiss);
    logoutButton.addEventListener('click', forceLogout);
    if (timerEl) {
        timerEl.addEventListener('click', function() {
            if (canExtend()) {
                extend();
            }
        });
    }

    dialog.addEventListener('cancel', function(event) {
        if (canExtend()) {
            event.preventDefault();
            return;
        }
        warningDismissed = true;
    });

    if (channel) {
        channel.onmessage = function(event) {
            var data = event.data;
            if (!data || !Number.isFinite(data.expireAt)) {
                return;
            }
            if (typeof data.absoluteAt === 'number' && !Number.isNaN(data.absoluteAt)) {
                absoluteDeadline = data.absoluteAt;
            }
            applyDeadline(data.expireAt);
        };
    }

    // Any non-401 response counts as activity and defers the countdown.
    var originalFetch = window.fetch;
    if (typeof originalFetch === 'function') {
        window.fetch = function() {
            return originalFetch.apply(window, arguments).then(function(response) {
                if (!leaving && response.status !== 401) {
                    registerActivity(idleSeconds);
                }
                return response;
            });
        };
    }

    if (remainingSeconds <= 0) {
        setTimeout(reloadExpired, RELOAD_GRACE_MS);
        return;
    }
    registerActivity(remainingSeconds);
    tick();
    setInterval(tick, 1000);
})();
