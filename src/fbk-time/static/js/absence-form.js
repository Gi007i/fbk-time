/**
 * Absence form handling, validation, and live conflict checking.
 * @module absence-form
 */

(function() {
    'use strict';

    var form;
    var userSelect;
    var categorySelect;
    var substituteSelect;
    var startDateInput;
    var endDateInput;
    var timeTypeInputs;
    var startTimeInput;
    var endTimeInput;
    var conflictDisplay;
    var checkTimeout;

    // Recurrence fields
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

        userSelect = form.querySelector('[name="user_id"]');
        categorySelect = form.querySelector('[name="category_id"]');
        substituteSelect = form.querySelector('[name="substitute_id"]');
        startDateInput = form.querySelector('[name="start_date"]');
        endDateInput = form.querySelector('[name="end_date"]');
        timeTypeInputs = form.querySelectorAll('[name="time_type"]');
        startTimeInput = form.querySelector('[name="start_time"]');
        endTimeInput = form.querySelector('[name="end_time"]');
        conflictDisplay = document.getElementById('conflict-warnings');

        // Recurrence fields
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
        if (userSelect) {
            userSelect.addEventListener('change', function() {
                updateAvailableSubstitutes();
                scheduleConflictCheck();
            });
        }

        if (categorySelect) {
            categorySelect.addEventListener('change', function() {
                updateSubstituteRequirement();
                scheduleConflictCheck();
            });
        }

        if (substituteSelect) {
            substituteSelect.addEventListener('change', scheduleConflictCheck);
        }

        if (startDateInput) {
            startDateInput.addEventListener('change', function() {
                syncEndDate();
                updateAvailableSubstitutes();
                scheduleConflictCheck();
            });
        }

        if (endDateInput) {
            endDateInput.addEventListener('change', function() {
                updateAvailableSubstitutes();
                scheduleConflictCheck();
            });
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
     */
    function updateSubstituteRequirement() {
        if (!categorySelect || !substituteSelect) return;

        var selectedOption = categorySelect.options[categorySelect.selectedIndex];
        var requiresSubstitute = selectedOption && selectedOption.dataset.requiresSubstitute === 'true';
        var substituteLabel = document.querySelector('label[for="substitute_id"]');

        if (requiresSubstitute) {
            substituteSelect.required = true;
            if (substituteLabel) {
                substituteLabel.innerHTML = 'Vertretung <span class="required">*</span>';
            }
        } else {
            substituteSelect.required = false;
            if (substituteLabel) {
                substituteLabel.innerHTML = 'Vertretung';
            }
        }
    }

    /**
     * Update available substitutes based on date range.
     */
    function updateAvailableSubstitutes() {
        if (!userSelect || !substituteSelect || !startDateInput || !endDateInput) return;

        var userId = userSelect.value;
        var startDate = startDateInput.value;
        var endDate = endDateInput.value;

        if (!userId || !startDate || !endDate) return;

        var url = '/absences/api/available-substitutes?' +
            'user_id=' + encodeURIComponent(userId) +
            '&start_date=' + encodeURIComponent(startDate) +
            '&end_date=' + encodeURIComponent(endDate);

        fetch(url, {
            headers: {
                'X-Requested-With': 'XMLHttpRequest'
            }
        })
            .then(function(response) {
                return response.json();
            })
            .then(function(data) {
                if (data.substitutes) {
                    updateSubstituteOptions(data.substitutes);
                }
            })
            .catch(function() {
                showWarning('Vertretungsliste konnte nicht aktualisiert werden. Bitte manuelle Überprüfung durchführen.');
            });
    }

    /**
     * Update substitute select options.
     * @param {Array} substitutes - Array of available substitute objects.
     */
    function updateSubstituteOptions(substitutes) {
        if (!substituteSelect) return;

        var currentValue = substituteSelect.value;

        // Clear all options except the first (empty) one
        while (substituteSelect.options.length > 1) {
            substituteSelect.remove(1);
        }

        substitutes.forEach(function(sub) {
            var option = document.createElement('option');
            option.value = sub.id;
            option.textContent = sub.name;
            substituteSelect.appendChild(option);
        });

        // Try to restore previous selection
        if (currentValue) {
            substituteSelect.value = currentValue;
            if (substituteSelect.value !== currentValue) {
                // Previous selection is no longer available
                substituteSelect.value = '';
                showWarning('Die zuvor gewählte Vertretung ist im neuen Zeitraum nicht verfügbar.');
            }
        }
    }

    /**
     * Schedule a conflict check with debouncing.
     */
    function scheduleConflictCheck() {
        if (checkTimeout) {
            clearTimeout(checkTimeout);
        }
        checkTimeout = setTimeout(checkConflicts, 300);
    }

    /**
     * Check for conflicts via API.
     */
    function checkConflicts() {
        if (!userSelect || !startDateInput || !endDateInput) return;

        var userId = userSelect.value;
        var startDate = startDateInput.value;
        var endDate = endDateInput.value;

        if (!userId || !startDate || !endDate) {
            clearConflictDisplay();
            return;
        }

        var data = {
            user_id: userId,
            start_date: startDate,
            end_date: endDate
        };

        if (substituteSelect && substituteSelect.value) {
            data.substitute_id = substituteSelect.value;
        }

        // Check if we're editing (exclude_id from form)
        var excludeInput = form.querySelector('[name="exclude_id"]');
        if (excludeInput && excludeInput.value) {
            data.exclude_id = excludeInput.value;
        }

        var csrfToken = document.querySelector('meta[name="csrf-token"]');

        fetch('/absences/api/check-conflicts', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-Requested-With': 'XMLHttpRequest',
                'X-CSRFToken': csrfToken ? csrfToken.content : ''
            },
            body: JSON.stringify(data)
        })
        .then(function(response) {
            if (!response.ok) {
                throw new Error('Request failed');
            }
            return response.json();
        })
        .then(function(result) {
            displayConflicts(result);
        })
        .catch(function() {
            showWarning('Konfliktprüfung fehlgeschlagen. Bitte manuelle Überprüfung vor dem Speichern durchführen.');
        });
    }

    /**
     * Display conflict warnings in the UI.
     * @param {Object} result - Conflict check result.
     */
    function displayConflicts(result) {
        if (!conflictDisplay) return;

        var hasMessages = result.messages && result.messages.length > 0;

        if (!result.has_conflicts && !result.cross_substitution_warning && !hasMessages) {
            clearConflictDisplay();
            return;
        }

        var html = '';

        if (hasMessages) {
            result.messages.forEach(function(message) {
                // Conflicts are blocking (danger), warnings are informational
                var isWarning = !result.has_conflicts ||
                    message.indexOf('Kreuzvertretung') !== -1 ||
                    message.indexOf('vertritt bereits') !== -1;
                var className = isWarning ? 'alert-warning' : 'alert-danger';
                html += '<article role="alert" class="inline-alert ' + className + '">' + escapeHtml(message) + '</article>';
            });
        }

        conflictDisplay.innerHTML = html;
        conflictDisplay.classList.remove('hidden');
    }

    /**
     * Clear the conflict display.
     */
    function clearConflictDisplay() {
        if (conflictDisplay) {
            conflictDisplay.innerHTML = '';
            conflictDisplay.classList.add('hidden');
        }
    }

    /**
     * Show a warning message.
     * @param {string} message - Warning message to display.
     */
    function showWarning(message) {
        if (conflictDisplay) {
            var html = '<article role="alert" class="inline-alert alert-warning">' + escapeHtml(message) + '</article>';
            conflictDisplay.innerHTML += html;
            conflictDisplay.classList.remove('hidden');
        }
    }

    /**
     * Escape HTML special characters.
     * Uses global utility function from main.js.
     * @param {string} text - Text to escape.
     * @returns {string} Escaped text.
     */
    function escapeHtml(text) {
        return window.FBKTime.escapeHtml(text);
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

        // Start date change updates max recurrence end date
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
     */
    function toggleRecurrenceFields() {
        if (!recurrenceFieldsContainer) return;

        if (isRecurringCheckbox && isRecurringCheckbox.checked) {
            recurrenceFieldsContainer.classList.remove('hidden');
            // Hide end date for recurring (uses recurrence_end_date instead)
            if (endDateGroup) {
                endDateGroup.classList.add('hidden');
            }
        } else {
            recurrenceFieldsContainer.classList.add('hidden');
            if (endDateGroup) {
                endDateGroup.classList.remove('hidden');
            }
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
     * Update the maximum allowed recurrence end date (1 year from start).
     */
    function updateMaxRecurrenceEndDate() {
        if (!recurrenceEndDate || !startDateInput) return;

        var startDate = startDateInput.value;
        if (!startDate) return;

        var start = new Date(startDate);
        var maxEnd = new Date(start);
        maxEnd.setFullYear(maxEnd.getFullYear() + 1);

        // Format as YYYY-MM-DD
        var maxDateStr = maxEnd.toISOString().split('T')[0];
        recurrenceEndDate.max = maxDateStr;
        recurrenceEndDate.min = startDate;

        // If no end date set, default to 3 months from start
        if (!recurrenceEndDate.value) {
            var defaultEnd = new Date(start);
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
            // weekly
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

    /**
     * Reset submit button state after validation failure.
     */
    function resetSubmitButton() {
        var submitButton = form.querySelector('button[type="submit"]');
        if (submitButton) {
            submitButton.disabled = false;
            submitButton.removeAttribute('aria-busy');
        }
    }

    /**
     * Validate the form before submission.
     * @param {Event} event - Form submit event.
     * @returns {boolean} Whether form is valid.
     */
    function validateForm(event) {
        var isValid = true;

        // Clear previous validation errors
        window.FBKTime.clearAllFieldErrors(form);

        // Check date range (only for non-recurring)
        var isRecurring = isRecurringCheckbox && isRecurringCheckbox.checked;
        if (!isRecurring && startDateInput && endDateInput) {
            if (endDateInput.value < startDateInput.value) {
                window.FBKTime.showFieldError(endDateInput, 'Das Enddatum darf nicht vor dem Startdatum liegen.');
                event.preventDefault();
                isValid = false;
            }
        }

        var timeType = getSelectedTimeType();
        if (timeType === 'custom_time') {
            if (!startTimeInput.value || !endTimeInput.value) {
                window.FBKTime.showFieldError(startTimeInput, 'Bitte geben Sie Start- und Endzeit an.');
                event.preventDefault();
                isValid = false;
            } else if (startTimeInput.value >= endTimeInput.value) {
                window.FBKTime.showFieldError(endTimeInput, 'Die Endzeit muss nach der Startzeit liegen.');
                event.preventDefault();
                isValid = false;
            }
        }

        if (isRecurring) {
            var frequency = recurrenceFrequency ? recurrenceFrequency.value : 'weekly';

            // Weekday required for weekly/biweekly
            if (frequency !== 'daily') {
                var selectedWeekdays = getSelectedWeekdays();
                if (selectedWeekdays.length === 0) {
                    window.FBKTime.showFieldsetError(weekdayFields, 'Bitte wählen Sie mindestens einen Wochentag aus.');
                    event.preventDefault();
                    isValid = false;
                }
            }

            if (recurrenceEndDate && !recurrenceEndDate.value) {
                window.FBKTime.showFieldError(recurrenceEndDate, 'Bitte geben Sie ein Enddatum für die Serie an.');
                event.preventDefault();
                isValid = false;
            }
        }

        if (!isValid) {
            resetSubmitButton();
        }

        return isValid;
    }

    // Initialize on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    document.addEventListener('DOMContentLoaded', function() {
        var absenceForm = document.getElementById('absence-form');
        if (absenceForm) {
            absenceForm.addEventListener('submit', validateForm);
        }
    });
})();
