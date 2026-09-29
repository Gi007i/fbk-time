"""Session-based authentication with signed remember-me cookies.

Sessions are bound to a client fingerprint (network + user agent), so a
replayed session cookie is rejected. The remember-me cookie is a bearer token
by design, since client binding would end it on every IP change; its signed
payload carries the expiry. A signed device cookie (OWASP "device cookies")
exempts known browsers from the account's login delay and lockout.
"""

import hashlib
import hmac
import ipaddress
import time
from datetime import timedelta
from functools import wraps
from typing import Any, Callable, Optional

from flask import current_app, g, redirect, request, session, url_for
from werkzeug.local import LocalProxy


SESSION_USER_KEY = '_user_id'
SESSION_CLIENT_KEY = '_client_id'
SESSION_FRESH_KEY = '_fresh_at'
# Sensitive actions accept a password entry this recent (OWASP
# re-authentication, ASVS 3.7.1); short, so a hijacked or unattended session
# cannot use them for long.
FRESH_LOGIN_WINDOW = timedelta(minutes=15)
# __Host- prefix: accepted only over a Secure, host-only origin, which
# blocks cookie injection from subdomains or insecure siblings.
REMEMBER_COOKIE_NAME = '__Host-remember_token'
DEVICE_COOKIE_NAME = '__Host-device'
# Covers long absences such as holidays or parental leave between two
# logins; the cookie grants no access, it only waives the account's login
# delay and lockout.
DEVICE_COOKIE_DURATION = timedelta(days=180)
# Bounds the cookie size on a browser shared by several accounts.
DEVICE_COOKIE_MAX_ENTRIES = 5
_DEVICE_ENTRY_SEPARATOR = '_'

# Distinct HMAC purposes, so a token issued for one cookie can never
# validate as the other.
_REMEMBER_PURPOSE = 'remember'
_DEVICE_PURPOSE = 'device'

# Unix timestamps stay below 12 digits for millennia; the cap keeps int()
# on crafted cookies far from its digit limit, which would raise.
_MAX_EXPIRY_DIGITS = 12

_user_loader: Optional[Callable[[str], Any]] = None
_remember_restorer: Optional[Callable[[Any], bool]] = None


class AnonymousUser:
    """Placeholder user for unauthenticated requests."""

    is_authenticated = False


class UserMixin:
    """Authentication properties for user models.

    Models must provide ``get_id()`` returning the identity persisted in the
    session and remember cookie.
    """

    is_authenticated = True


def user_loader(func: Callable[[str], Any]) -> Callable[[str], Any]:
    """Register the callable that resolves a stored identity to a user.

    The callable receives the identity persisted in the session or
    remember cookie and returns a user instance or None.
    """
    global _user_loader
    _user_loader = func
    return func


def remember_restorer(func: Callable[[Any], bool]) -> Callable[[Any], bool]:
    """Register the callable invoked when a remember cookie restores a user.

    The callable receives the loaded user and returns True when the
    restored session may be established. It is responsible for
    re-initializing session metadata.
    """
    global _remember_restorer
    _remember_restorer = func
    return func


def client_network(ip_address: str, ipv4_prefix: int) -> str:
    """Return the network a client address is counted or bound under.

    An IPv6 client usually controls a whole /64, so it is always reduced to
    that; IPv4-mapped addresses count as their IPv4 address.

    Args:
        ip_address: Client IP address.
        ipv4_prefix: Prefix length applied to IPv4 addresses.

    Returns:
        Network in CIDR notation.
    """
    address = ipaddress.ip_address(ip_address)
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    prefix = 64 if address.version == 6 else ipv4_prefix
    return str(ipaddress.ip_network(f'{address}/{prefix}', strict=False))


# Binding to the /24 instead of the exact address (baseline §7) keeps
# sessions alive when a client hops between addresses of one network.
_FINGERPRINT_IPV4_PREFIX = 24


def _client_fingerprint() -> str:
    """Hash the client identity used to bind a session to its origin."""
    network = client_network(request.remote_addr, _FINGERPRINT_IPV4_PREFIX)
    raw = f'{network}|{request.user_agent.string}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _digests_match(given: str, expected: str) -> bool:
    # Compare as bytes: compare_digest raises TypeError on non-ASCII str,
    # so a crafted cookie would otherwise 500 on every request.
    return hmac.compare_digest(given.encode('utf-8'), expected.encode('utf-8'))


