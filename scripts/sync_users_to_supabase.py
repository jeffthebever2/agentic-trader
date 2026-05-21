#!/usr/bin/env python3
"""
One-shot: push existing tmp/users.json into the Supabase agentic_users table.

Requires SUPABASE_URL + SUPABASE_SERVICE_KEY in the environment (.env).
Safe to re-run: upserts on the email primary key.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

from web import supabase_store  # noqa: E402


def main() -> int:
    if not supabase_store.enabled():
        print("Supabase not configured. Set SUPABASE_URL + SUPABASE_SERVICE_KEY.", file=sys.stderr)
        return 1

    store = ROOT / "tmp" / "users.json"
    if not store.exists():
        print("No tmp/users.json to sync.")
        return 0

    data = json.loads(store.read_text(encoding="utf-8") or "{}")
    records = list(data.values())
    if not records:
        print("users.json is empty; nothing to sync.")
        return 0

    supabase_store.upsert_many(records)
    print(f"Synced {len(records)} user(s) to Supabase agentic_users:")
    for r in records:
        print(f"  {r.get('role','user'):6s} {r.get('email')}")

    # Verify round-trip.
    remote = supabase_store.fetch_all()
    print(f"\nSupabase now holds {len(remote)} row(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
