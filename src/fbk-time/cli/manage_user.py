#!/usr/bin/env python3
"""User management CLI tool.

Provides commands for managing user accounts with RBAC:
- create-user: Create a new user with role
- reset-password: Reset password for existing user
- set-role: Change user role
- set-status: Change user status
- delete-user: Delete a user
- list-users: List all users with roles
"""

import argparse
import getpass
import sys
from datetime import timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select

from app import create_app
from core.db import db
from core.settings_manager import settings_manager
from core.timezone import get_app_timezone
from modules.auth.models import User, UserRole, UserStatus
from modules.auth.services import clear_login_attempts_for_username, ph
from modules.user.services import set_user_password, validate_last_admin
from utils.validators import (
    USERNAME_RULE_MESSAGE,
    is_valid_username,
    normalize_email,
    normalize_username,
    validate_password_strength,
)


def get_cli_app():
    """Create app instance for CLI usage without web components."""
    return create_app(cli_mode=True)


VALID_ROLES = ['admin', 'manager', 'user']
VALID_STATUSES = ['active', 'disabled', 'locked', 'pending', 'managed']


def get_password(confirm=True):
    """Prompt for password with validation and optional confirmation.

    Args:
        confirm: If True, ask for password twice.

    Returns:
        Password string or None if validation failed.
    """
    password = getpass.getpass('Passwort: ')

    if not password:
        print('Fehler: Passwort darf nicht leer sein.')
        return None

    is_valid, error_msg = validate_password_strength(password)
    if not is_valid:
        print(f'Fehler: {error_msg}')
        return None

    if confirm:
        password_confirm = getpass.getpass('Passwort bestätigen: ')
        if password != password_confirm:
            print('Fehler: Passwörter stimmen nicht überein.')
            return None

    return password


def create_user(username, role='user', no_login=False):
    """Create a new user with role.

    Args:
        username: Username for the new user.
        role: Role for the new user (admin, manager, user).
        no_login: If True, create user with MANAGED status (no login capability).
    """
    app = get_cli_app()

    role_lower = role.lower()
    if role_lower not in VALID_ROLES:
        print(f'Fehler: Ungültige Rolle. Erlaubt: {", ".join(VALID_ROLES)}')
        sys.exit(1)

    if no_login and role_lower in ['admin', 'manager']:
        print('Fehler: Admin und Manager können nicht mit --no-login erstellt werden.')
        sys.exit(1)

    if not is_valid_username(username):
        print(f'Fehler: {USERNAME_RULE_MESSAGE}')
        sys.exit(1)

    with app.app_context():
        username = normalize_username(username)
        existing = db.session.scalars(
            select(User).filter_by(username=username)
        ).first()
        if existing:
            print(f'Fehler: Benutzer "{username}" existiert bereits.')
            sys.exit(1)

        operation_mode = settings_manager.get('operation_mode')
        if operation_mode == 'single_user' and role_lower == 'user' and not no_login:
            no_login = True
            print('Info: SingleUser-Modus aktiv - User wird ohne Login erstellt.')

        name = input('Anzeigename: ').strip()
        if not name:
            print('Fehler: Anzeigename darf nicht leer sein.')
            sys.exit(1)

        email = normalize_email(input('E-Mail (optional): '))

        if no_login:
            # Random secret: no password can ever match it.
            import secrets
            password_hash = ph.hash(secrets.token_hex(32))
            status = UserStatus.MANAGED
            force_pwd_change = False
            has_real_pwd = False
        else:
            password = get_password(confirm=True)
            if not password:
                sys.exit(1)
            password_hash = ph.hash(password)
            status = UserStatus.ACTIVE
            force_pwd_change = settings_manager.get('password_force_change_on_first_login')
            has_real_pwd = True

        role_enum = UserRole[role_lower.upper()]

        user = User(
            username=username,
            password_hash=password_hash,
            name=name,
            email=email,
            role=role_enum,
            status=status,
            force_password_change=force_pwd_change,
            has_real_password=has_real_pwd,
            theme=settings_manager.get('user_default_theme'),
            date_format=settings_manager.get('user_default_date_format'),
            items_per_page=settings_manager.get('user_default_items_per_page'),
            holiday_region=settings_manager.get('user_default_holiday_region'),
            default_text_color=settings_manager.get('user_default_text_color'),
            start_page=settings_manager.get('user_default_start_page'),
            view_scope=settings_manager.get('user_default_view_scope')
        )

        db.session.add(user)
        db.session.commit()

        status_info = ' (ohne Login)' if no_login else ''
        print(f'Benutzer "{username}" mit Rolle "{role_lower}"{status_info} wurde erstellt.')


