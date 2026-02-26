#!/usr/bin/env python3
"""Setup tool.

Creates required directories, generates Flask SECRET_KEY and creates .env file.
Database initialization happens automatically on first app start.

Usage:
    python cli/setup.py init      # Create directories and .env with SECRET_KEY
    python cli/setup.py verify    # Verify installation
"""

import os
import sys
import secrets
import argparse
from datetime import datetime, timezone
from pathlib import Path


class Logger:
    """Simple logging with colors."""

    def __init__(self, quiet=False):
        self.quiet = quiet

    def success(self, msg):
        if not self.quiet:
            print(f"\033[92m✓\033[0m {msg}")

    def error(self, msg):
        print(f"\033[91m✗\033[0m {msg}", file=sys.stderr)

    def warning(self, msg):
        if not self.quiet:
            print(f"\033[93m⚠\033[0m {msg}")

    def info(self, msg):
        if not self.quiet:
            print(f"\033[94m→\033[0m {msg}")

    def section(self, title):
        if not self.quiet:
            print(f"\n{'='*60}")
            print(f"  {title}")
            print(f"{'='*60}\n")


class FBKSetup:
    """Setup tool for environment initialization."""

    def __init__(self, args):
        self.args = args
        self.logger = Logger(quiet=args.quiet)
        self.app_dir = self._find_app_dir()

    def _find_app_dir(self):
        cwd = Path.cwd()

        if (cwd / 'app.py').exists():
            return cwd

        self.logger.error("app.py not found in current directory!")
        self.logger.info("Run this script from the application directory.")
        sys.exit(1)

    def cmd_init(self):
        """Initialize environment configuration."""
        self.logger.section("FBK-Time Setup")

        self.logger.info(f"Application directory: {self.app_dir}")

        if sys.version_info < (3, 8):
            self.logger.error("Python 3.8+ required")
            return False

        if not self._create_directories():
            return False

        if not self._generate_env_file():
            return False

        self._display_next_steps()
        return True

    def cmd_verify(self):
        """Verify installation."""
        self.logger.section("Installation Verification")

        checks_passed = 0
        total_checks = 4

        env_path = self.app_dir / '.env'
        if env_path.exists():
            with open(env_path, 'r') as f:
                content = f.read()
                if 'SECRET_KEY=' in content and len(content.split('SECRET_KEY=')[1].split('\n')[0]) >= 64:
                    self.logger.success(".env file exists with valid SECRET_KEY")
                    checks_passed += 1
                else:
                    self.logger.error(".env file invalid or incomplete")
        else:
            self.logger.error(f".env file not found at {env_path}")

        settings_path = self.app_dir / 'settings.json'
        if settings_path.exists():
            self.logger.success("settings.json exists")
            checks_passed += 1
        else:
            self.logger.error("settings.json not found")

        data_dir = self.app_dir / 'data'
        logs_dir = self.app_dir / 'logs'
        dirs_ok = True

        if data_dir.exists() and self._is_writable(data_dir):
            self.logger.success("Directory 'data/' exists and is writable")
        else:
            self.logger.error("Directory 'data/' missing or not writable")
            dirs_ok = False

        if logs_dir.exists() and self._is_writable(logs_dir):
            self.logger.success("Directory 'logs/' exists and is writable")
        else:
            self.logger.error("Directory 'logs/' missing or not writable")
            dirs_ok = False

        if dirs_ok:
            checks_passed += 1

        temp_key_set = False
        try:
            sys.path.insert(0, str(self.app_dir))
            os.chdir(self.app_dir)

            if 'SECRET_KEY' not in os.environ:
                os.environ['SECRET_KEY'] = 'test-key-for-verification'
                temp_key_set = True

            from config import Config
            self.logger.success("Configuration loadable")
            checks_passed += 1
        except Exception as e:
            self.logger.error(f"Configuration error: {e}")
        finally:
            if temp_key_set:
                del os.environ['SECRET_KEY']

        if checks_passed == total_checks:
            self.logger.section("VERIFICATION PASSED")
            self.logger.success("System ready! Start with: systemctl start fbk-time")
            return True
        else:
            self.logger.section(f"VERIFICATION INCOMPLETE ({checks_passed}/{total_checks})")
            return False

    def _is_writable(self, path):
        try:
            test_file = path / '.write_test'
            test_file.touch()
            test_file.unlink()
            return True
        except Exception:
            return False

    def _create_directories(self):
        directories = ['data', 'logs']

        for dir_name in directories:
            dir_path = self.app_dir / dir_name

            if dir_path.exists():
                self.logger.success(f"Directory '{dir_name}/' already exists")
                continue

            try:
                dir_path.mkdir(mode=0o755, parents=True, exist_ok=True)
                self.logger.success(f"Created directory '{dir_name}/'")
            except Exception as e:
                self.logger.error(f"Failed to create '{dir_name}/': {e}")
                return False

        return True

    def _generate_env_file(self):
        env_path = self.app_dir / '.env'

        if env_path.exists() and not self.args.force:
            self.logger.warning(f".env already exists at {env_path}")
            self.logger.info("Use --force to overwrite")
            return True

        self.logger.info("Generating SECRET_KEY (64 hex characters)...")
        secret_key = secrets.token_hex(32)

        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

        env_content = f"""# Environment Configuration
# Generated: {timestamp}
# DO NOT COMMIT THIS FILE TO VERSION CONTROL

SECRET_KEY={secret_key}
FLASK_ENV=production
"""

        try:
            with open(env_path, 'w') as f:
                f.write(env_content)

            # Set secure permissions (Unix only)
            try:
                os.chmod(env_path, 0o600)
                self.logger.success(f"Created {env_path} with secure permissions (600)")
            except (OSError, AttributeError):
                self.logger.success(f"Created {env_path}")
                self.logger.warning("Set file permissions manually on this system")

            return True

        except Exception as e:
            self.logger.error(f"Failed to create .env: {e}")
            return False

    def _display_next_steps(self):
        print("\n" + "=" * 60)
        print("  FBK-TIME SETUP COMPLETE")
        print("=" * 60)
        print()
        print("  Created:")
        print("    • data/ directory")
        print("    • logs/ directory")
        print("    • .env file with SECRET_KEY")
        print()
        print("  Next steps:")
        print("    1. Create admin user:")
        print(f"       python cli/manage_user.py create-user admin --role admin")
        print()
        print("    2. Start the application:")
        print("       systemctl start fbk-time")
        print("       OR")
        print("       gunicorn -c gunicorn.conf.py app:app")
        print()
        print("    3. Access the application:")
        print("       https://your-domain.de")
        print()
        print("  Database initialization happens automatically on first start.")
        print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        prog='setup',
        description='FBK-Time Setup Tool',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python cli/setup.py init          # Initial setup
    python cli/setup.py init --force  # Overwrite existing .env
    python cli/setup.py verify        # Verify installation
        """
    )

    parser.add_argument('--force', action='store_true',
                        help='Overwrite existing configuration')
    parser.add_argument('--quiet', '-q', action='store_true',
                        help='Minimal output')

    subparsers = parser.add_subparsers(dest='command', required=True,
                                       help='Available commands')

    subparsers.add_parser('init', help='Initialize environment')
    subparsers.add_parser('verify', help='Verify installation')

    args = parser.parse_args()

    try:
        setup = FBKSetup(args)

        if args.command == 'init':
            success = setup.cmd_init()
        elif args.command == 'verify':
            success = setup.cmd_verify()
        else:
            parser.print_help()
            success = False

        sys.exit(0 if success else 1)

    except KeyboardInterrupt:
        print("\n✗ Setup cancelled")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
