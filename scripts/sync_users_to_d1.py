#!/usr/bin/env python3
"""One-shot: push tmp/users.json into Cloudflare D1."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from web import d1_store  # noqa: E402


def main() -> int:
    if not d1_store.enabled():
        print(
            "D1 not configured. Set CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN, and CLOUDFLARE_D1_DATABASE_ID.",
            file=sys.stderr,
        )
        return 2
    store = ROOT / "tmp" / "users.json"
    if not store.exists():
        print("No tmp/users.json to sync.")
        return 0
    data = json.loads(store.read_text(encoding="utf-8") or "{}")
    records = list(data.values())
    if not records:
        print("users.json is empty; nothing to sync.")
        return 0
    d1_store.upsert_many(records)
    print(f"Synced {len(records)} user(s) to Cloudflare D1:")
    for rec in records:
        print(f"  - {rec.get('email')} ({rec.get('role', 'user')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