def reset_password(username):
    """Reset password for an existing user.

    Args:
        username: Username of the user to reset.
    """
    app = get_cli_app()

    with app.app_context():
        username = username.lower()
        user = db.session.scalars(
            select(User).filter_by(username=username)
        ).first()
        if not user:
            print(f'Fehler: Benutzer "{username}" nicht gefunden.')
            sys.exit(1)

        if user.status == UserStatus.MANAGED:
            print('Fehler: MANAGED User hat keinen Login. Nutze set-status um Login zu aktivieren.')
            sys.exit(1)

        print(f'Neues Passwort für "{username}" setzen:')
        password = get_password(confirm=True)
        if not password:
            sys.exit(1)

        set_user_password(user, password, require_change=True)
        db.session.commit()

        print(f'Passwort für "{username}" wurde zurückgesetzt.')
        print('Benutzer muss Passwort beim nächsten Login ändern.')


def set_role(username, role):
    """Change user role.

    Args:
        username: Username of the user.
        role: New role (admin, manager, user).
    """
    app = get_cli_app()

    role_lower = role.lower()
    if role_lower not in VALID_ROLES:
        print(f'Fehler: Ungültige Rolle. Erlaubt: {", ".join(VALID_ROLES)}')
        sys.exit(1)

    with app.app_context():
        username = username.lower()
        user = db.session.scalars(
            select(User).filter_by(username=username)
        ).first()
        if not user:
            print(f'Fehler: Benutzer "{username}" nicht gefunden.')
            sys.exit(1)

        new_role = UserRole[role_lower.upper()]

        is_valid, error = validate_last_admin(user, new_role)
        if not is_valid:
            print(f'Fehler: {error}')
            sys.exit(1)

        if user.status == UserStatus.MANAGED and new_role in [UserRole.ADMIN, UserRole.MANAGER]:
            print('Fehler: MANAGED User muss zuerst aktiviert werden, bevor Rolle geändert werden kann.')
            sys.exit(1)

        old_role = user.role.value
        user.role = new_role
        db.session.commit()

        print(f'Rolle von "{username}" geändert: {old_role} -> {role_lower}')


def set_status(username, status):
    """Change user status.

    Args:
        username: Username of the user.
        status: New status (active, disabled, locked, pending, managed).
    """
    app = get_cli_app()

    status_lower = status.lower()
    if status_lower not in VALID_STATUSES:
        print(f'Fehler: Ungültiger Status. Erlaubt: {", ".join(VALID_STATUSES)}')
        sys.exit(1)

    with app.app_context():
        username = username.lower()
        user = db.session.scalars(
            select(User).filter_by(username=username)
        ).first()
        if not user:
            print(f'Fehler: Benutzer "{username}" nicht gefunden.')
            sys.exit(1)

        if status_lower == 'managed' and user.role in [UserRole.ADMIN, UserRole.MANAGER]:
            print('Fehler: Admin und Manager können nicht auf MANAGED gesetzt werden.')
            sys.exit(1)

        if user.status == UserStatus.MANAGED and status_lower == 'active':
            if user.has_real_password:
                # The stored password may be stale or compromised.
                user.force_password_change = True
                print('Hinweis: MANAGED User hat bereits ein Passwort. Status wird geändert.')
                print('Benutzer muss Passwort beim nächsten Login ändern.')
            else:
                print('Hinweis: MANAGED User wird aktiviert. Passwort muss gesetzt werden.')
                password = get_password(confirm=True)
                if not password:
                    sys.exit(1)
                set_user_password(user, password, require_change=True)

        old_status = user.status.value
        user.status = UserStatus[status_lower.upper()]

        if status_lower == 'active':
            clear_login_attempts_for_username(user.username)

        db.session.commit()

        print(f'Status von "{username}" geändert: {old_status} -> {status_lower}')


