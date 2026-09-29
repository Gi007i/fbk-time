"""Flask extensions.

Centralizes extension initialization to avoid circular imports.
"""

from flask_wtf.csrf import CSRFProtect


csrf = CSRFProtect()
