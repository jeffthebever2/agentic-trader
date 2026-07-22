#!/usr/bin/env python3
"""Quick SnapTrade credential + connection checker.

Run this right after pasting your trial keys into .env to confirm they work —
no web server needed. It:
  1. loads .env,
  2. verifies clientId/consumerKey authenticate (read-only, no user creation),
  3. optionally registers/links your app user and prints a Fidelity connect URL.

Usage:
  python3 scripts/snaptrade_check.py                 # just verify the keys
  python3 scripts/snaptrade_check.py --connect EMAIL # + mint a Fidelity connect URL

Trial keys allow up to ~5 connected accounts — plenty for testing. If you have no
live brokerage handy, connect "Alpaca Paper" in the portal.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env", override=True)

from web.broker import snaptrade_store as store  # noqa: E402
from web.broker.snaptrade_data import is_enabled  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--connect", metavar="EMAIL", help="register/link this user and print a Fidelity connect URL")
    args = ap.parse_args()

    print(f"SNAPTRADE_ENABLED : {is_enabled()}")
    print(f"keys configured   : {store.keys_configured()}")
    if not store.keys_configured():
        print("\n❌ Paste your trial keys into .env first:")
        print("     SNAPTRADE_CLIENT_ID=<your clientId>")
        print("     SNAPTRADE_CONSUMER_KEY=<your consumerKey>")
        print("   (same values you enter on SnapTrade's interactive demo page)")
        return 1

    ok, reason = store.verify_credentials(force=True)
    print(f"credentials valid : {ok}  ({reason})")
    if not ok:
        print("\n❌ Keys did not authenticate. Copy the EXACT clientId + consumerKey")
        print("   from dashboard.snaptrade.com (no extra characters/duplication).")
        return 1

    print("\n✅ SnapTrade trial credentials WORK.")
    if args.connect:
        try:
            url = store.connect_url(args.connect)
            print(f"\nFidelity connect URL for {args.connect}:\n  {url}\n")
            print("Open it, log in to Fidelity (or pick 'Alpaca Paper' to test), then the")
            print("app can read accounts/balances/positions/orders (data only).")
        except Exception as e:
            print(f"connect URL failed: {e}")
            return 1
    else:
        print("Run with --connect your@email.com to link an account and get a connect URL.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
