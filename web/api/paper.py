import csv
import asyncio
import datetime as dt
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from contextlib import closing
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from web.auth import require_admin

ROOT = Path(__file__).parent.parent.parent
DEFAULT_OUTPUT_BASE = ROOT / "tmp" / "paper_trading_today"
SCRIPT_PATH = ROOT / "scripts" / "paper_trade_today.py"
AUTOSTART_CONFIG_PATH = ROOT / "tmp" / "paper_autostart.json"
DEFAULT_AUTOSTART_CONFIG = {
    "enabled": False,
    "starting_cash": 10000,
    "scan_interval_minutes": 15,
    "max_tickers": 0,
    "openrouter_model": "openai/gpt-4o-mini",
    "model_bundle": "ml_models/stock_universe/model_bundle.joblib",
    "new_model_bundle": "ml_models/latest/model_bundle.joblib",
    "ai_shortlist_size": 30,
    "ai_max_picks": 5,
    "include_pure_ai": True,
    "tickers": "all_tickers.txt",
    "position_cap_pct": 25.0,
    "position_cap_min_pct": 10.0,
    "position_high_confidence_threshold": 0.80,
    "take_profit_pct": 0.0,
    "stop_loss_pct": 0.0,
    "partial_profit_pct": 0.5,
    "partial_profit_fraction": 0.5,
    "trailing_stop_atr_mult": 0.5,
    "time_decay_scans": 0,
    "sector_max_positions": 3,
    "daily_loss_limit_pct": 2.0,
    "max_portfolio_drawdown": 0.05,
    "risk_per_trade_pct": 0.0,
    "min_risk_reward": 1.3,
    "bear_regime_size_factor": 0.5,
    "neutral_regime_size_factor": 0.75,
    "max_positions": 5,
    "ml_probability_threshold": 0.72,
    "ml_large_loss_max": 0.20,
    "ml_expected_return_min": 0.0,
    "target_mult": 1.5,
    "stop_mult": 1.0,
    "breadth_threshold": 0.40,
    "max_heat_pct": 80.0,
    "double_target_exit_pct": 0.5,
    "premarket_warmup_minutes": 30,
    "hold_overnight": True,
    "long_hold_days": 20,
    "sms_number": "",
    "hil_timeout_minutes": 15,
    "hil_auto_reject": True,
    "sms_on_fills": False,
}
STRATEGIES = ["algorithm", "machine_learning", "ml_new", "combined", "pure_ai", "long_hold"]
STRATEGY_LABELS = {
    "algorithm": "Algorithm",
    "machine_learning": "ML Old",
    "ml_new": "ML New",
    "combined": "Algorithm + ML",
    "pure_ai": "Pure AI",
    "long_hold": "Long Hold",
}

router = APIRouter()

_lock = threading.Lock()
_process: subprocess.Popen | None = None
_started_at: str | None = None
_last_command: list[str] = []
_last_log_path: Path | None = None
_last_output_base: Path = DEFAULT_OUTPUT_BASE

_status_cache: dict | None = None
_status_cache_ts: float = 0.0
_STATUS_CACHE_TTL: float = 2.0

_ps_cache: list = []
_ps_cache_ts: float = 0.0
_PS_CACHE_TTL: float = 15.0  # ps -ax is expensive; refresh at most every 15s


def _default_sms_number() -> str:
    return (
        os.getenv("PAPER_SMS_NUMBER")
        or os.getenv("TEXTNOW_PHONE")
        or os.getenv("TEXTNOW_ALERT_NUMBER")
        or os.getenv("SMS_NUMBER")
        or ""
    ).strip()


def _mask_phone(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) < 4:
        return "set" if value else ""
    return "*" * max(0, len(digits) - 4) + digits[-4:]


