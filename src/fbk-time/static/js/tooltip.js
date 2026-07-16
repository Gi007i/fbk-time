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
    var tooltip = null;
    var current = null;

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
        tooltip.setAttribute('role', 'tooltip');
        document.body.appendChild(tooltip);
        return tooltip;
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
        var tip = ensureTooltip();
        tip.textContent = text;
        tip.classList.add('tooltip-visible');
        current = anchor;
        position(anchor);
    }

    /**
     * Hide the tooltip.
     * @returns {void}
     */
    function hide() {
        if (!tooltip) {
            return;
        }
        tooltip.classList.remove('tooltip-visible');
        current = null;
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
        if (anchor && anchor !== current) {
            show(anchor);
        }
    });

    document.addEventListener('mouseout', function(e) {
        if (!current) {
            return;
        }
        var from = anchorFor(e.target);
        var to = anchorFor(e.relatedTarget);
        if (from === current && to !== current) {
            hide();
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
        if (current === anchor) {
            hide();
        } else {
            show(anchor);
        }
    });

    window.addEventListener('scroll', hide, true);
    window.addEventListener('resize', hide);
})();
