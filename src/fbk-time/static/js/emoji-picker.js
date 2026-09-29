/**
 * Emoji picker for category icons.
 * @module emoji-picker
 */

(function() {
    'use strict';

    /**
     * Initialize all emoji pickers on the page.
     */
    function initEmojiPickers() {
        const wrappers = document.querySelectorAll('.emoji-picker-wrapper');
        wrappers.forEach(initPicker);
    }

    /**
     * Initialize a single emoji picker.
     * @param {HTMLElement} wrapper - The picker wrapper element.
     */
    function initPicker(wrapper) {
        const input = wrapper.querySelector('.emoji-input');
        const pickerBtn = wrapper.querySelector('.emoji-picker-btn');
        const clearBtn = wrapper.querySelector('.emoji-clear-btn');
        const picker = wrapper.querySelector('.emoji-picker');
        const grid = wrapper.querySelector('.emoji-grid');

        if (!input || !pickerBtn || !picker || !grid) return;

        grid.addEventListener('click', function(e) {
            const btn = e.target.closest('.emoji-btn');
            if (btn && btn.dataset.emoji) {
                selectEmoji(input, btn.dataset.emoji, picker, pickerBtn);
            }
        });

        pickerBtn.addEventListener('click', function(e) {
            e.preventDefault();
            togglePicker(picker, pickerBtn, grid);
        });

        input.addEventListener('click', function(e) {
            e.preventDefault();
            togglePicker(picker, pickerBtn, grid);
        });

        input.addEventListener('keydown', function(e) {
            if (e.key !== 'Enter' && e.key !== ' ') return;
            // The field is read-only and acts as an opener; without this it
            // would submit the surrounding form instead.
            e.preventDefault();
            togglePicker(picker, pickerBtn, grid);
        });

        setupRovingFocus(grid);

        if (clearBtn) {
            clearBtn.addEventListener('click', function(e) {
                e.preventDefault();
                input.value = '';
                input.dispatchEvent(new Event('change', { bubbles: true }));
                hidePicker(picker, pickerBtn);
            });
        }

        wrapper.addEventListener('keydown', function(e) {
            if (e.key === 'Escape' && !picker.classList.contains('hidden')) {
                hidePicker(picker, pickerBtn);
                pickerBtn.focus();
            }
        });

        document.addEventListener('click', function(e) {
            if (!wrapper.contains(e.target)) {
                hidePicker(picker, pickerBtn);
            }
        });
    }

    /**
     * Number of buttons in the first grid row, derived from the rendered
     * layout because the column count adapts to the available width.
     * @param {Array<HTMLElement>} buttons - All grid buttons in order.
     * @returns {number} Buttons per row.
     */
    function columnCount(buttons) {
        const top = buttons[0].offsetTop;
        for (let i = 1; i < buttons.length; i++) {
            if (buttons[i].offsetTop !== top) return i;
        }
        return buttons.length;
    }

    /**
     * Give the grid a single tab stop and arrow-key navigation, so leaving an
     * opened picker does not require stepping through every symbol.
     * @param {HTMLElement} grid - The grid element.
     */
    function setupRovingFocus(grid) {
        const buttons = Array.prototype.slice.call(grid.querySelectorAll('.emoji-btn'));
        if (!buttons.length) return;

        buttons.forEach(function(btn, i) {
            btn.setAttribute('tabindex', i === 0 ? '0' : '-1');
        });

        grid.addEventListener('keydown', function(e) {
            const index = buttons.indexOf(document.activeElement);
            if (index === -1) return;

            const cols = columnCount(buttons);
            let next;
            switch (e.key) {
                case 'ArrowRight': next = index + 1; break;
                case 'ArrowLeft': next = index - 1; break;
                case 'ArrowDown': next = index + cols; break;
                case 'ArrowUp': next = index - cols; break;
                case 'Home': next = 0; break;
                case 'End': next = buttons.length - 1; break;
                default: return;
            }
            if (next < 0 || next >= buttons.length) return;

            e.preventDefault();
            buttons[index].setAttribute('tabindex', '-1');
            buttons[next].setAttribute('tabindex', '0');
            buttons[next].focus();
        });
    }

    /**
     * Toggle picker visibility.
     * @param {HTMLElement} picker - The picker element.
     * @param {HTMLButtonElement} pickerBtn - The button that owns the picker.
     * @param {HTMLElement} grid - The grid element.
     */
    function togglePicker(picker, pickerBtn, grid) {
        const open = picker.classList.toggle('hidden') === false;
        pickerBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (open && grid) {
            const active = grid.querySelector('.emoji-btn[tabindex="0"]');
            if (active) active.focus();
        }
    }

    /**
     * Hide picker.
     * @param {HTMLElement} picker - The picker element.
     * @param {HTMLButtonElement} pickerBtn - The button that owns the picker.
     */
    function hidePicker(picker, pickerBtn) {
        // The focused grid button is about to be hidden; without handing
        // focus back it would fall to the document.
        if (picker.contains(document.activeElement)) {
            pickerBtn.focus();
        }
        picker.classList.add('hidden');
        pickerBtn.setAttribute('aria-expanded', 'false');
    }

    /**
     * Select an emoji and notify listeners.
     * @param {HTMLInputElement} input - The input field.
     * @param {string} emoji - The selected emoji.
     * @param {HTMLElement} picker - The picker element.
     * @param {HTMLButtonElement} pickerBtn - The button that owns the picker.
     */
    function selectEmoji(input, emoji, picker, pickerBtn) {
        input.value = emoji;
        input.dispatchEvent(new Event('change', { bubbles: true }));
        hidePicker(picker, pickerBtn);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initEmojiPickers);
    } else {
        initEmojiPickers();
    }
})();