def delete_user(username):
    """Delete a user with confirmation.

    Args:
        username: Username of the user to delete.
    """
    app = get_cli_app()

    with app.app_context():
        username = username.lower()
        user = db.session.scalars(
            select(User).filter_by(username=username)
        ).first()
        if not user:
            print(f'Fehler: Benutzer "{username}" nicht gefunden.')
            sys.exit(1)

        print(f'Benutzer: {user.name} ({user.username})')
        print(f'Rolle: {user.role.value}')
        from modules.absence.models import Absence
        absence_count = db.session.scalar(
            select(func.count()).select_from(Absence).filter_by(user_id=user.id)
        )
        print(f'Abwesenheiten: {absence_count}')
        print()

        confirm = input('Benutzer wirklich löschen? Alle Abwesenheiten werden ebenfalls gelöscht! (ja/nein): ')
        if confirm.lower() != 'ja':
            print('Löschung abgebrochen.')
            sys.exit(0)

        from modules.user.services import delete_user
        delete_user(user)

        print(f'Benutzer "{username}" wurde gelöscht.')


def list_users():
    """List all users with roles and status."""
    app = get_cli_app()

    with app.app_context():
        users = db.session.scalars(
            select(User).order_by(User.role, User.name)
        ).all()

        if not users:
            print('Keine Benutzer vorhanden.')
            return

        print('\nBenutzer:')
        print('-' * 80)
        print(f'{"Username":<20} {"Name":<25} {"Rolle":<10} {"Status":<10} {"Erstellt":<12}')
        print('-' * 80)

        for user in users:
            if user.created_at:
                utc_time = user.created_at.replace(tzinfo=timezone.utc)
                local_time = utc_time.astimezone(get_app_timezone())
                created = local_time.strftime('%d.%m.%Y')
            else:
                created = '-'

            status_display = user.status.value
            if user.status == UserStatus.ACTIVE:
                status_display = 'Aktiv'
            elif user.status == UserStatus.DISABLED:
                status_display = 'Deaktiviert'
            elif user.status == UserStatus.LOCKED:
                status_display = 'Gesperrt'
            elif user.status == UserStatus.PENDING:
                status_display = 'Ausstehend'
            elif user.status == UserStatus.MANAGED:
                status_display = 'Verwaltet'

            print(f'{user.username:<20} {user.name:<25} {user.role.value:<10} {status_display:<10} {created:<12}')

        print('-' * 80)
        print(f'Gesamt: {len(users)} Benutzer')


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description='FBK-Time Benutzerverwaltung',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Beispiele:
  python manage_user.py create-user admin --role admin
  python manage_user.py create-user mmustermann --role user
  python manage_user.py reset-password admin
  python manage_user.py set-role mmustermann manager
  python manage_user.py set-status mmustermann disabled
  python manage_user.py delete-user mmustermann
  python manage_user.py list-users
        '''
    )

    subparsers = parser.add_subparsers(dest='command', help='Verfügbare Befehle')

    parser_create = subparsers.add_parser(
        'create-user',
        help='Neuen Benutzer erstellen'
    )
    parser_create.add_argument(
        'username',
        help='Benutzername'
    )
    parser_create.add_argument(
        '--role', '-r',
        default='user',
        choices=VALID_ROLES,
        help='Benutzerrolle (default: user)'
    )
    parser_create.add_argument(
        '--no-login',
        action='store_true',
        help='Benutzer ohne Login erstellen (Status: MANAGED)'
    )

    parser_reset = subparsers.add_parser(
        'reset-password',
        help='Passwort zurücksetzen'
    )
    parser_reset.add_argument(
        'username',
        help='Benutzername'
    )

    parser_role = subparsers.add_parser(
        'set-role',
        help='Benutzerrolle ändern'
    )
    parser_role.add_argument(
        'username',
        help='Benutzername'
    )
    parser_role.add_argument(
        'role',
        choices=VALID_ROLES,
        help='Neue Rolle'
    )

    parser_status = subparsers.add_parser(
        'set-status',
        help='Benutzerstatus ändern'
    )
    parser_status.add_argument(
        'username',
        help='Benutzername'
    )
    parser_status.add_argument(
        'status',
        choices=VALID_STATUSES,
        help='Neuer Status'
    )

    parser_delete = subparsers.add_parser(
        'delete-user',
        help='Benutzer löschen'
    )
    parser_delete.add_argument(
        'username',
        help='Benutzername'
    )

    subparsers.add_parser(
        'list-users',
        help='Alle Benutzer auflisten'
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == 'create-user':
        create_user(args.username, args.role, args.no_login)
    elif args.command == 'reset-password':
        reset_password(args.username)
    elif args.command == 'set-role':
        set_role(args.username, args.role)
    elif args.command == 'set-status':
        set_status(args.username, args.status)
    elif args.command == 'delete-user':
        delete_user(args.username)
    elif args.command == 'list-users':
        list_users()


if __name__ == '__main__':
    main()
