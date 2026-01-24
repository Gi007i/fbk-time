/**
 * Updates live preview when editing category properties.
 * @module category-preview
 */
(function() {
    'use strict';

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

        if (!preview) return;

        /**
         * Update preview badge with current input values.
         * @returns {void}
         */
        function updatePreview() {
            var bgColor = colorInput.value || '#3B82F6';
            var txtColor = textColorInput.value || '#FFFFFF';
            var name = nameInput.value || 'Kategorie';
            var icon = iconInput.value || '';

            preview.style.setProperty('--preview-bg', bgColor);
            preview.style.setProperty('--preview-color', txtColor);
            previewIcon.textContent = icon ? icon + ' ' : '';
            previewName.textContent = name;
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
