/**
 * Viewport-aware positioning for all [data-tooltip] anchors.
 * Renders a single shared, fixed-position bubble that prefers to sit above
 * the anchor, flips below when there is no room, and shifts horizontally so
 * it always stays fully inside the viewport. Replaces the framework's
 * pure-CSS tooltip, which is centred on the anchor and clips at screen edges.
 * @module tooltip
 */
(function() {
    'use strict';

    var GAP = 8;
    var MARGIN = 8;
    var HIDE_DELAY_MS = 150;
    var TOOLTIP_ID = 'shared-tooltip';
    var tooltip = null;
    var current = null;
    var hideTimer = null;

    /**
     * Return the shared tooltip element, creating it on first use.
     * @returns {HTMLElement} The tooltip container appended to the body.
     */
    function ensureTooltip() {
        if (tooltip) {
            return tooltip;
        }
        tooltip = document.createElement('div');
        tooltip.className = 'tooltip';
        tooltip.id = TOOLTIP_ID;
        tooltip.setAttribute('role', 'tooltip');
        tooltip.addEventListener('mouseenter', cancelHide);
        tooltip.addEventListener('mouseleave', scheduleHide);
        // The bubble overlays content and would swallow the click; it is
        // dismissed and the click handed to whatever sits underneath. A
        // synthesised click carries no modifier keys, so those are left to
        // the browser rather than silently turned into a plain click.
        tooltip.addEventListener('click', function(e) {
            hide();
            if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) {
                return;
            }
            var below = document.elementFromPoint(e.clientX, e.clientY);
            if (below && below !== tooltip && typeof below.click === 'function') {
                below.click();
            }
        });
        document.body.appendChild(tooltip);
        return tooltip;
    }

    /**
     * Resolve the form control a label belongs to.
     * @param {HTMLElement} label - The label or legend element.
     * @returns {HTMLElement|null} The control, or null when there is none.
     */
    function controlFor(label) {
        if (label.htmlFor) {
            return document.getElementById(label.htmlFor);
        }
        return label.querySelector('input, select, textarea');
    }

    /**
     * Append an id to an element's aria-describedby list.
     * @param {HTMLElement} element - The element to describe.
     * @param {string} id - The id of the describing element.
     * @returns {void}
     */
    function addDescribedBy(element, id) {
        var existing = element.getAttribute('aria-describedby');
        if (!existing) {
            element.setAttribute('aria-describedby', id);
            return;
        }
        if (existing.split(/\s+/).indexOf(id) === -1) {
            element.setAttribute('aria-describedby', existing + ' ' + id);
        }
    }

    /**
     * Make hint markers focusable and named so their text is not
     * reachable by pointer only, and tie the hint text to the field it
     * explains. The text node is placed outside the label so it does not
     * become part of the field's accessible name.
     * @returns {void}
     */
    function enhanceHints() {
        var hints = document.querySelectorAll('.info-tip');
        Array.prototype.forEach.call(hints, function(hint, index) {
            hint.setAttribute('tabindex', '0');
            hint.setAttribute('role', 'img');
            hint.setAttribute('aria-label', 'Hinweis');

            var text = hint.getAttribute('data-tooltip');
            var label = hint.closest('label, legend');
            if (!text || !text.trim() || !label) {
                return;
            }

            var target = label.tagName === 'LEGEND' ? label.parentNode : controlFor(label);
            if (!target) {
                return;
            }

            var note = document.createElement('span');
            note.id = 'hint-text-' + index;
            note.className = 'visually-hidden';
            note.textContent = text;
            label.parentNode.insertBefore(note, label.nextSibling);
            addDescribedBy(target, note.id);
        });
    }

    /**
     * Place the tooltip above the anchor, flipping below when there is no
     * room, and clamp it horizontally and vertically into the viewport.
     * @param {HTMLElement} anchor - The element the tooltip describes.
     * @returns {void}
     */
    function position(anchor) {
        if (!anchor.isConnected) {
            hide();
            return;
        }
        var tip = ensureTooltip();
        var rect = anchor.getBoundingClientRect();
        var tipRect = tip.getBoundingClientRect();
        var vw = document.documentElement.clientWidth;
        var vh = document.documentElement.clientHeight;

        if (rect.bottom < 0 || rect.top > vh || rect.right < 0 || rect.left > vw) {
            hide();
            return;
        }

        var top = rect.top - tipRect.height - GAP;
        if (top < MARGIN) {
            top = rect.bottom + GAP;
        }
        if (top + tipRect.height > vh - MARGIN) {
            top = Math.max(MARGIN, vh - tipRect.height - MARGIN);
        }

        var left = rect.left + rect.width / 2 - tipRect.width / 2;
        var maxLeft = vw - tipRect.width - MARGIN;
        if (left > maxLeft) {
            left = maxLeft;
        }
        if (left < MARGIN) {
            left = MARGIN;
        }

        tip.style.setProperty('--tt-x', Math.round(left) + 'px');
        tip.style.setProperty('--tt-y', Math.round(top) + 'px');
    }

    /**
     * Show the tooltip for an anchor if it carries non-empty text.
     * @param {HTMLElement} anchor - The element the tooltip describes.
     * @returns {void}
     */
    function show(anchor) {
        if (!anchor.isConnected) {
            return;
        }
        var text = anchor.getAttribute('data-tooltip');
        if (!text || !text.trim()) {
            return;
        }
        cancelHide();
        if (current && current !== anchor) {
            current.removeAttribute('aria-describedby');
        }
        var tip = ensureTooltip();
        tip.textContent = text;
        tip.classList.add('tooltip-visible');
        anchor.setAttribute('aria-describedby', TOOLTIP_ID);
        current = anchor;
        position(anchor);
    }

    /**
     * Hide the tooltip.
     * @returns {void}
     */
    function hide() {
        cancelHide();
        if (!tooltip) {
            return;
        }
        if (current) {
            current.removeAttribute('aria-describedby');
        }
        tooltip.classList.remove('tooltip-visible');
        current = null;
    }

    /**
     * Cancel a pending delayed hide.
     * @returns {void}
     */
    function cancelHide() {
        if (hideTimer !== null) {
            clearTimeout(hideTimer);
            hideTimer = null;
        }
    }

    /**
     * Hide after a short delay so the pointer can travel across the gap
     * between anchor and bubble without the bubble disappearing.
     * @returns {void}
     */
    function scheduleHide() {
        cancelHide();
        hideTimer = setTimeout(function() {
            // The pointer may wander off while the anchor still holds focus;
            // the bubble belongs to the focus in that case.
            if (current && (current === document.activeElement ||
                    current.contains(document.activeElement))) {
                return;
            }
            hide();
        }, HIDE_DELAY_MS);
    }

    /**
     * Resolve the nearest tooltip anchor for an event target.
     * @param {EventTarget} target - The event target to walk up from.
     * @returns {HTMLElement|null} The anchor, or null when there is none.
     */
    function anchorFor(target) {
        if (!target || typeof target.closest !== 'function') {
            return null;
        }
        return target.closest('[data-tooltip]');
    }

    document.addEventListener('mouseover', function(e) {
        var anchor = anchorFor(e.target);
        if (!anchor) {
            return;
        }
        if (anchor === current) {
            // Returning from the bubble to its own anchor: a hide is pending
            // from leaving the bubble and has to be called off.
            cancelHide();
            return;
        }
        show(anchor);
    });

    document.addEventListener('mouseout', function(e) {
        if (!current) {
            return;
        }
        if (tooltip && e.relatedTarget && tooltip.contains(e.relatedTarget)) {
            return;
        }
        var from = anchorFor(e.target);
        var to = anchorFor(e.relatedTarget);
        if (from === current && to !== current) {
            scheduleHide();
        }
    });

    document.addEventListener('focusin', function(e) {
        var anchor = anchorFor(e.target);
        if (anchor) {
            show(anchor);
        }
    });

    document.addEventListener('focusout', hide);

    document.addEventListener('click', function(e) {
        var anchor = anchorFor(e.target);
        if (!anchor) {
            hide();
            return;
        }
        if (e.target.closest('a, button, input, select, [role="button"]')) {
            return;
        }
        if (anchor.classList.contains('info-tip')) {
            // Hint markers sit inside labels; without this the click would be
            // forwarded to the field and change the user's input.
            e.preventDefault();
        }
        if (current === anchor) {
            hide();
        } else {
            show(anchor);
        }
    });

    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            hide();
        }
    });

    // Keyboard users reach an anchor by scrolling to it; hiding on scroll
    // would remove the bubble in the same moment it appears.
    var scrollPending = false;
    window.addEventListener('scroll', function() {
        if (!current || scrollPending) {
            return;
        }
        scrollPending = true;
        window.requestAnimationFrame(function() {
            scrollPending = false;
            if (current) {
                position(current);
            }
        });
    }, true);

    window.addEventListener('resize', hide);

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', enhanceHints);
    } else {
        enhanceHints();
    }
})();
