/**
 * Absence form UI handling for time fields and recurrence.
 * @module absence-form
 */

(function() {
    'use strict';

    var form;
    var categorySelect;
    var substituteSelect;
    var startDateInput;
    var endDateInput;
    var timeTypeInputs;
    var startTimeInput;
    var endTimeInput;

    var isRecurringCheckbox;
    var recurrenceFieldsContainer;
    var recurrenceFrequency;
    var weekdayFields;
    var recurrenceEndDate;
    var recurrencePreview;
    var endDateGroup;

    /**
     * Initialize the absence form module.
     */
    function init() {
        form = document.getElementById('absence-form');
        if (!form) return;

        categorySelect = form.querySelector('[name="category_id"]');
        substituteSelect = form.querySelector('[name="substitute_id"]');
        startDateInput = form.querySelector('[name="start_date"]');
        endDateInput = form.querySelector('[name="end_date"]');
        timeTypeInputs = form.querySelectorAll('[name="time_type"]');
        startTimeInput = form.querySelector('[name="start_time"]');
        endTimeInput = form.querySelector('[name="end_time"]');

        isRecurringCheckbox = document.getElementById('is_recurring');
        recurrenceFieldsContainer = document.getElementById('recurrence-fields');
        recurrenceFrequency = document.getElementById('recurrence_frequency');
        weekdayFields = document.getElementById('weekday-fields');
        recurrenceEndDate = document.getElementById('recurrence_end_date');
        recurrencePreview = document.getElementById('recurrence-preview');
        endDateGroup = document.getElementById('end-date-group');

        initEventListeners();
        updateTimeFields();
        updateSubstituteRequirement();
        initRecurrenceFields();
    }

    /**
     * Set up event listeners for form fields.
     */
    function initEventListeners() {
        if (categorySelect) {
            categorySelect.addEventListener('change', updateSubstituteRequirement);
        }

        if (startDateInput) {
            startDateInput.addEventListener('change', syncEndDate);
        }

        timeTypeInputs.forEach(function(input) {
            input.addEventListener('change', updateTimeFields);
        });
    }

    /**
     * Update time input field visibility based on time type selection.
     */
    function updateTimeFields() {
        var selectedType = getSelectedTimeType();
        var timeFieldsContainer = document.getElementById('time-fields');

        if (!timeFieldsContainer) return;

        if (selectedType === 'custom_time') {
            timeFieldsContainer.classList.remove('hidden');
            if (startTimeInput) startTimeInput.required = true;
            if (endTimeInput) endTimeInput.required = true;
        } else {
            timeFieldsContainer.classList.add('hidden');
            if (startTimeInput) {
                startTimeInput.required = false;
                startTimeInput.value = '';
            }
            if (endTimeInput) {
                endTimeInput.required = false;
                endTimeInput.value = '';
            }
        }
    }

    /**
     * Get the currently selected time type.
     * @returns {string} Selected time type value.
     */
    function getSelectedTimeType() {
        var selected = form.querySelector('[name="time_type"]:checked');
        return selected ? selected.value : 'all_day';
    }

    /**
     * Sync end date to start date if end date is empty or before start date.
     */
    function syncEndDate() {
        if (!startDateInput || !endDateInput) return;

        var startValue = startDateInput.value;
        var endValue = endDateInput.value;

        if (startValue && (!endValue || endValue < startValue)) {
            endDateInput.value = startValue;
        }
    }

    /**
     * Update substitute field requirement based on category.
     * Uses DOM manipulation instead of innerHTML for CSP compliance.
     */
    function updateSubstituteRequirement() {
        if (!categorySelect || !substituteSelect) return;

        var selectedOption = categorySelect.options[categorySelect.selectedIndex];
        var requiresSubstitute = selectedOption && selectedOption.dataset.requiresSubstitute === 'true';
        var substituteLabel = substituteSelect.closest('label');

        if (!substituteLabel) return;

        substituteSelect.required = requiresSubstitute;

        var existingRequired = substituteLabel.querySelector('.required');

        if (requiresSubstitute && !existingRequired) {
            var requiredSpan = document.createElement('span');
            requiredSpan.className = 'required';
            requiredSpan.textContent = ' *';
            substituteLabel.insertBefore(requiredSpan, substituteSelect);
        } else if (!requiresSubstitute && existingRequired) {
            existingRequired.remove();
        }
    }

    /**
     * Check if a date string is within valid range.
     * Uses global utility function from main.js.
     * @param {string} dateStr - Date string in YYYY-MM-DD format.
     * @returns {boolean} True if date is valid and within range.
     */
    function isDateInValidRange(dateStr) {
        return window.FBKTime.isDateInValidRange(dateStr);
    }

    /**
     * Initialize recurrence field event listeners and state.
     */
    function initRecurrenceFields() {
        if (!isRecurringCheckbox) return;

        isRecurringCheckbox.addEventListener('change', function() {
            toggleRecurrenceFields();
            updateRecurrencePreview();
        });

        if (recurrenceFrequency) {
            recurrenceFrequency.addEventListener('change', function() {
                toggleWeekdayFields();
                updateRecurrencePreview();
            });
        }

        var weekdayCheckboxes = form.querySelectorAll('[name="recurrence_weekdays"]');
        weekdayCheckboxes.forEach(function(checkbox) {
            checkbox.addEventListener('change', updateRecurrencePreview);
        });

        if (startDateInput) {
            startDateInput.addEventListener('change', function() {
                updateMaxRecurrenceEndDate();
                updateRecurrencePreview();
            });
        }

        if (recurrenceEndDate) {
            recurrenceEndDate.addEventListener('change', updateRecurrencePreview);
        }

        toggleRecurrenceFields();
        toggleWeekdayFields();
        updateMaxRecurrenceEndDate();
        updateRecurrencePreview();
    }

    /**
     * Toggle recurrence fields container visibility.
     * Clears recurrence end date constraints when hidden to prevent browser validation issues.
     */
    function toggleRecurrenceFields() {
        if (!recurrenceFieldsContainer) return;

        if (isRecurringCheckbox && isRecurringCheckbox.checked) {
            recurrenceFieldsContainer.classList.remove('hidden');
            if (endDateGroup) {
                endDateGroup.classList.add('hidden');
            }
            updateMaxRecurrenceEndDate();
        } else {
            recurrenceFieldsContainer.classList.add('hidden');
            if (endDateGroup) {
                endDateGroup.classList.remove('hidden');
            }
            clearRecurrenceEndDateConstraints();
        }
    }

    /**
     * Toggle weekday fields based on frequency selection.
     */
    function toggleWeekdayFields() {
        if (!weekdayFields || !recurrenceFrequency) return;

        var frequency = recurrenceFrequency.value;
        if (frequency === 'daily') {
            weekdayFields.classList.add('hidden');
        } else {
            weekdayFields.classList.remove('hidden');
        }
    }

    /**
     * Clear recurrence end date constraints to prevent browser validation on hidden field.
     */
    function clearRecurrenceEndDateConstraints() {
        if (!recurrenceEndDate) return;

        recurrenceEndDate.removeAttribute('min');
        recurrenceEndDate.removeAttribute('max');
        recurrenceEndDate.removeAttribute('required');
        recurrenceEndDate.disabled = true;
        recurrenceEndDate.value = '';
    }

    /**
     * Update the maximum allowed recurrence end date (1 year from start).
     * Only sets constraints if recurrence is active and start date is within valid range.
     */
    function updateMaxRecurrenceEndDate() {
        if (!recurrenceEndDate || !startDateInput) return;

        if (!isRecurringCheckbox || !isRecurringCheckbox.checked) {
            return;
        }

        var startDate = startDateInput.value;
        if (!startDate) {
            clearRecurrenceEndDateConstraints();
            return;
        }

        if (!isDateInValidRange(startDate)) {
            clearRecurrenceEndDateConstraints();
            return;
        }

        recurrenceEndDate.disabled = false;
        recurrenceEndDate.min = startDate;

        if (!recurrenceEndDate.value) {
            var defaultEnd = new Date(startDate);
            defaultEnd.setMonth(defaultEnd.getMonth() + 3);
            recurrenceEndDate.value = defaultEnd.toISOString().split('T')[0];
        }
    }

    /**
     * Update the recurrence preview text.
     */
    function updateRecurrencePreview() {
        if (!recurrencePreview) return;

        if (!isRecurringCheckbox || !isRecurringCheckbox.checked) {
            recurrencePreview.textContent = '';
            return;
        }

        var frequency = recurrenceFrequency ? recurrenceFrequency.value : 'weekly';
        var selectedWeekdays = getSelectedWeekdays();
        var endDate = recurrenceEndDate ? recurrenceEndDate.value : '';

        var previewText = buildPreviewText(frequency, selectedWeekdays, endDate);
        recurrencePreview.textContent = previewText;
    }

    /**
     * Get selected weekday codes.
     * @returns {Array} Array of selected weekday codes.
     */
    function getSelectedWeekdays() {
        var checkboxes = form.querySelectorAll('[name="recurrence_weekdays"]:checked');
        return Array.prototype.map.call(checkboxes, function(cb) {
            return cb.value;
        });
    }

    /**
     * Build human-readable preview text.
     * @param {string} frequency - Frequency type.
     * @param {Array} weekdays - Selected weekday codes.
     * @param {string} endDate - End date string (YYYY-MM-DD).
     * @returns {string} Preview text.
     */
    function buildPreviewText(frequency, weekdays, endDate) {
        var weekdayNames = {
            'MO': 'Montag', 'TU': 'Dienstag', 'WE': 'Mittwoch',
            'TH': 'Donnerstag', 'FR': 'Freitag', 'SA': 'Samstag', 'SU': 'Sonntag'
        };

        var desc = '';

        if (frequency === 'daily') {
            desc = 'Täglich';
        } else if (frequency === 'biweekly') {
            if (weekdays.length > 0) {
                var dayNames = weekdays.map(function(d) { return weekdayNames[d]; });
                desc = 'Alle 2 Wochen am ' + dayNames.join(' und ');
            } else {
                desc = 'Alle 2 Wochen';
            }
        } else {
            if (weekdays.length > 0) {
                var dayNames = weekdays.map(function(d) { return weekdayNames[d]; });
                if (dayNames.length === 1) {
                    desc = 'Jeden ' + dayNames[0];
                } else {
                    var last = dayNames.pop();
                    desc = 'Jeden ' + dayNames.join(', ') + ' und ' + last;
                }
            } else {
                desc = 'Wöchentlich';
            }
        }

        if (endDate) {
            var dateParts = endDate.split('-');
            var formattedDate = dateParts[2] + '.' + dateParts[1] + '.' + dateParts[0];
            desc += ' bis ' + formattedDate;
        }

        return desc;
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
