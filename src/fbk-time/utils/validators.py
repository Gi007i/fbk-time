"""Password validation utilities.

Provides configurable password strength validation based on database settings.
"""

import re
from typing import Optional

from core.settings_manager import settings_manager


class PasswordValidator:
    """Validates password strength against configured policy.

    Reads policy settings from settings_manager. All requirements must be met
    for a password to be considered valid.
    """

    UPPERCASE_PATTERN = re.compile(r'[A-Z]')
    LOWERCASE_PATTERN = re.compile(r'[a-z]')
    NUMBER_PATTERN = re.compile(r'[0-9]')
    SYMBOL_PATTERN = re.compile(r'[!@#$%^&*()_+\-=\[\]{}|;:\'",.<>?/\\`~]')

    def _get_policy(self):
        """Get current password policy from settings.

        Returns:
            Dict with policy settings.
        """
        return {
            'min_length': settings_manager.get('password_min_length'),
            'max_length': settings_manager.get('password_max_length'),
            'require_uppercase': settings_manager.get('password_require_uppercase'),
            'require_lowercase': settings_manager.get('password_require_lowercase'),
            'require_numbers': settings_manager.get('password_require_numbers'),
            'require_symbols': settings_manager.get('password_require_symbols'),
        }

    def validate(self, password: str) -> tuple[bool, Optional[str]]:
        """Validate password against configured policy.

        Args:
            password: Plain text password to validate.

        Returns:
            Tuple of (is_valid, error_message).
            error_message is None if valid, German error string if invalid.
        """
        if not password:
            return False, 'Passwort ist erforderlich.'

        policy = self._get_policy()

        if len(password) < policy['min_length']:
            return False, f'Passwort muss mindestens {policy["min_length"]} Zeichen lang sein.'

        if len(password) > policy['max_length']:
            return False, f'Passwort darf maximal {policy["max_length"]} Zeichen lang sein.'

        if policy['require_uppercase'] and not self.UPPERCASE_PATTERN.search(password):
            return False, 'Passwort muss mindestens einen Grossbuchstaben enthalten.'

        if policy['require_lowercase'] and not self.LOWERCASE_PATTERN.search(password):
            return False, 'Passwort muss mindestens einen Kleinbuchstaben enthalten.'

        if policy['require_numbers'] and not self.NUMBER_PATTERN.search(password):
            return False, 'Passwort muss mindestens eine Zahl enthalten.'

        if policy['require_symbols'] and not self.SYMBOL_PATTERN.search(password):
            return False, 'Passwort muss mindestens ein Sonderzeichen enthalten.'

        return True, None

    def get_policy_description(self) -> list[str]:
        """Get human-readable policy requirements.

        Returns:
            List of requirement strings in German.
        """
        policy = self._get_policy()
        requirements = [f'Mindestens {policy["min_length"]} Zeichen']

        if policy['require_uppercase']:
            requirements.append('Mindestens ein Grossbuchstabe (A-Z)')
        if policy['require_lowercase']:
            requirements.append('Mindestens ein Kleinbuchstabe (a-z)')
        if policy['require_numbers']:
            requirements.append('Mindestens eine Zahl (0-9)')
        if policy['require_symbols']:
            requirements.append('Mindestens ein Sonderzeichen (!@#$%...)')

        return requirements


password_validator = PasswordValidator()


def validate_password_strength(password: str) -> tuple[bool, Optional[str]]:
    """Convenience function for password validation.

    Args:
        password: Plain text password to validate.

    Returns:
        Tuple of (is_valid, error_message).
    """
    return password_validator.validate(password)


def get_password_policy_info() -> dict:
    """Get password policy information for templates.

    Returns:
        Dict with min_length, placeholder text, and requirements list.
    """
    min_length = settings_manager.get('password_min_length')
    return {
        'min_length': min_length,
        'placeholder': f'Mindestens {min_length} Zeichen',
        'requirements': password_validator.get_policy_description()
    }
