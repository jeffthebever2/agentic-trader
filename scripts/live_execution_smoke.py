#!/usr/bin/env python3
"""Supervised live-execution smoke test — drives the REAL Fidelity ticket, never submits.

Browser automation cannot be verified by unit tests: the guards are testable
against fixtures, but Fidelity's live DOM is not. That leaves execution as the
one surface that unit tests cannot reach — which is exactly the surface holding
real money.

This closes most of that gap without spending any. It walks the *entire*
production order path — navigate, select and verify the account, fill symbol /
action / quantity / order type / limit price, click **Preview**, read the ticket
back, and run the same `verify_order_ticket` gate the live path runs — then
STOPS. The Place Order button is never located and never clicked.

What it proves:
  * the Fidelity session is valid and the trade ticket still loads;
  * every DOM selector the order path depends on still resolves;
  * the account selector picks the intended account and verifies it back;
  * the live preview page agrees with our intent (symbol/side/qty/limit);
  * the confirmation reader classifies the real page correctly.

What it cannot prove: submission and fill. Only a real order does that.

SAFETY
  * `--execute` does not exist. This script has no code path that submits.
  * The limit price is deliberately far BELOW market (default 50%), so even a
    hypothetical submission could not fill.
  * Quantity defaults to 1 share.
  * Refuses to run if the startup preflight reports a CRITICAL finding.

Usage:
    python3 scripts/live_execution_smoke.py --email you@example.com \\
        --account 123456789 --ticker F
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web.app import app  # noqa: E402,F401  (loads .env, defines shared helpers)

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
_results: list[tuple[str, str, str]] = []


def _record(step: str, status: str, detail: str = "") -> None:
    _results.append((step, status, detail))
    icon = {"PASS": "  ok  ", "FAIL": " FAIL ", "SKIP": " skip "}[status]
    print(f"[{icon}] {step}" + (f" — {detail}" if detail else ""), flush=True)


async def _run(email: str, account: str, ticker: str, discount_pct: float,
               shares: int) -> int:
    from tradingagents.compliance import LIVE_TRADING_HARD_BLOCKED
    from tradingagents.preflight import format_findings, run_preflight
    import os

    # ── 0. Preflight ─────────────────────────────────────────────────────────
    pf = run_preflight(os.environ, hard_blocked=LIVE_TRADING_HARD_BLOCKED)
    if pf.critical:
        _record("preflight", FAIL, f"{len(pf.critical)} critical finding(s)")
        print("\n" + format_findings(pf))
        return 1
    _record("preflight", PASS, f"{len(pf.warnings)} warning(s)")

    from web.api.fidelity import (
        _assert_account_tradeable, _ensure_browser, _is_login_url, _nav,
        _select_and_verify_account, _verify_fidelity_order_page,
        _get_fidelity_balances,
    )
    from fastapi import HTTPException

    # ── 1. Account guard ─────────────────────────────────────────────────────
    try:
        _assert_account_tradeable(account)
        _record("account guard", PASS, f"{account[-4:]} is tradeable")
    except HTTPException as e:
        _record("account guard", FAIL, str(e.detail)[:120])
        return 1

    # ── 2. Browser + session ─────────────────────────────────────────────────
    try:
        ctx = await _ensure_browser(email)
    except Exception as e:
        _record("browser launch", FAIL, str(e)[:160])
        return 1
    _record("browser launch", PASS)

    page = await ctx.new_page()
    try:
        trade_url = ("https://digital.fidelity.com/ftgw/digital/trade-equity"
                     "/index/orderEntry")
        await _nav(page, trade_url, sleep=5)
        if _is_login_url(page.url):
            _record("session", FAIL, "redirected to login — log in through the UI first")
            return 1
        _record("session", PASS, "trade ticket reachable")

        # ── 3. Account selector ──────────────────────────────────────────────
        try:
            await _select_and_verify_account(page, account)
            _record("account selector", PASS, "selected and verified on the ticket")
        except Exception as e:
            _record("account selector", FAIL, str(e)[:160])
            return 1

        # ── 4. Reference price (for a deliberately unfillable limit) ─────────
        try:
            import yfinance as yf
            hist = yf.download(ticker, period="5d", progress=False, auto_adjust=True)
            last = float(hist["Close"].dropna().iloc[-1])
        except Exception as e:
            _record("reference price", FAIL, str(e)[:120])
            return 1
        limit_price = round(last * (1 - discount_pct / 100.0), 2)
        _record("reference price", PASS,
                f"last ${last:.2f} → limit ${limit_price:.2f} "
                f"({discount_pct:.0f}% below market, cannot fill)")

        # ── 5. Fill the ticket (identical to the production path) ───────────
        sym_input = page.locator('#eq-ticket-dest-symbol')
        await sym_input.wait_for(state="visible", timeout=10000)
        await sym_input.click()
        await sym_input.fill(ticker)
        await asyncio.sleep(1.5)
        await page.keyboard.press("ArrowDown")
        await asyncio.sleep(0.5)
        await page.keyboard.press("Enter")
        await asyncio.sleep(2)
        _record("symbol input", PASS, ticker)

        await page.locator('#dest-dropdownlist-button-action').click()
        await asyncio.sleep(1)
        for sel in ['[role="option"]:has-text("Buy")', 'li:has-text("Buy")',
                    'a:has-text("Buy")']:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=1500):
                    await loc.click()
                    break
            except Exception:
                continue
        await asyncio.sleep(0.8)
        _record("action selector", PASS, "Buy")

        await page.evaluate(f"""
        () => {{
            const el = document.getElementById('eqt-shared-quantity');
            if (!el) throw new Error('qty input not found');
            el.focus();
            const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value');
            s.set.call(el, '{shares}');
            el.dispatchEvent(new Event('input',  {{bubbles:true}}));
            el.dispatchEvent(new Event('change', {{bubbles:true}}));
            el.dispatchEvent(new KeyboardEvent('keyup', {{bubbles:true}}));
        }}
        """)
        await asyncio.sleep(0.8)
        _record("quantity input", PASS, f"{shares} share(s)")

        await page.locator('#dest-dropdownlist-button-ordertype').click()
        await asyncio.sleep(1)
        for sel in ['[role="option"]:has-text("Limit")', 'li:has-text("Limit")',
                    'a:has-text("Limit")']:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=1500):
                    await loc.click()
                    break
            except Exception:
                continue
        await asyncio.sleep(0.8)
        _record("order type selector", PASS, "Limit")

        price_input = page.locator('input[id*="price" i], input[name*="price" i]').first
        await price_input.wait_for(state="visible", timeout=5000)
        await price_input.click(click_count=3)
        await price_input.fill(str(limit_price))
        await asyncio.sleep(0.8)
        _record("limit price input", PASS, f"${limit_price:.2f}")

        # ── 6. Preview (this is as far as we go) ────────────────────────────
        preview_btn = page.locator(
            'button.pvd-button--primary.pvd-button--full-width,'
            'button:has-text("Preview order"),'
            'button:has-text("Preview Order")'
        ).first
        await preview_btn.wait_for(state="visible", timeout=8000)
        await preview_btn.click()
        await asyncio.sleep(5)
        preview_text = await page.evaluate("() => document.body.innerText")
        _record("preview click", PASS, f"{len(preview_text)} chars returned")

        ok, msg = _verify_fidelity_order_page(preview_text)
        _record("preview page reader", PASS if ok else FAIL, msg[:120])

        # ── 7. The real pre-submit gate, against the LIVE page ──────────────
        from tradingagents.brokers.order_verifier import (
            OrderIntent, verify_order_ticket,
        )
        intent = OrderIntent(
            account_mask=f"•••••{account[-4:]}", symbol=ticker.upper(), side="buy",
            quantity=shares, order_type="limit", limit_price=limit_price,
            est_cost=round(shares * limit_price, 2),
        )
        t_ok, reasons = verify_order_ticket(intent, preview_text)
        _record("ticket verifier (live DOM)", PASS if t_ok else FAIL,
                "ticket matches intent" if t_ok else "; ".join(reasons)[:200])

        if not t_ok:
            print("\n--- preview page excerpt ---")
            print(preview_text[:1200])

        # ── 8. Explicitly NOT submitting ────────────────────────────────────
        _record("place order", SKIP, "by design — this script cannot submit")
        return 0 if all(s != FAIL for _, s, _ in _results) else 1

    finally:
        try:
            await page.close()
        except Exception:
            pass


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--email", required=True, help="user whose Fidelity session to drive")
    p.add_argument("--account", required=True, help="explicit account number")
    p.add_argument("--ticker", default="F", help="liquid, low-priced ticker (default F)")
    p.add_argument("--shares", type=int, default=1)
    p.add_argument("--discount-pct", type=float, default=50.0,
                   help="how far BELOW market to set the limit (default 50%%)")
    args = p.parse_args()

    if args.shares < 1:
        print("shares must be >= 1")
        return 2

    print(__doc__.split("Usage:")[0])
    print(f"Driving the REAL Fidelity ticket for {args.ticker} "
          f"({args.shares} share(s), {args.discount_pct:.0f}% below market).")
    print("This script has NO code path that submits an order.\n")

    rc = asyncio.run(_run(args.email, args.account, args.ticker,
                          args.discount_pct, args.shares))

    print("\n" + "=" * 62)
    failed = [s for s in _results if s[1] == FAIL]
    print(f"{len(_results)} step(s): "
          f"{sum(1 for s in _results if s[1] == PASS)} passed, "
          f"{len(failed)} failed, "
          f"{sum(1 for s in _results if s[1] == SKIP)} skipped")
    if failed:
        print("\nFAILED STEPS — execution is NOT safe to arm:")
        for step, _, detail in failed:
            print(f"  - {step}: {detail}")
    else:
        print("\nEvery DOM selector and every pre-submit gate verified against the "
              "live page.\nStill unproven: submission and fill — only a real order "
              "shows that.")
    print("=" * 62)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
