#!/usr/bin/env python3
"""Generate a bcrypt password hash for config.json."""

from __future__ import annotations

import getpass
import sys

from passlib.context import CryptContext


_PWD_CONTEXT = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _get_password_from_args() -> str | None:
    if len(sys.argv) >= 2:
        return sys.argv[1]
    return None


def _prompt_password() -> str:
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm: ")
    if password != confirm:
        print("Passwords do not match.", file=sys.stderr)
        raise SystemExit(1)
    return password


def main() -> int:
    password = _get_password_from_args()
    if password is None:
        password = _prompt_password()

    if not password:
        print("Password cannot be empty.", file=sys.stderr)
        return 1

    hashed = _PWD_CONTEXT.hash(password)
    print(hashed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
