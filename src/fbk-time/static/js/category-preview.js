/**
 * Updates live preview when editing category properties.
 * @module category-preview
 */
(function() {
    'use strict';

    // WCAG 2.2 (1.4.3) minimum for regular-size text.
    var MIN_CONTRAST = 4.5;
    var HEX_COLOR_PATTERN = /^#[0-9A-Fa-f]{6}$/;

    /**
     * Relative luminance of a colour per WCAG 2.2.
     * @param {string} hex - Colour as #rrggbb.
     * @returns {number} Luminance between 0 and 1.
     */
    function luminance(hex) {
        var channels = [1, 3, 5].map(function(offset) {
            var value = parseInt(hex.substr(offset, 2), 16) / 255;
            return value <= 0.03928
                ? value / 12.92
                : Math.pow((value + 0.055) / 1.055, 2.4);
        });
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
    }

    /**
     * Contrast ratio between two colours.
     * @param {string} foreground - Text colour as #rrggbb.
     * @param {string} background - Background colour as #rrggbb.
     * @returns {number} Ratio between 1 and 21.
     */
    function contrastRatio(foreground, background) {
        var first = luminance(foreground);
        var second = luminance(background);
        return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
    }

    /**
     * Initialize category preview functionality.
     * @returns {void}
     */
    function init() {
        var colorInput = document.getElementById('color');
        var textColorInput = document.getElementById('text_color');
        var nameInput = document.getElementById('name');
        var iconInput = document.getElementById('icon');
        var preview = document.getElementById('category-preview');
        var previewIcon = document.getElementById('preview-icon');
        var previewName = document.getElementById('preview-name');
        var contrastHint = document.getElementById('contrast-hint');
        var contrastHintText = document.getElementById('contrast-hint-text');

        if (!preview) return;

        /**
         * Find the picker grid button for an emoji value.
         * @param {string} icon - The emoji value.
         * @returns {HTMLElement|null} Matching button or null.
         */
        function findGridButton(icon) {
            var buttons = document.querySelectorAll('.emoji-btn');
            for (var i = 0; i < buttons.length; i++) {
                if (buttons[i].dataset.emoji === icon) return buttons[i];
            }
            return null;
        }

        /**
         * Show the contrast ratio while it stays below the readable minimum.
         * @param {string} textColor - Text colour as #rrggbb.
         * @param {string} backgroundColor - Background colour as #rrggbb.
         * @returns {void}
         */
        function updateContrastHint(textColor, backgroundColor) {
            if (!contrastHint || !contrastHintText) return;

            if (!HEX_COLOR_PATTERN.test(textColor) || !HEX_COLOR_PATTERN.test(backgroundColor)) {
                contrastHint.classList.add('hidden');
                return;
            }

            var ratio = contrastRatio(textColor, backgroundColor);
            var sufficient = ratio >= MIN_CONTRAST;

            contrastHint.classList.toggle('hidden', sufficient);
            if (!sufficient) {
                // Rounded down so a near miss is never reported as a hit.
                var message = 'Kontrast '
                    + (Math.floor(ratio * 10) / 10).toFixed(1).replace('.', ',')
                    + ':1 – empfohlen sind mindestens 4,5:1, damit die Schrift gut lesbar bleibt.';
                // Dragging a colour picker fires continuously; rewriting an
                // unchanged message would announce it on every step.
                if (contrastHintText.textContent !== message) {
                    contrastHintText.textContent = message;
                }
            }
        }

        /**
         * Update preview badge with current input values.
         * @returns {void}
         */
        function updatePreview() {
            var bgColor = colorInput.value || '#2563EB';
            var txtColor = textColorInput.value || '#FFFFFF';
            var name = nameInput.value || 'Kategorie';
            var icon = iconInput.value || '';

            preview.style.setProperty('--preview-bg', bgColor);
            preview.style.setProperty('--preview-color', txtColor);
            previewIcon.textContent = '';
            if (icon) {
                var gridBtn = findGridButton(icon);
                var iconSpan = gridBtn ? gridBtn.querySelector('.category-icon') : null;
                if (iconSpan) {
                    previewIcon.appendChild(iconSpan.cloneNode(true));
                    previewIcon.appendChild(document.createTextNode(' '));
                }
            }
            previewName.textContent = name;
            updateContrastHint(txtColor, bgColor);
        }

        colorInput.addEventListener('input', updatePreview);
        textColorInput.addEventListener('input', updatePreview);
        nameInput.addEventListener('input', updatePreview);
        iconInput.addEventListener('change', updatePreview);

        updatePreview();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