def _paper_runner_processes() -> list[dict[str, Any]]:
    """Return live paper runner processes, including ones orphaned by old servers."""
    global _ps_cache, _ps_cache_ts
    if _ps_cache_ts and (time.monotonic() - _ps_cache_ts) < _PS_CACHE_TTL:
        return _ps_cache
    try:
        output = subprocess.check_output(
            ["ps", "-axo", "pid=,ppid=,command="],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:
        return _ps_cache  # return stale on error

    script_abs = str(SCRIPT_PATH)
    script_rel = "scripts/paper_trade_today.py"
    current_pid = os.getpid()
    processes: list[dict[str, Any]] = []
    for line in output.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        command = parts[2]
        if pid == current_pid:
            continue
        if script_abs in command or script_rel in command:
            processes.append({"pid": pid, "ppid": ppid, "command": command})
    _ps_cache = processes
    _ps_cache_ts = time.monotonic()
    return processes


def _terminate_paper_runner_pids(pids: list[int]) -> list[int]:
    stopped: list[int] = []
    unique_pids = sorted({pid for pid in pids if pid > 0 and pid != os.getpid()})
    for pid in unique_pids:
        try:
            os.kill(pid, signal.SIGTERM)
            stopped.append(pid)
        except ProcessLookupError:
            stopped.append(pid)
        except Exception:
            pass

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        remaining = []
        for pid in unique_pids:
            try:
                os.kill(pid, 0)
                remaining.append(pid)
            except ProcessLookupError:
                pass
            except Exception:
                pass
        if not remaining:
            return stopped
        time.sleep(0.2)

    for pid in unique_pids:
        try:
            os.kill(pid, signal.SIGKILL)
            stopped.append(pid)
        except ProcessLookupError:
            pass
        except Exception:
            pass
    return sorted(set(stopped))

def _paper_runner_preexec() -> None:
    try:
        os.nice(10)
    except OSError:
        pass


class PaperStartRequest(BaseModel):
    starting_cash: float = Field(10000.0, ge=100.0, le=10_000_000.0)
    scan_interval_minutes: float = Field(15.0, ge=1.0, le=390.0)
    max_tickers: int = Field(0, ge=0, le=10000)
    openrouter_model: str = Field("openai/gpt-4o-mini", min_length=1, max_length=200)
    model_bundle: str = Field("ml_models/stock_universe/model_bundle.joblib", max_length=500)
    new_model_bundle: str = Field("ml_models/latest/model_bundle.joblib", max_length=500)
    ai_shortlist_size: int = Field(30, ge=1, le=200)
    ai_max_picks: int = Field(5, ge=1, le=50)
    include_pure_ai: bool = True
    reset: bool = False
    once: bool = False
    force: bool = False
    tickers: str = Field("all_tickers.txt", min_length=1, max_length=500)
    position_cap_pct: float = Field(25.0, ge=1.0, le=100.0)
    position_cap_min_pct: float = Field(10.0, ge=0.5, le=100.0)
    position_high_confidence_threshold: float = Field(0.80, ge=0.5, le=0.99)
    take_profit_pct: float = Field(0.0, ge=0.0, le=50.0)
    stop_loss_pct: float = Field(0.0, ge=0.0, le=50.0)
    partial_profit_pct: float = Field(0.5, ge=0.0, le=1.0)
    partial_profit_fraction: float = Field(0.5, ge=0.1, le=1.0)
    trailing_stop_atr_mult: float = Field(0.5, ge=0.0, le=5.0)
    time_decay_scans: int = Field(0, ge=0, le=200)
    sector_max_positions: int = Field(3, ge=0, le=20)
    daily_loss_limit_pct: float = Field(2.0, ge=0.0, le=50.0)
    risk_per_trade_pct: float = Field(0.0, ge=0.0, le=10.0)
    min_risk_reward: float = Field(1.3, ge=0.0, le=10.0)
    bear_regime_size_factor: float = Field(0.5, ge=0.0, le=1.0)
    neutral_regime_size_factor: float = Field(0.75, ge=0.0, le=1.0)
    max_positions: int = Field(5, ge=1, le=50)
    ml_probability_threshold: float = Field(0.72, ge=0.1, le=0.99)
    ml_large_loss_max: float = Field(0.20, ge=0.01, le=0.99)
    ml_expected_return_min: float = Field(0.0, ge=-0.5, le=0.5)
    target_mult: float = Field(1.5, ge=0.05, le=10.0)
    stop_mult: float = Field(1.0, ge=0.5, le=10.0)
    breadth_threshold: float = Field(0.40, ge=0.0, le=1.0)
    max_portfolio_drawdown: float = Field(0.05, ge=0.0, le=1.0)
    min_avg_volume: int = Field(500_000, ge=0, le=50_000_000)
    max_heat_pct: float = Field(80.0, ge=0.0, le=100.0)
    double_target_exit_pct: float = Field(0.5, ge=0.0, le=1.0)
    webhook_url: str = Field("", max_length=500)
    sms_number: str = Field("", max_length=32)
    hil_timeout_minutes: int = Field(15, ge=1, le=120)
    hil_auto_reject: bool = True
    sms_on_fills: bool = False
    hold_overnight: bool = True
    long_hold_days: int = Field(20, ge=1, le=365)
    trade_fidelity: bool = False
    trade_fidelity_execute: bool = False


class SmsTestRequest(BaseModel):
    phone: str = Field("", max_length=32)
    message: str = Field("TradingAgents TextNow test", min_length=1, max_length=240)


@router.get("/paper/sms/status")
async def sms_status():
    load_dotenv(ROOT / ".env", override=True)
    try:
        import playwright  # noqa: F401
        playwright_available = True
    except Exception:
        playwright_available = False
    phone = _default_sms_number()
    return {
        "provider": (os.getenv("SMS_PROVIDER") or "sendblue").strip().lower(),
        "sendblue_configured": bool(
            os.getenv("SENDBLUE_API_KEY_ID") and os.getenv("SENDBLUE_API_SECRET")
        ),
        "textbelt_key_set": bool(os.getenv("TEXTBELT_KEY")),
        "textnow_username_set": bool(os.getenv("TEXTNOW_USERNAME")),
        "textnow_sid_set": bool(os.getenv("TEXTNOW_SID")),
        "default_phone_set": bool(phone),
        "default_phone_masked": _mask_phone(phone),
        "playwright_available": playwright_available,
    }


class MmsTestRequest(BaseModel):
    to: str
    message: str


class EmailTestRequest(BaseModel):
    to: str
    subject: str = "Agentic Trader email test"
    message: str


@router.post("/paper/mms/test")
async def paper_mms_test(req: MmsTestRequest, admin: dict = Depends(require_admin)):
    try:
        from scripts.gmail_mms import send_gmail_mms
        result = send_gmail_mms(req.to, "TradingAgents Test", req.message)
        return result
    except Exception as e:
        import logging; logging.error(f"MMS Test Error: {e}")
        return {"success": False, "error": "An internal error occurred."}


@router.post("/paper/email/test")
async def paper_email_test(req: EmailTestRequest, admin: dict = Depends(require_admin)):
    try:
        from scripts.email_sender import send_email

        return send_email(req.to, req.subject, req.message)
    except Exception as e:
        import logging; logging.error(f"Email Test Error: {e}")
        return {"success": False, "error": "An internal error occurred."}

HIL_STATE_FILE = ROOT / "tmp" / "hil_state.json"

@router.get("/paper/hil/pending")
async def get_hil_pending():
    if HIL_STATE_FILE.exists():
        try:
            state = json.loads(HIL_STATE_FILE.read_text())
            if state.get("status") == "pending":
                return {"pending": True, "trade": state}
        except Exception:
            pass
    return {"pending": False}

from fastapi.responses import HTMLResponse

import yfinance as yf
import ssl

# Fix for macOS yfinance SSL issues
try:
    ssl._create_default_https_context = ssl._create_unverified_context
except Exception:
    pass

@router.get("/approve")
async def approve_page(request: Request, t: str = None):
    state = {}
    if HIL_STATE_FILE.exists():
        try:
            state = json.loads(HIL_STATE_FILE.read_text())
        except Exception:
            pass
            
    if not state or state.get("status") != "pending" or state.get("token") != t:
        return HTMLResponse(
            "<div style='font-family: sans-serif; text-align: center; padding: 50px; color: #ff4444;'>"
            "<h1>Unauthorized</h1><p>Invalid or expired trade security token.</p></div>", 
            status_code=403
        )

    ticker = state.get('ticker', 'AAPL')
    shares = int(state.get('shares', 0))
    entry_price = float(state.get('price', 0))
    
    # Calculate targets (Default to 2% profit, 1% loss if not provided)
    goal_price = state.get('goal', entry_price * 1.02)
    stop_price = state.get('stop', entry_price * 0.99)
    
    max_profit = (goal_price - entry_price) * shares
    max_loss = (entry_price - stop_price) * shares

    # Fetch recent price history for the chart
    chart_data = []
    try:
        yt = yf.Ticker(ticker)
        df = yt.history(period="1d", interval="5m") # Use 1d for simplicity
        
        if not df.empty:
            for idx, row in df.iterrows():
                try:
                    # Lightweight charts needs time in seconds or YYYY-MM-DD
                    chart_data.append({
                        "time": int(idx.timestamp()),
                        "open": float(row['Open']),
                        "high": float(row['High']),
                        "low": float(row['Low']),
                        "close": float(row['Close'])
                    })
                except Exception as e:
                    print(f"Row error: {e}")
            
            # Debug: Write the first 5 points to a file
            debug_path = Path("tmp/chart_debug.json")
            debug_path.write_text(json.dumps(chart_data[:5], indent=2))
        else:
            print(f"No history found for {ticker}")
            Path("tmp/chart_debug.json").write_text("EMPTY")
    except Exception as e:
        print(f"Chart download error: {e}")
        Path("tmp/chart_debug.json").write_text(f"ERROR: {e}")

    chart_data = sorted(chart_data, key=lambda x: x['time'])

    html = f"""
    <!DOCTYPE html>
    <html class="dark">
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <script src="https://cdn.tailwindcss.com"></script>
        <!-- Pinned to v4: v5 removed chart.addCandlestickSeries() (used below).
             jsdelivr fallback in case unpkg is network-blocked. -->
        <script src="https://unpkg.com/lightweight-charts@4.2.3/dist/lightweight-charts.standalone.production.js"></script>
        <script>
            window.LightweightCharts || document.write(
              '<scr'+'ipt src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.2.3/dist/lightweight-charts.standalone.production.js"><'+'/scr'+'ipt>');
        </script>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&family=JetBrains+Mono:wght@500;700&display=swap');
            body {{ font-family: 'Inter', sans-serif; background: radial-gradient(circle at top right, #1e1b4b, #020617); }}
            .glass {{ background: rgba(15, 23, 42, 0.6); backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.1); }}
            .mono {{ font-family: 'JetBrains Mono', monospace; }}
        </style>
    </head>
    <body class="text-slate-200 min-h-screen flex items-center justify-center p-4">
        <div class="glass rounded-3xl shadow-2xl max-w-2xl w-full overflow-hidden flex flex-col">
            <!-- Header -->
            <div class="px-8 py-6 border-b border-white/5 flex justify-between items-center bg-white/5">
                <div>
                    <h1 class="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
                        <span class="w-3 h-3 bg-indigo-500 rounded-full animate-pulse"></span>
                        Trade Authorization
                    </h1>
                    <p class="text-slate-400 text-sm">Reviewing signal for {ticker}</p>
                </div>
                <div class="px-3 py-1 rounded-full bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 text-xs font-bold tracking-widest uppercase">
                    HIL Mode
                </div>
            </div>

            <!-- Stats Grid -->
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-px bg-white/5">
                <div class="p-6 bg-slate-900/40">
                    <div class="text-[10px] text-slate-500 uppercase font-bold tracking-wider mb-1">Ticker</div>
                    <div class="text-2xl font-bold text-indigo-400 mono">{ticker}</div>
                </div>
                <div class="p-6 bg-slate-900/40">
                    <div class="text-[10px] text-slate-500 uppercase font-bold tracking-wider mb-1">Quantity</div>
                    <div class="text-2xl font-bold text-white mono">{shares}</div>
                </div>
                <div class="p-6 bg-slate-900/40">
                    <div class="text-[10px] text-slate-500 uppercase font-bold tracking-wider mb-1">Max Profit</div>
                    <div class="text-2xl font-bold text-emerald-400 mono">+${max_profit:.2f}</div>
                </div>
                <div class="p-6 bg-slate-900/40">
                    <div class="text-[10px] text-slate-500 uppercase font-bold tracking-wider mb-1">Max Loss</div>
                    <div class="text-2xl font-bold text-rose-400 mono">-${max_loss:.2f}</div>
                </div>
            </div>

            <!-- Chart Container -->
            <div class="p-4 bg-black/20">
                <div id="chart" class="w-full h-80 rounded-xl overflow-hidden border border-white/5 shadow-inner flex items-center justify-center">
                    <div id="chart-loading" class="text-slate-500 text-sm animate-pulse">Loading market data...</div>
                </div>
            </div>

            <!-- Controls -->
            <div class="p-8 bg-white/5 flex flex-col gap-6">
                <div class="flex items-center justify-between text-sm px-2">
                    <div class="flex items-center gap-4 text-slate-400">
                        <span class="flex items-center gap-1.5"><span class="w-2 h-2 rounded-full bg-slate-400"></span> Entry: ${entry_price:.2f}</span>
                        <span class="flex items-center gap-1.5"><span class="w-2 h-2 rounded-full bg-emerald-500"></span> Goal: ${goal_price:.2f}</span>
                        <span class="flex items-center gap-1.5"><span class="w-2 h-2 rounded-full bg-rose-500"></span> Stop: ${stop_price:.2f}</span>
                    </div>
                </div>

                <div class="flex gap-4" id="buttons">
                    <button onclick="resolve('reject')" class="flex-1 py-4 rounded-2xl font-bold bg-slate-800 hover:bg-slate-700 text-slate-300 transition-all border border-white/5">
                        Decline Trade
                    </button>
                    <button onclick="resolve('approve')" class="flex-1 py-4 rounded-2xl font-bold bg-indigo-600 hover:bg-indigo-500 text-white shadow-lg shadow-indigo-500/20 transition-all active:scale-[0.98]">
                        Execute Order
                    </button>
                </div>
                <div id="result" class="py-4 text-center hidden"></div>
            </div>
        </div>

        <script>
            // Initialize Chart
            console.log('Initializing chart...');
            const chartElement = document.getElementById('chart');
            const data = {json.dumps(chart_data)};
            console.log('Chart data points:', data.length);
            
            if (typeof LightweightCharts === 'undefined') {{
                const el = document.getElementById('chart-loading');
                el.innerHTML = 'Chart library blocked by network. Approve/Reject still works.';
                el.classList.remove('animate-pulse');
            }} else if (data.length > 0) {{
              try {{
                document.getElementById('chart-loading').style.display = 'none';
                chartElement.classList.remove('items-center', 'justify-center');

                const chart = LightweightCharts.createChart(chartElement, {{
                    layout: {{
                        background: {{ color: 'transparent' }},
                        textColor: '#94a3b8',
                        fontSize: 10,
                    }},
                    grid: {{
                        vertLines: {{ color: 'rgba(255,255,255,0.02)' }},
                        horzLines: {{ color: 'rgba(255,255,255,0.02)' }},
                    }},
                    rightPriceScale: {{
                        borderColor: 'rgba(255,255,255,0.1)',
                        autoScale: true,
                    }},
                    timeScale: {{
                        borderColor: 'rgba(255,255,255,0.1)',
                        timeVisible: true,
                    }},
                }});

                const candlestickSeries = chart.addCandlestickSeries({{
                    upColor: '#10b981',
                    downColor: '#f43f5e',
                    borderVisible: false,
                    wickUpColor: '#10b981',
                    wickDownColor: '#f43f5e',
                }});

                candlestickSeries.setData(data);

                // Add Target Lines
                const entryLine = {{
                    price: {entry_price},
                    color: '#94a3b8',
                    lineWidth: 1,
                    lineStyle: 2,
                    axisLabelVisible: true,
                    title: 'ENTRY',
                }};
                const goalLine = {{
                    price: {goal_price},
                    color: '#10b981',
                    lineWidth: 2,
                    lineStyle: 0,
                    axisLabelVisible: true,
                    title: 'GOAL',
                }};
                const stopLine = {{
                    price: {stop_price},
                    color: '#f43f5e',
                    lineWidth: 2,
                    lineStyle: 0,
                    axisLabelVisible: true,
                    title: 'STOP',
                }};

                candlestickSeries.createPriceLine(entryLine);
                candlestickSeries.createPriceLine(goalLine);
                candlestickSeries.createPriceLine(stopLine);

                chart.timeScale().fitContent();
                
                window.addEventListener('resize', () => {{
                    chart.applyOptions({{ width: chartElement.clientWidth }});
                }});
              }} catch (err) {{
                console.error('Chart render failed:', err);
                const el = document.getElementById('chart-loading');
                el.style.display = '';
                el.innerHTML = 'Chart unavailable (' + err.message + '). Approve/Reject still works.';
                el.classList.remove('animate-pulse');
              }}
            }} else {{
                document.getElementById('chart-loading').innerHTML = 'No market data available for this ticker.';
                document.getElementById('chart-loading').classList.remove('animate-pulse');
            }}

            async function resolve(action) {{
                const btns = document.getElementById('buttons');
                btns.style.opacity = '0.5';
                btns.style.pointerEvents = 'none';
                try {{
                    const res = await fetch('/api/paper/hil/resolve', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ id: "{state.get('id')}", action: action }})
                    }});
                    btns.style.display = 'none';
                    const result = document.getElementById('result');
                    result.style.display = 'block';
                    result.innerHTML = action === 'approve' 
                        ? '<div class="p-4 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 font-bold">✓ Trade Executed. You can close this page.</div>'
                        : '<div class="p-4 rounded-xl bg-slate-500/10 border border-slate-500/20 text-slate-400 font-bold">✕ Trade Canceled.</div>';
                }} catch(e) {{
                    alert('Error: ' + e);
                }}
            }}

            window.addEventListener('resize', () => {{
                chart.applyOptions({{ width: chartElement.clientWidth }});
            }});
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html)

class HilResolveRequest(BaseModel):
    id: str
    action: str

@router.post("/paper/hil/resolve")
async def resolve_hil(req: HilResolveRequest, admin: dict = Depends(require_admin)):
    if HIL_STATE_FILE.exists():
        try:
            state = json.loads(HIL_STATE_FILE.read_text())
            if state.get("id") == req.id and state.get("status") == "pending":
                state["status"] = "approved" if req.action == "approve" else "rejected"
                HIL_STATE_FILE.write_text(json.dumps(state))
                return {"success": True}
        except Exception:
            pass
    return {"success": False, "error": "No pending trade found"}


_APPROVE_WORDS = {"y", "yes", "yep", "yeah", "ok", "okay", "approve", "approved",
                  "confirm", "confirmed", "go", "buy", "accept", "👍", "✅", "👌", "💯"}
_REJECT_WORDS = {"n", "no", "nope", "reject", "rejected", "cancel", "cancelled",
                 "stop", "skip", "deny", "abort", "decline", "👎", "❌", "🚫", "✋"}


def _classify_reply(text: str) -> str | None:
    """Map an inbound SMS/iMessage body or reaction to approve/reject/None."""
    if not text:
        return None
    t = text.strip().lower()
    # whole-message exact match first (safest)
    if t in _APPROVE_WORDS:
        return "approve"
    if t in _REJECT_WORDS:
        return "reject"
    # token scan: first decisive word wins; reject beats approve on ties
    toks = [w.strip(".,!?:;'\"()") for w in t.split()]
    has_rej = any(w in _REJECT_WORDS for w in toks)
    has_app = any(w in _APPROVE_WORDS for w in toks)
    # emoji can be glued to text (no whitespace) — substring check for those
    if not has_rej:
        has_rej = any(e in text for e in ("👎", "❌", "🚫", "✋"))
    if not has_app:
        has_app = any(e in text for e in ("👍", "✅", "👌", "💯"))
    if has_rej:
        return "reject"
    if has_app:
        return "approve"
    return None


@router.post("/paper/sms/inbound")
async def sendblue_inbound(request: Request):
    """Sendblue inbound webhook for two-way SMS commands.

    Trade approvals are resolved in the authenticated dashboard, not by SMS
    reply. This route is kept for STATUS/POSITIONS/HIL/HELP style commands.
    Secured by the SENDBLUE_INBOUND_SECRET shared key (?key=... or
    X-Inbound-Key header); Basic Auth is bypassed for this route in app.py
    since Sendblue cannot send credentials.
    """
    # Diagnostic: record every inbound hit (key presence only, never the value)
    # so we can confirm Sendblue is actually delivering to this webhook.
    try:
        import datetime as _dt
        _log = ROOT / "tmp" / "sms_inbound.log"
        _log.parent.mkdir(exist_ok=True)
        with _log.open("a", encoding="utf-8") as _f:
            _f.write(f"{_dt.datetime.now().isoformat()} HIT key_present="
                     f"{bool(request.query_params.get('key') or request.headers.get('x-inbound-key'))} "
                     f"ua={request.headers.get('user-agent','')[:60]}\n")
    except Exception:
        pass

    secret = os.getenv("SENDBLUE_INBOUND_SECRET", "").strip()
    if secret:
        provided = (
            request.query_params.get("key")
            or request.headers.get("x-inbound-key", "")
        ).strip()
        if provided != secret:
            return {"success": False, "error": "unauthorized"}

    # Sendblue posts JSON; tolerate form/garbage without 500ing.
    try:
        payload = await request.json()
    except Exception:
        try:
            payload = dict(await request.form())
        except Exception:
            payload = {}

    if not isinstance(payload, dict):
        payload = {}

    # Ignore our own outbound echoes.
    if payload.get("is_outbound") in (True, "true", "True", 1, "1"):
        return {"success": True, "ignored": "outbound"}

    content = str(
        payload.get("content")
        or payload.get("message")
        or payload.get("text")
        or ""
    )
    # Approval/rejection replies are intentionally ignored; approval happens
    # inside the Cloudflare-protected dashboard. This router handles STATUS,
    # POSITIONS, HIL, HELP, STOP, START, WHOAMI, ROLE, etc.
    from web.sms_router import dispatch
    from scripts.sms_alerts import send_sms

    from_number = str(payload.get("from_number") or payload.get("number") or "")
    result = dispatch(from_number, content)
    reply = result.get("reply") or ""
    if reply and from_number:
        try:
            await asyncio.to_thread(send_sms, from_number, reply)
        except Exception as exc:
            import logging; logging.error(f"Send error: {exc}")
            result["send_error"] = "An internal error occurred."
    return {"success": True, "router": result}


@router.post("/paper/sms/test")
async def test_sms(body: SmsTestRequest, admin: dict = Depends(require_admin)):
    load_dotenv(ROOT / ".env", override=True)
    phone = (body.phone or _default_sms_number()).strip()
    if not phone:
        return {
            "success": False,
            "error": "No phone number set. Add TEXTNOW_PHONE or PAPER_SMS_NUMBER in .env, or pass phone in the test.",
        }
    try:
        from scripts.sms_alerts import send_sms

        result = await asyncio.to_thread(send_sms, phone, body.message)
        return {**result, "phone_masked": _mask_phone(phone)}
    except Exception as exc:
        import logging; logging.error(f"Send test alert error: {exc}")
        return {"success": False, "error": "An internal error occurred.", "phone_masked": _mask_phone(phone)}


def _ny_today() -> dt.date:
    return dt.datetime.now(ZoneInfo("America/New_York")).date()


def _day_dir(base: Path | None = None) -> Path:
    return (base or _last_output_base) / _ny_today().strftime("%Y%m%d")


def _has_account_state(path: Path) -> bool:
    return any((path / strategy / "state.json").exists() for strategy in STRATEGIES)


def _latest_data_dir() -> Path:
    today = _day_dir()
    if today.exists() and _has_account_state(today):
        return today
    if not _last_output_base.exists():
        return today
    dated = [
        path
        for path in _last_output_base.iterdir()
        if path.is_dir() and path.name.isdigit() and len(path.name) == 8
    ]
    with_state = [path for path in dated if _has_account_state(path)]
    if with_state:
        return sorted(with_state, key=lambda p: p.name)[-1]
    if not dated:
        return today
    return sorted(dated, key=lambda p: p.name)[-1]


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        import logging; logging.error(f"Log load error: {exc}")
        return {"error": "An internal error occurred.", "path": str(path)}


def _tail_text(path: Path, limit: int = 80) -> list[str]:
    if not path.exists():
        return []
    try:
        chunk = 65536  # 64 KB — enough for 80 log lines
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - chunk))
            raw = f.read()
        lines = raw.decode("utf-8", errors="replace").splitlines()
        # Drop first line if we seeked mid-line
        if size > chunk and len(lines) > 1:
            lines = lines[1:]
        return lines[-limit:]
    except Exception as exc:
        import logging; logging.error(f"Tail text error for {path.name}: {exc}")
        return [f"Unable to read {path.name}"]


def _tail_jsonl(path: Path, limit: int = 20) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in _tail_text(path, limit):
        try:
            rows.append(json.loads(line))
        except Exception:
            rows.append({"type": "LOG", "message": line})
    return rows


def _read_candidates(path: Path, limit: int = 25) -> dict[str, Any]:
    if not path.exists():
        return {"count": 0, "rows": []}
    try:
        with path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        return {"count": len(rows), "rows": rows[:limit]}
    except Exception as exc:
        import logging; logging.error(f"Order load error: {exc}")
        return {"count": 0, "rows": [], "error": "An internal error occurred."}


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}{'*' * min(len(value) - 8, 20)}{value[-4:]}"


def _openrouter_status() -> dict[str, Any]:
    load_dotenv(ROOT / ".env", override=True)
    value = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_KEY") or ""
    return {"set": bool(value), "masked": _mask(value)}


def _process_status() -> dict[str, Any]:
    global _process
    with _lock:
        proc = _process
        external = _paper_runner_processes()
        if proc is not None and proc.poll() is not None:
            return_code = proc.returncode
            _process = None
            if external:
                return {
                    "running": True,
                    "pid": external[0]["pid"],
                    "external_pids": [p["pid"] for p in external],
                    "return_code": return_code,
                }
            return {"running": False, "return_code": return_code}
        if proc is None:
            if external:
                return {
                    "running": True,
                    "pid": external[0]["pid"],
                    "external_pids": [p["pid"] for p in external],
                    "return_code": None,
                }
            return {"running": False, "return_code": None}
        external_pids = [p["pid"] for p in external if p["pid"] != proc.pid]
        status = {"running": True, "pid": proc.pid, "return_code": None}
        if external_pids:
            status["external_pids"] = external_pids
        return status


def _collect_account(strategy: str, data_dir: Path) -> dict[str, Any]:
    strategy_dir = data_dir / strategy
    summary = _read_json(strategy_dir / "summary.json")
    state = _read_json(strategy_dir / "state.json")
    candidates = _read_candidates(data_dir / f"{strategy}_candidates.csv")
    events = _tail_jsonl(strategy_dir / "events.jsonl")

    if summary is None and isinstance(state, dict):
        positions = state.get("positions") or {}
        trades = state.get("trades") or []
        cash = float(state.get("cash") or 0)
        pos_value = sum(
            float(p.get("entry_price", 0)) * int(p.get("shares", 0))
            for p in positions.values()
        )
        summary = {
            "strategy": strategy,
            "strategy_label": STRATEGY_LABELS.get(strategy, strategy),
            "starting_cash": state.get("starting_cash"),
            "cash": state.get("cash"),
            "realized_pnl": state.get("realized_pnl"),
            "total_value": round(cash + pos_value, 2),
            "open_positions": list(positions.values()),
            "trades_closed": len(trades),
            "candidates": candidates["count"],
        }

    if isinstance(summary, dict) and "candidates" not in summary:
        summary["candidates"] = candidates["count"]

    return {
        "strategy": strategy,
        "label": STRATEGY_LABELS.get(strategy, strategy),
        "summary": summary,
        "state": state,
        "candidates": candidates,
        "events": events,
    }


@router.get("/paper/status")
async def paper_status(force: bool = False):
    global _status_cache, _status_cache_ts
    import time
    now = time.monotonic()
    if not force and _status_cache is not None and (now - _status_cache_ts) < _STATUS_CACHE_TTL:
        return _status_cache

    proc = _process_status()
    data_dir = _latest_data_dir()
    log_path = _last_log_path or data_dir / "web_runner.log"
    accounts = [_collect_account(strategy, data_dir) for strategy in STRATEGIES]
    fallback_cash = next(
        (
            float(account["summary"]["starting_cash"])
            for account in accounts
            if isinstance(account.get("summary"), dict)
            and account["summary"].get("starting_cash") is not None
        ),
        10000.0,
    )
    for account in accounts:
        if account.get("summary") is None:
            account["summary"] = {
                "strategy": account["strategy"],
                "strategy_label": account["label"],
                "starting_cash": fallback_cash,
                "cash": fallback_cash,
                "total_value": fallback_cash,
                "realized_pnl": 0.0,
                "open_positions": [],
                "trades_closed": 0,
                "candidates": account["candidates"]["count"],
                "not_started": True,
            }
    result = {
        "process": {
            **proc,
            "started_at": _started_at,
            "command": _last_command,
            "log_path": str(log_path),
        },
        "date": data_dir.name,
        "data_dir": str(data_dir),
        "data_dir_exists": data_dir.exists(),
        "openrouter": _openrouter_status(),
        "accounts": accounts,
        "end_of_day": _read_json(data_dir / "end_of_day_statistics.json"),
        "log_lines": _tail_text(log_path),
    }
    _status_cache = result
    _status_cache_ts = now
    return result


@router.post("/paper/start")
async def start_paper_runner(body: PaperStartRequest, admin: dict = Depends(require_admin)):
    global _process, _started_at, _last_command, _last_log_path, _last_output_base

    with _lock:
        if _process is not None and _process.poll() is None:
            return {"success": False, "running": True, "pid": _process.pid, "error": "Paper runner is already running"}

        external = _paper_runner_processes()
        if external:
            return {
                "success": False,
                "running": True,
                "pid": external[0]["pid"],
                "external_pids": [p["pid"] for p in external],
                "error": "Paper runner is already running outside this web process",
            }

        if not SCRIPT_PATH.exists():
            return {"success": False, "running": False, "error": f"Missing script: {SCRIPT_PATH}"}

        load_dotenv(ROOT / ".env", override=True)
        _last_output_base = DEFAULT_OUTPUT_BASE
        today = _ny_today()
        if not body.force and today.weekday() >= 5:
            return {
                "success": False,
                "running": False,
                "error": f"{today.isoformat()} is not a regular weekday market day. Enable Force for a smoke run.",
            }
        day_dir = _day_dir(_last_output_base)
        day_dir.mkdir(parents=True, exist_ok=True)
        log_path = day_dir / "web_runner.log"

        command = [
            sys.executable,
            "-W", "ignore::UserWarning:sklearn",
            "-W", "ignore::UserWarning:joblib",
            "-W", "ignore::ResourceWarning",
            str(SCRIPT_PATH),
            "--tickers",
            body.tickers,
            "--starting-cash",
            str(body.starting_cash),
            "--scan-interval-minutes",
            str(body.scan_interval_minutes),
            "--output-dir",
            str(_last_output_base),
            "--openrouter-model",
            body.openrouter_model,
            "--ai-shortlist-size",
            str(body.ai_shortlist_size),
            "--ai-max-picks",
            str(body.ai_max_picks),
            "--max-tickers",
            str(body.max_tickers),
            "--position-cap-pct",
            str(body.position_cap_pct),
            "--position-cap-min-pct",
            str(body.position_cap_min_pct),
            "--position-high-confidence-threshold",
            str(body.position_high_confidence_threshold),
            "--take-profit-pct",
            str(body.take_profit_pct),
            "--stop-loss-pct",
            str(body.stop_loss_pct),
            "--partial-profit-pct",
            str(body.partial_profit_pct),
            "--partial-profit-fraction",
            str(body.partial_profit_fraction),
            "--trailing-stop-atr-mult",
            str(body.trailing_stop_atr_mult),
            "--time-decay-scans",
            str(body.time_decay_scans),
            "--sector-max-positions",
            str(body.sector_max_positions),
            "--daily-loss-limit-pct",
            str(body.daily_loss_limit_pct),
            "--risk-per-trade-pct",
            str(body.risk_per_trade_pct),
            "--min-risk-reward",
            str(body.min_risk_reward),
            "--bear-regime-size-factor",
            str(body.bear_regime_size_factor),
            "--neutral-regime-size-factor",
            str(body.neutral_regime_size_factor),
            "--max-positions",
            str(body.max_positions),
            "--ml-probability-threshold",
            str(body.ml_probability_threshold),
            "--ml-large-loss-max",
            str(body.ml_large_loss_max),
            "--ml-expected-return-min",
            str(body.ml_expected_return_min),
            "--target-mult",
            str(body.target_mult),
            "--stop-mult",
            str(body.stop_mult),
            "--breadth-threshold",
            str(body.breadth_threshold),
            "--max-portfolio-drawdown",
            str(body.max_portfolio_drawdown),
            "--min-avg-volume",
            str(body.min_avg_volume),
            "--max-heat-pct",
            str(body.max_heat_pct),
            "--double-target-exit-pct",
            str(body.double_target_exit_pct),
            "--ml-algo-only",
            "--no-dashboard",
        ]
        command.extend(["--hil-timeout-minutes", str(body.hil_timeout_minutes)])
        if not body.hil_auto_reject:
            command.append("--hil-auto-approve")
        if body.sms_on_fills:
            command.append("--sms-on-fills")
        command.append("--hold-overnight" if body.hold_overnight else "--no-hold-overnight")
        if body.model_bundle:
            command.extend(["--model-bundle", body.model_bundle])
        if body.new_model_bundle:
            command.extend(["--new-model-bundle", body.new_model_bundle])
        if not body.include_pure_ai:
            command.append("--no-ai")
        if body.reset:
            command.append("--reset")
        if body.once:
            command.append("--once")
        if body.force:
            command.append("--force")
        if body.webhook_url:
            command.extend(["--webhook-url", body.webhook_url])
        sms_number = (body.sms_number or _default_sms_number()).strip()
        if sms_number:
            command.extend(["--sms-number", sms_number])
        command.extend(["--long-hold-days", str(body.long_hold_days)])
        if body.trade_fidelity:
            command.append("--trade-fidelity")
        if body.trade_fidelity_execute:
            command.append("--trade-fidelity-execute")

        safe_command = [
            Path(part).name if index == 0 else part
            for index, part in enumerate(command)
        ]
        log_header = {
            "timestamp": dt.datetime.now().isoformat(),
            "command": safe_command,
        }
        with log_path.open("a", encoding="utf-8") as f:
            f.write("\n=== Web paper runner start ===\n")
            f.write(json.dumps(log_header) + "\n")

        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        env = os.environ.copy()
        for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            env.setdefault(name, "1")
        log_handle = log_path.open("a", encoding="utf-8", buffering=1)
        try:
            _process = subprocess.Popen(
                command,
                cwd=str(ROOT),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                creationflags=flags,
                preexec_fn=_paper_runner_preexec if os.name != "nt" else None,
                text=True,
            )
        finally:
            log_handle.close()

        _started_at = dt.datetime.now().isoformat()
        _last_command = safe_command
        _last_log_path = log_path
        return {"success": True, "running": True, "pid": _process.pid, "log_path": str(log_path)}


@router.get("/paper/autostart")
async def get_autostart():
    if AUTOSTART_CONFIG_PATH.exists():
        try:
            saved = json.loads(AUTOSTART_CONFIG_PATH.read_text(encoding="utf-8-sig"))
            return {**DEFAULT_AUTOSTART_CONFIG, **saved}
        except Exception:
            pass
    return DEFAULT_AUTOSTART_CONFIG.copy()


@router.post("/paper/autostart")
async def set_autostart(body: dict, admin: dict = Depends(require_admin)):
    AUTOSTART_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config = {**DEFAULT_AUTOSTART_CONFIG, **body}
    AUTOSTART_CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return {"success": True, "config": config}


@router.get("/paper/analytics")
async def paper_analytics():
    import math
    data_dir = _latest_data_dir()
    result = {}
    for strategy in STRATEGIES:
        state_path = data_dir / strategy / "state.json"
        drift_path = data_dir / strategy / "ml_drift.json"
        if not state_path.exists():
            result[strategy] = {"no_data": True}
            continue
        state = _read_json(state_path) or {}
        trades = state.get("trades") or []
        if not trades:
            result[strategy] = {"trades": 0}
            continue

        pnls = [float(t.get("pnl", 0)) for t in trades]
        pnl_pcts = [float(t.get("pnl_pct", 0)) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        win_rate = len(wins) / len(trades) if trades else 0

        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        profit_factor = abs(sum(wins) / sum(losses)) if sum(losses) != 0 else None

        hold_times = []
        for t in trades:
            try:
                entry = dt.datetime.fromisoformat(t["entry_time"])
                exit_ = dt.datetime.fromisoformat(t["exit_time"])
                hold_times.append((exit_ - entry).total_seconds() / 3600)
            except Exception:
                pass
        avg_hold_h = sum(hold_times) / len(hold_times) if hold_times else None

        # Sharpe (daily returns proxy from pnl_pct)
        sharpe = None
        if len(pnl_pcts) >= 5:
            mean_r = sum(pnl_pcts) / len(pnl_pcts)
            variance = sum((r - mean_r) ** 2 for r in pnl_pcts) / len(pnl_pcts)
            std_r = math.sqrt(variance) if variance > 0 else 0
            sharpe = round(mean_r / std_r * math.sqrt(252), 4) if std_r > 0 else None

        # Max drawdown on cumulative pnl
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        start_cash = float(state.get("starting_cash", 1))
        for p in pnls:
            cumulative += p
            if cumulative > peak:
                peak = cumulative
            dd = (peak - cumulative) / start_cash if start_cash else 0
            if dd > max_dd:
                max_dd = dd

        ml_drift = _read_json(drift_path) if drift_path.exists() else None

        result[strategy] = {
            "trades": len(trades),
            "win_rate": round(win_rate, 4),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 4) if profit_factor else None,
            "total_pnl": round(sum(pnls), 2),
            "avg_hold_hours": round(avg_hold_h, 2) if avg_hold_h is not None else None,
            "sharpe": sharpe,
            "max_drawdown_pct": round(max_dd * 100, 2),
            "ml_drift": ml_drift,
        }
    return result


@router.get("/paper/equity")
async def paper_equity():
    data_dir = _latest_data_dir()
    result = {}
    for strategy in STRATEGIES:
        path = data_dir / strategy / "equity_curve.jsonl"
        rows: list[dict] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
        result[strategy] = rows
    return result


@router.get("/paper/system-health")
async def paper_system_health():
    import sqlite3 as _sqlite3
    data_dir = _latest_data_dir()
    health: dict = {"data_dir": str(data_dir), "strategies": {}}

    # Model bundle info
    model_paths = [
        ROOT / "ml_models" / "stock_universe" / "model_bundle.joblib",
        ROOT / "ml_models" / "latest" / "model_bundle.joblib",
    ]
    bundle_info = {"found": False, "path": None, "created_at": None}
    for mp in model_paths:
        if mp.exists():
            report_path = mp.parent / "training_report.json"
            created_at = None
            features = None
            if report_path.exists():
                try:
                    rpt = json.loads(report_path.read_text())
                    created_at = rpt.get("settings", {}).get("source") or None
                    features = rpt.get("settings", {}).get("feature_count")
                except Exception:
                    pass
            import os
            mtime = os.path.getmtime(str(mp))
            import datetime as _dt
            bundle_info = {
                "found": True,
                "path": str(mp),
                "modified_at": _dt.datetime.fromtimestamp(mtime).isoformat(),
                "feature_count": features,
            }
            break
    health["model_bundle"] = bundle_info

    # Per-strategy: journal trade count + drift summary
    for strategy in STRATEGIES:
        s_dir = data_dir / strategy
        info: dict = {"strategy": strategy}
        journal_path = s_dir / "trades_journal.db"
        if journal_path.exists():
            try:
                with closing(_sqlite3.connect(str(journal_path))) as conn:
                    count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
                info["journal_trades"] = count
            except Exception:
                info["journal_trades"] = None
        drift_path = s_dir / "ml_drift.json"
        info["ml_drift"] = _read_json(drift_path)
        equity_path = s_dir / "equity_curve.jsonl"
        if equity_path.exists():
            lines = equity_path.read_text(encoding="utf-8").splitlines()
            info["equity_points"] = len(lines)
            if lines:
                try:
                    last = json.loads(lines[-1])
                    info["last_value"] = last.get("v")
                    info["last_update"] = last.get("t")
                except Exception:
                    pass
        health["strategies"][strategy] = info

    # Backtest index summary
    idx_db = ROOT / "backtest_index.db"
    if idx_db.exists():
        try:
            with closing(_sqlite3.connect(str(idx_db))) as conn:
                row = conn.execute("SELECT COUNT(*), MAX(run_at) FROM runs").fetchone()
            health["backtest_index"] = {"total_runs": row[0], "last_run": row[1]}
        except Exception:
            health["backtest_index"] = {"total_runs": 0, "last_run": None}
    else:
        health["backtest_index"] = {"total_runs": 0, "last_run": None}

    return health


@router.get("/paper/backtest-index")
async def backtest_index():
    import sqlite3 as _sqlite3
    db_path = ROOT / "backtest_index.db"
    if not db_path.exists():
        return {"runs": [], "message": "No backtest runs indexed yet."}
    try:
        with closing(_sqlite3.connect(str(db_path))) as conn:
            conn.row_factory = _sqlite3.Row
            rows = conn.execute("SELECT * FROM runs ORDER BY run_at DESC LIMIT 100").fetchall()
        return {"runs": [dict(r) for r in rows]}
    except Exception as exc:
        import logging; logging.error(f"List logs error: {exc}")
        return {"runs": [], "error": "An internal error occurred."}


@router.get("/paper/candidates-history")
async def candidates_history(days: int = 7, limit: int = 500):
    """Return candidates logged across past N days from persistent history files."""
    base = _last_output_base
    rows: list[dict] = []
    limit = max(1, min(int(limit), 500))
    cutoff = (dt.datetime.now() - dt.timedelta(days=days)).strftime("%Y-%m-%d")
    for strategy in STRATEGIES:
        log_path = base / f"{strategy}_candidates_history.jsonl"
        if not log_path.exists():
            continue
        try:
            with log_path.open("r", encoding="utf-8") as handle:
                recent_lines = deque(handle, maxlen=limit * 3)
            for line in recent_lines:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("scan_date", "9999") >= cutoff:
                        rows.append({
                            "scan_dt": obj.get("scan_dt", ""),
                            "scan_date": obj.get("scan_date", ""),
                            "strategy": obj.get("strategy", strategy),
                            "ticker": obj.get("ticker", ""),
                            "entry": obj.get("entry"),
                            "target": obj.get("target"),
                            "stop": obj.get("stop"),
                            "ml_probability": obj.get("ml_probability"),
                            "expected_return": obj.get("expected_return"),
                            "rule_pass": obj.get("rule_pass"),
                            "gate_status": str(obj.get("gate_status", ""))[:160],
                            "ai_reason": str(obj.get("ai_reason", ""))[:160],
                        })
                except Exception:
                    pass
        except Exception:
            pass
    rows.sort(key=lambda r: r.get("scan_dt", ""), reverse=True)
    return {"rows": rows[:limit], "total": len(rows)}


@router.get("/paper/quotes")
async def paper_quotes(tickers: str = ""):
    """Return latest price + day change for a comma-separated list of tickers."""
    import yfinance as yf
    if not tickers:
        return {}
    symbols = [t.strip().upper() for t in tickers.split(",") if t.strip()][:40]
    result: dict[str, dict] = {}
    try:
        raw = yf.download(
            tickers=symbols,
            period="2d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        try:
            _close = raw["Close"]
            closes = _close if hasattr(_close, "columns") else None
        except Exception:
            closes = None
        for sym in symbols:
            try:
                if closes is not None and sym in closes.columns:
                    series = closes[sym].dropna()
                elif closes is None and len(symbols) == 1:
                    series = raw["Close"].dropna()
                else:
                    series = None
                if series is None or len(series) < 1:
                    result[sym] = {}
                    continue
                price = float(series.iloc[-1])
                prev = float(series.iloc[-2]) if len(series) >= 2 else price
                chg_pct = (price - prev) / prev if prev else 0
                result[sym] = {"price": round(price, 4), "chg_pct": round(chg_pct, 6), "prev": round(prev, 4)}
            except Exception:
                result[sym] = {}
    except Exception as exc:
        import logging; logging.error(f"Read params error: {exc}")
        return {"error": "An internal error occurred."}
    return result


@router.post("/paper/stop")
async def stop_paper_runner(admin: dict = Depends(require_admin)):
    global _process
    stopped_pids: list[int] = []
    with _lock:
        proc = _process
        external_pids = [p["pid"] for p in _paper_runner_processes()]
        if proc is None or proc.poll() is not None:
            _process = None
            stopped_pids = _terminate_paper_runner_pids(external_pids)
            return {"success": True, "running": False, "stopped_pids": stopped_pids}

        pids = external_pids + [proc.pid]
        stopped_pids = _terminate_paper_runner_pids(pids)

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass

    with _lock:
        _process = None
    return {
        "success": True,
        "running": False,
        "return_code": proc.returncode,
        "stopped_pids": stopped_pids,
    }
