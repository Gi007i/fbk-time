/**
 * Handles dynamic form behavior for user creation.
 * @module user-form
 */
(function() {
    'use strict';

    /**
     * Initialize user form functionality.
     * @returns {void}
     */
    function init() {
        var roleSelect = document.getElementById('role');
        var passwordField = document.getElementById('password-field');

        if (!roleSelect || !passwordField) return;

        /**
         * Toggle password field visibility based on role selection.
         * In single-user mode, USER role creates MANAGED users (no password).
         * @returns {void}
         */
        function togglePasswordField() {
            if (roleSelect.value === 'user') {
                passwordField.classList.add('hidden');
            } else {
                passwordField.classList.remove('hidden');
            }
        }

        roleSelect.addEventListener('change', togglePasswordField);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
