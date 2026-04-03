#!/usr/bin/env python3
"""CLI utility for managing web admin users and configuration."""

import json
import sys
from pathlib import Path

from app.core.config_manager import ConfigManager
from app.core.security import get_password_hash


def main():
    """Main CLI entry point."""
    if len(sys.argv) < 2:
        print_help()
        sys.exit(1)

    command = sys.argv[1]

    if command == "init-config":
        init_config()
    elif command == "hash-password":
        hash_password()
    elif command == "add-user":
        add_user()
    elif command == "list-users":
        list_users()
    else:
        print(f"Unknown command: {command}")
        print_help()
        sys.exit(1)


def print_help():
    """Print help message."""
    print("""
Web Admin Panel CLI Tool

Usage:
    python -m scripts.admin_cli <command> [args]

Commands:
    init-config              - Initialize config.json with default settings
    hash-password            - Generate a bcrypt hash for a password
    add-user <username>      - Add or update a web admin user
    list-users               - List all web admin users

Examples:
    python -m scripts.admin_cli init-config
    python -m scripts.admin_cli hash-password
    python -m scripts.admin_cli add-user admin
    python -m scripts.admin_cli list-users
    """)


def init_config():
    """Initialize config.json with default settings."""
    config_path = Path("config.json")
    
    if config_path.exists():
        print(f"Config file already exists: {config_path}")
        response = input("Overwrite? (y/n): ").strip().lower()
        if response != 'y':
            print("Aborted.")
            return

    manager = ConfigManager(config_path)
    success, message = manager.update_config(manager._default_config())
    
    if success:
        print(f"✅ {message}")
        print(f"Config file created: {config_path}")
        print("\nNext steps:")
        print("1. Edit config.json and set your password hash:")
        print("   python -m scripts.admin_cli hash-password")
        print("2. Or add a user directly:")
        print("   python -m scripts.admin_cli add-user admin")
    else:
        print(f"❌ Failed: {message}")
        sys.exit(1)


def hash_password():
    """Generate a bcrypt hash for a password."""
    import getpass
    
    print("Generate password hash")
    password = getpass.getpass("Enter password: ")
    password_confirm = getpass.getpass("Confirm password: ")
    
    if password != password_confirm:
        print("❌ Passwords do not match!")
        sys.exit(1)
    
    if not password:
        print("❌ Password cannot be empty!")
        sys.exit(1)
    
    hashed = get_password_hash(password)
    print(f"\n✅ Password hash:\n{hashed}")
    print("\nAdd this to your config.json under web_users[0].password_hash")


def add_user():
    """Add or update a web admin user."""
    if len(sys.argv) < 3:
        print("Usage: python -m scripts.admin_cli add-user <username>")
        sys.exit(1)

    username = sys.argv[2]
    config_path = Path("config.json")
    
    if not config_path.exists():
        print(f"❌ Config file not found: {config_path}")
        print("Run 'init-config' first")
        sys.exit(1)

    import getpass
    password = getpass.getpass(f"Enter password for '{username}': ")
    password_confirm = getpass.getpass("Confirm password: ")
    
    if password != password_confirm:
        print("❌ Passwords do not match!")
        sys.exit(1)
    
    if not password:
        print("❌ Password cannot be empty!")
        sys.exit(1)

    manager = ConfigManager(config_path)
    config = manager.get_config()
    
    # Ensure web_users list exists
    if "web_users" not in config:
        config["web_users"] = []
    
    # Find or create user
    users = config["web_users"]
    user_found = False
    for user in users:
        if user.get("username") == username:
            user["password_hash"] = get_password_hash(password)
            user_found = True
            break
    
    if not user_found:
        users.append({
            "username": username,
            "password_hash": get_password_hash(password)
        })
    
    success, message = manager.update_config(config)
    
    if success:
        action = "Updated" if user_found else "Added"
        print(f"✅ {action} user '{username}'")
    else:
        print(f"❌ Failed: {message}")
        sys.exit(1)


def list_users():
    """List all web admin users."""
    config_path = Path("config.json")
    
    if not config_path.exists():
        print(f"❌ Config file not found: {config_path}")
        sys.exit(1)

    manager = ConfigManager(config_path)
    users = manager.get_web_users()
    
    if not users:
        print("No users configured.")
        return
    
    print(f"\nConfigured Web Admin Users ({len(users)}):")
    print("-" * 40)
    for user in users:
        username = user.get("username", "unknown")
        has_hash = bool(user.get("password_hash"))
        status = "✅ configured" if has_hash else "❌ no password"
        print(f"  • {username} ... {status}")


if __name__ == "__main__":
    main()
