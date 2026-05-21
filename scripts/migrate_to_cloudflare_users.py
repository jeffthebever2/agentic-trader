#!/usr/bin/env python3
"""
Seed the local user registry with an initial admin from
CF_ACCESS_BOOTSTRAP_ADMIN (comma-separated emails) and/or any
emails passed positionally.

Examples:
  CF_ACCESS_BOOTSTRAP_ADMIN=you@example.com python3 scripts/migrate_to_cloudflare_users.py
  python3 scripts/migrate_to_cloudflare_users.py you@example.com teammate@example.com
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web import users as user_store  # noqa: E402


def main() -> int:
    cli_emails = [a.strip().lower() for a in sys.argv[1:] if a.strip()]
    env_emails = [
        e.strip().lower()
        for e in os.getenv("CF_ACCESS_BOOTSTRAP_ADMIN", "").split(",")
        if e.strip()
    ]
    emails = list(dict.fromkeys(cli_emails + env_emails))  # de-dupe, preserve order

    if not emails:
        print(
            "No emails provided. Pass them as args or set "
            "CF_ACCESS_BOOTSTRAP_ADMIN=email1,email2",
            file=sys.stderr,
        )
        return 1

    for email in emails:
        rec = user_store.get_or_create_user(email)
        if rec["role"] != "admin":
            rec = user_store.set_role(email, "admin")
        print(f"  admin: {rec['email']}")

    print(f"\nUsers now ({len(user_store.list_users())}):")
    for u in user_store.list_users():
        print(f"  {u['role']:6s} {u['email']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