def _hmac(key: str, purpose: str, payload: str) -> str:
    return hmac.new(
        key.encode('utf-8'),
        f'{purpose}|{payload}'.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def _sign(purpose: str, payload: str) -> str:
    """Return the HMAC-SHA256 of a payload with the current key."""
    return _hmac(current_app.config['SECRET_KEY'], purpose, payload)


def _signature_valid(signature: str, purpose: str, payload: str) -> bool:
    """Check a signature against the current key and every fallback key.

    Cookies signed before a key rotation stay valid until the old key is
    removed from SECRET_KEY_FALLBACKS.
    """
    keys = [current_app.config['SECRET_KEY'],
            *current_app.config['SECRET_KEY_FALLBACKS']]
    return any(
        _digests_match(signature, _hmac(key, purpose, payload)) for key in keys
    )


def _make_remember_token(user_id: str, expires: int) -> str:
    """Build ``user_id|expires|signature`` with a server-side expiry."""
    payload = f'{user_id}|{expires}'
    return f'{payload}|{_sign(_REMEMBER_PURPOSE, payload)}'


def _parse_remember_token(token: str) -> Optional[tuple[str, int]]:
    """Return ``(user identity, expiry)`` of a valid, unexpired token."""
    parts = token.split('|')
    if len(parts) != 3:
        return None
    user_id, expires_raw, signature = parts
    payload = f'{user_id}|{expires_raw}'
    if not _signature_valid(signature, _REMEMBER_PURPOSE, payload):
        return None
    if len(expires_raw) > _MAX_EXPIRY_DIGITS:
        return None
    try:
        expires = int(expires_raw)
    except ValueError:
        return None
    if time.time() > expires:
        return None
    return user_id, expires


def _set_remember_cookie(user_id: str, expires: int) -> None:
    g._remember_action = ('set', user_id, expires)


def _clear_remember_cookie() -> None:
    g._remember_action = ('clear', None, None)


def _device_signature_valid(
    signature: str, username: str, expires_raw: str
) -> bool:
    return _signature_valid(
        signature, _DEVICE_PURPOSE, f'{username}|{expires_raw}'
    )


def _device_entries() -> list[tuple[str, str]]:
    """Return the unexpired ``(expires, signature)`` entries of the cookie.

    Entries carry no username, so the cookie does not reveal who signs in
    on this browser; a match is found by re-signing the entered name.
    """
    raw = request.cookies.get(DEVICE_COOKIE_NAME, '')
    now = time.time()
    entries = []
    for entry in raw.split(_DEVICE_ENTRY_SEPARATOR)[:DEVICE_COOKIE_MAX_ENTRIES]:
        expires_raw, _, signature = entry.partition('.')
        if not (len(expires_raw) <= _MAX_EXPIRY_DIGITS
                and expires_raw.isascii() and expires_raw.isdigit()):
            continue
        if int(expires_raw) < now:
            continue
        entries.append((expires_raw, signature))
    return entries


def known_device_id(username: str) -> Optional[str]:
    """Return the id of this browser's valid device entry for a username.

    The id is a hash of the entry's signature, so failed attempts can be
    counted per entry without storing the token itself.

    Args:
        username: Normalized username.

    Returns:
        Hex id of the matching entry, or None if the browser has none.
    """
    for expires_raw, signature in _device_entries():
        if _device_signature_valid(signature, username, expires_raw):
            return hashlib.sha256(signature.encode('utf-8')).hexdigest()
    return None


def remember_device(username: str) -> None:
    """Issue or renew this browser's device entry for a username.

    The cookie is not a credential: it only waives the account's login delay
    and lockout, so logout, password changes and ended sessions leave it in
    place.

    Args:
        username: Normalized username that just signed in successfully.
    """
    others = [
        (expires_raw, signature)
        for expires_raw, signature in _device_entries()
        if not _device_signature_valid(signature, username, expires_raw)
    ]
    expires_raw = str(int(time.time() + DEVICE_COOKIE_DURATION.total_seconds()))
    signature = _sign(_DEVICE_PURPOSE, f'{username}|{expires_raw}')
    entries = [(expires_raw, signature)] + others
    g._device_cookie = _DEVICE_ENTRY_SEPARATOR.join(
        f'{expires}.{signature}'
        for expires, signature in entries[:DEVICE_COOKIE_MAX_ENTRIES]
    )


def _restore_from_remember_cookie() -> Optional[Any]:
    """Re-establish a session from a valid remember cookie.

    The session is rebuilt from scratch; the re-signed token keeps its
    original expiry, so the sign-in never outlives the last password login's
    duration. Unusable tokens schedule cookie deletion.
    """
    token = request.cookies.get(REMEMBER_COOKIE_NAME)
    if not token:
        return None

    parsed = _parse_remember_token(token)
    if parsed is None:
        _clear_remember_cookie()
        return None
    user_id, expires = parsed

    user = _user_loader(user_id)
    if user is None:
        _clear_remember_cookie()
        return None

    session.clear()
    if not _remember_restorer(user):
        _clear_remember_cookie()
        return None

    session[SESSION_USER_KEY] = user.get_id()
    session[SESSION_CLIENT_KEY] = _client_fingerprint()
    _set_remember_cookie(user.get_id(), expires)
    return user


def _load_user() -> Any:
    """Resolve the current user once per request."""
    if '_current_user' in g:
        return g._current_user

    user = None
    user_id = session.get(SESSION_USER_KEY)
    if user_id is not None:
        fingerprint = session.get(SESSION_CLIENT_KEY, '')
        if _digests_match(fingerprint, _client_fingerprint()):
            user = _user_loader(user_id)
            if user is None:
                session.clear()
        else:
            # Replayed from a different client: drop the remember cookie too,
            # or the browser would restore the sign-in on the next request.
            session.clear()
            _clear_remember_cookie()

    if user is None and user_id is None:
        cookie_name = current_app.config['SESSION_COOKIE_NAME']
        if cookie_name in request.cookies and not session:
            # Flask never sends an empty session cookie; it loads as empty
            # only when its signature expired or is invalid. Restoring here
            # would let a remember cookie bypass the idle and absolute limits.
            if REMEMBER_COOKIE_NAME in request.cookies:
                _clear_remember_cookie()
        else:
            user = _restore_from_remember_cookie()

    if user is None:
        user = AnonymousUser()

    g._current_user = user
    return user


current_user: Any = LocalProxy(_load_user)


def mark_login_fresh() -> None:
    """Record that the user has just entered the password in this session."""
    session[SESSION_FRESH_KEY] = int(time.time())


def login_is_fresh() -> bool:
    """Report whether the password was entered within FRESH_LOGIN_WINDOW.

    A session restored from a remember cookie carries no marker, so it
    never counts as fresh.
    """
    fresh_at = session.get(SESSION_FRESH_KEY)
    return (fresh_at is not None
            and time.time() - fresh_at < FRESH_LOGIN_WINDOW.total_seconds())


def login_user(user: Any, remember: bool = False) -> None:
    """Establish an authenticated session for a user.

    Args:
        user: User instance providing ``get_id()``.
        remember: If True, issue a signed remember cookie with the
            configured duration.
    """
    session[SESSION_USER_KEY] = user.get_id()
    session[SESSION_CLIENT_KEY] = _client_fingerprint()
    g._current_user = user
    # Without remember, revoke any token from an earlier login so the
    # durable sign-in does not outlive the user's latest choice.
    if remember:
        duration = current_app.config['REMEMBER_COOKIE_DURATION']
        _set_remember_cookie(
            user.get_id(), int(time.time() + duration.total_seconds())
        )
    else:
        _clear_remember_cookie()


def logout_user() -> None:
    """Terminate the authenticated session and revoke the remember cookie."""
    session.pop(SESSION_USER_KEY, None)
    session.pop(SESSION_CLIENT_KEY, None)
    # Pin the anonymous user: a later current_user access would otherwise
    # re-authenticate from the remember cookie still on the request.
    g._current_user = AnonymousUser()
    _clear_remember_cookie()


def login_required(view_func: Callable) -> Callable:
    """Restrict a view to authenticated users, redirecting to login."""
    @wraps(view_func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not current_user.is_authenticated:
            # Only GET targets survive the round trip: a POST target would
            # be re-requested as GET after login and answer 405.
            if request.method == 'GET':
                return redirect(
                    url_for('auth.login', next=request.full_path.rstrip('?'))
                )
            return redirect(url_for('auth.login'))
        return view_func(*args, **kwargs)
    return wrapper


def register(application) -> None:
    """Register the authentication hooks on the application."""

    @application.before_request
    def load_current_user():
        _load_user()

    @application.after_request
    def apply_auth_cookies(response):
        action, user_id, expires = g.get('_remember_action', (None, None, None))
        if action == 'set':
            response.set_cookie(
                REMEMBER_COOKIE_NAME,
                _make_remember_token(user_id, expires),
                max_age=max(expires - int(time.time()), 0),
                secure=application.config['REMEMBER_COOKIE_SECURE'],
                httponly=application.config['REMEMBER_COOKIE_HTTPONLY'],
                samesite=application.config['REMEMBER_COOKIE_SAMESITE'],
            )
        elif action == 'clear':
            response.delete_cookie(
                REMEMBER_COOKIE_NAME,
                secure=application.config['REMEMBER_COOKIE_SECURE'],
                httponly=application.config['REMEMBER_COOKIE_HTTPONLY'],
                samesite=application.config['REMEMBER_COOKIE_SAMESITE'],
            )
        device_cookie = g.get('_device_cookie')
        if device_cookie is not None:
            response.set_cookie(
                DEVICE_COOKIE_NAME,
                device_cookie,
                max_age=DEVICE_COOKIE_DURATION,
                secure=True,
                httponly=True,
                samesite='Lax',
            )
        return response

    @application.context_processor
    def inject_current_user():
        return {'current_user': current_user}
