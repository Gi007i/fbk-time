"""License display services.

Provides wrapper functions for core license functionality.
"""

from core.licenses import load_licenses as _load_licenses
from core.licenses import get_license_summary as _get_license_summary


def get_all_licenses() -> list[dict]:
    """Get all open source licenses used by the application.

    Returns:
        List of license dicts with name, version, license type, etc.
    """
    return _load_licenses()


def get_license_stats() -> dict:
    """Get summary statistics about licenses.

    Returns:
        Dict with license type counts and totals.
    """
    return _get_license_summary()
