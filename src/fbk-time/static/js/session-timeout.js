/**
 * Session idle countdown with a keep-alive extension.
 * Shows the remaining session time as a live countdown in the header that
 * the user can click to extend, raises a modal warning dialog shortly
 * before expiry, and signs the user out when the countdown reaches zero.
 * Activity is mirrored across tabs so a background tab never signs out a
 * session that is still active elsewhere, and the countdown is capped at
 * the absolute session lifetime.
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
    var timerEl = document.getElementById('session-timer');

    if (!countdownEl || !extendBtn || !logoutButton || !keepaliveUrl || !logoutUrl) {
        return;
    }
    if (!(idleSeconds > 0) || !(warningSeconds > 0) || warningSeconds >= idleSeconds) {
        return;
    }
    if (!(remainingSeconds > 0)) {
        remainingSeconds = idleSeconds;
    }

    var absoluteDeadline = (absoluteSeconds > 0)
        ? Date.now() + absoluteSeconds * 1000
        : Infinity;

    var channel = (typeof BroadcastChannel === 'function')
        ? new BroadcastChannel('fbk-session-timeout')
        : null;

    var expireAt = 0;
    var warnAt = 0;
    var loggingOut = false;

    /**
     * Apply an expiry deadline, capped at the absolute lifetime, and reset
     * the dialog. Does not notify other tabs.
     * @param {number} deadline - Target expiry as epoch milliseconds.
     * @returns {void}
     */
    function applyDeadline(deadline) {
        expireAt = Math.min(deadline, absoluteDeadline);
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
            channel.postMessage(expireAt);
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
        if (loggingOut) {
            return;
        }
        loggingOut = true;

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
                registerActivity(result.data.remaining_seconds);
            } else {
                window.location.reload();
            }
        })
        .catch(function() {
            window.location.reload();
        });
    }

    /**
     * Evaluate the deadlines once per tick: log out, warn, and refresh the
     * visible countdown together with its emphasis state.
     * @returns {void}
     */
    function tick() {
        var now = Date.now();
        if (now >= expireAt) {
            forceLogout();
            return;
        }
        var label = formatDuration(Math.ceil((expireAt - now) / 1000));
        var warning = now >= warnAt;
        if (timerEl) {
            timerEl.textContent = label;
            timerEl.classList.toggle('contrast', warning);
            timerEl.classList.toggle('secondary', !warning);
        }
        if (warning) {
            if (!dialog.open) {
                dialog.showModal();
            }
            countdownEl.textContent = label;
        }
    }

    extendBtn.addEventListener('click', extend);
    logoutButton.addEventListener('click', forceLogout);
    if (timerEl) {
        timerEl.addEventListener('click', extend);
    }

    dialog.addEventListener('cancel', function(event) {
        event.preventDefault();
    });

    if (channel) {
        channel.onmessage = function(event) {
            if (Number.isFinite(event.data)) {
                applyDeadline(event.data);
            }
        };
    }

    // Any non-401 response counts as activity and defers the countdown.
    var originalFetch = window.fetch;
    if (typeof originalFetch === 'function') {
        window.fetch = function() {
            return originalFetch.apply(window, arguments).then(function(response) {
                if (!loggingOut && response.status !== 401) {
                    registerActivity(idleSeconds);
                }
                return response;
            });
        };
    }

    registerActivity(remainingSeconds);
    tick();
    setInterval(tick, 1000);
})();
