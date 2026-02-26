/**
 * Emoji picker for category icons.
 * @module emoji-picker
 */

(function() {
    'use strict';

    /**
     * Curated emoji list for absence categories.
     * @type {string[]}
     */
    const EMOJIS = [
        '🏖️', '🌴', '✈️', '🧳', '🏝️', '☀️', '🌊', '⛱️',
        '🤒', '🏥', '💊', '🩺', '🤕', '😷', '🩹', '❤️‍🩹',
        '🏠', '💻', '🖥️', '📱', '🏢', '🏗️', '🛠️', '🔧',
        '📚', '🎓', '📖', '✏️', '🎯', '💡', '🧠', '📝',
        '👶', '👨‍👩‍👧', '🍼', '🧸', '👪', '❤️', '💑', '🏡',
        '🎉', '🎊', '🎁', '🎂', '💒', '⛪', '🎄', '🎃',
        '⚽', '🏃', '🚴', '🏋️', '🧘', '🏊', '⛷️', '🎿',
        '🚗', '🚕', '🚌', '🚂', '✈️', '🚀', '⏰', '📅'
    ];

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

        EMOJIS.forEach(function(emoji) {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'emoji-btn';
            btn.textContent = emoji;
            btn.addEventListener('click', function() {
                selectEmoji(input, emoji, picker);
            });
            grid.appendChild(btn);
        });

        pickerBtn.addEventListener('click', function(e) {
            e.preventDefault();
            togglePicker(picker);
        });

        input.addEventListener('click', function(e) {
            e.preventDefault();
            togglePicker(picker);
        });

        if (clearBtn) {
            clearBtn.addEventListener('click', function(e) {
                e.preventDefault();
                input.value = '';
                input.dispatchEvent(new Event('change', { bubbles: true }));
                hidePicker(picker);
            });
        }

        document.addEventListener('click', function(e) {
            if (!wrapper.contains(e.target)) {
                hidePicker(picker);
            }
        });
    }

    /**
     * Toggle picker visibility.
     * @param {HTMLElement} picker - The picker element.
     */
    function togglePicker(picker) {
        picker.classList.toggle('hidden');
    }

    /**
     * Hide picker.
     * @param {HTMLElement} picker - The picker element.
     */
    function hidePicker(picker) {
        picker.classList.add('hidden');
    }

    /**
     * Select an emoji and notify listeners.
     * @param {HTMLInputElement} input - The input field.
     * @param {string} emoji - The selected emoji.
     * @param {HTMLElement} picker - The picker element.
     */
    function selectEmoji(input, emoji, picker) {
        input.value = emoji;
        input.dispatchEvent(new Event('change', { bubbles: true }));
        hidePicker(picker);
    }

    // Initialize on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initEmojiPickers);
    } else {
        initEmojiPickers();
    }
})();
