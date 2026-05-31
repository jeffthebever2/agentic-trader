#!/usr/bin/env python3
"""
ta — TradingAgents Operational Toolkit
Fast commands for server, paper trading, ML, frontend, market data, and system status.

Usage:
  ta status                    # full system health dashboard
  ta server start|stop|status  # manage the FastAPI/uvicorn server
  ta build [--watch]           # build React frontend
  ta dev                       # start vite dev server + uvicorn together
  ta paper status|positions|candidates|start|stop
  ta ml status|train|predict TICKER
  ta backtest run|results
  ta ticker SYMBOL [SYMBOL...]  # quick quote(s)
  ta scan SYMBOL... [--threshold N]
  ta history [--ticker T] [--days N] [--export]
  ta hil pending|approve ID|reject ID
  ta logs [server|paper|ml] [--lines N]
  ta db backup|stats
"""
from __future__ import annotations

import os
import sys
import json
import subprocess
import time
import signal
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List

import typer
import requests
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.rule import Rule
from rich import box
from rich.text import Text
from rich.live import Live
from rich.layout import Layout
from dotenv import load_dotenv

# ── Config ────────────────────────────────────────────────────────────────────
ROOT       = Path(__file__).parent
FRONTEND   = ROOT / "frontend"
DIST       = ROOT / "web" / "static" / "dist"
PID_FILE   = ROOT / ".ta_server.pid"
LOG_FILE   = ROOT / ".ta_server.log"
ENV_FILE   = ROOT / ".env"
DEFAULT_PORT = int(os.getenv("TA_PORT", "8001"))
BASE_URL   = os.getenv("TA_API_URL", f"http://127.0.0.1:{DEFAULT_PORT}")

load_dotenv(ENV_FILE, override=True)
load_dotenv(ROOT / ".env.enterprise", override=False)

console = Console()
app     = typer.Typer(
    name="ta",
    help="TradingAgents operational toolkit — fast commands for every subsystem.",
    add_completion=True,
    invoke_without_command=True,
    no_args_is_help=True,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _api(method: str, path: str, **kwargs):
    """Fire API request; return response or None on error."""
    url = f"{BASE_URL}{path}"
    try:
        r = getattr(requests, method)(url, timeout=10, **kwargs)
        r.raise_for_status()
        return r
    except requests.exceptions.ConnectionError:
        return None
    except requests.exceptions.HTTPError as e:
        return e.response
    except Exception:
        return None

def _get(path: str, **kw):  return _api("get",    path, **kw)
def _post(path: str, **kw): return _api("post",   path, **kw)

def _server_running() -> bool:
    r = _get("/health")
    return r is not None and r.status_code < 500

def _fmt_pct(v, decimals=2):
    if v is None: return "—"
    color = "green" if v >= 0 else "red"
    return f"[{color}]{v:+.{decimals}f}%[/{color}]"

def _fmt_price(v):
    if v is None: return "—"
    return f"${v:,.2f}"

def _fmt_vol(v):
    if v is None: return "—"
    if v >= 1_000_000_000: return f"{v/1_000_000_000:.2f}B"
    if v >= 1_000_000:     return f"{v/1_000_000:.2f}M"
    if v >= 1_000:         return f"{v/1_000:.1f}K"
    return str(int(v))

# ── STATUS ─────────────────────────────────────────────────────────────────────

@app.command()
def status():
    """Full system health dashboard: server, paper, ML, broker, HIL."""
    console.print()
    console.print(Rule("[bold]TradingAgents Status[/bold]", style="cyan"))

    # ── Server ────────────────────────────────────────────────────────────────
    alive = _server_running()
    srv_txt = "[bold green]● ONLINE[/bold green]" if alive else "[bold red]○ OFFLINE[/bold red]"
    pid_txt = ""
    if PID_FILE.exists():
        pid_txt = f"  pid {PID_FILE.read_text().strip()}"
    console.print(f"  Server   {srv_txt}{pid_txt}  → {BASE_URL}")

    if not alive:
        console.print("\n  [dim]Start with: ta server start[/dim]\n")
        return

    # ── Paper trading ─────────────────────────────────────────────────────────
    pr = _get("/paper/status")
    if pr and pr.status_code == 200:
        pd = pr.json()
        running = pd.get("running", False)
        p_txt = "[green]running[/green]" if running else "[dim]idle[/dim]"
        cap = pd.get("capital", pd.get("initial_capital"))
        cap_txt = f"  capital {_fmt_price(cap)}" if cap else ""
        console.print(f"  Paper    {p_txt}{cap_txt}")
    else:
        console.print("  Paper    [dim]unavailable[/dim]")

    # ── ML ────────────────────────────────────────────────────────────────────
    mr = _get("/ml/status")
    if mr and mr.status_code == 200:
        md = mr.json()
        up_to_date = md.get("up_to_date", False)
        label = md.get("status_label", "unknown")
        days_old = md.get("days_old")
        age_txt = f"  {days_old}d old" if days_old is not None else ""
        ml_color = "green" if up_to_date else "yellow"
        console.print(f"  ML       [{ml_color}]{label}[/{ml_color}]{age_txt}")
    else:
        console.print("  ML       [dim]unavailable[/dim]")

    # ── Broker (Fidelity) ─────────────────────────────────────────────────────
    br = _get("/fidelity/status")
    if br and br.status_code == 200:
        bd = br.json()
        connected = bd.get("connected", False)
        b_txt = "[green]connected[/green]" if connected else "[red]disconnected[/red]"
        console.print(f"  Broker   {b_txt}")
    else:
        console.print("  Broker   [dim]unavailable[/dim]")

    # ── HIL ───────────────────────────────────────────────────────────────────
    hr = _get("/hil/pending")
    if hr and hr.status_code == 200:
        hd = hr.json()
        pending = hd if isinstance(hd, list) else hd.get("pending", [])
        count = len(pending)
        h_txt = f"[yellow]{count} pending[/yellow]" if count else "[dim]0 pending[/dim]"
        console.print(f"  HIL      {h_txt}")
    else:
        console.print("  HIL      [dim]unavailable[/dim]")

    # ── Positions summary ─────────────────────────────────────────────────────
    pos_r = _get("/paper/positions")
    if pos_r and pos_r.status_code == 200:
        pos_d = pos_r.json()
        positions = pos_d if isinstance(pos_d, list) else pos_d.get("positions", [])
        if positions:
            total_val = sum(p.get("market_value", 0) for p in positions)
            total_pnl = sum(p.get("unrealized_pnl", 0) for p in positions)
            pnl_color = "green" if total_pnl >= 0 else "red"
            console.print(
                f"  Positions {len(positions)} open  "
                f"value {_fmt_price(total_val)}  "
                f"P&L [{pnl_color}]{_fmt_price(total_pnl)}[/{pnl_color}]"
            )

    # ── Frontend dist ─────────────────────────────────────────────────────────
    if DIST.exists():
        mtime = datetime.fromtimestamp(max(
            f.stat().st_mtime for f in DIST.rglob("*.js") if f.is_file()
        ) if list(DIST.rglob("*.js")) else 0)
        age = datetime.now() - mtime
        age_str = f"{int(age.total_seconds()//60)}m ago" if age.total_seconds() < 3600 else f"{int(age.total_seconds()//3600)}h ago"
        console.print(f"  Frontend built {age_str}  → {BASE_URL}/app")
    else:
        console.print("  Frontend [yellow]not built[/yellow]  run: ta build")

    console.print()


# ── SERVER ─────────────────────────────────────────────────────────────────────

server_app = typer.Typer(help="Manage the uvicorn server.")
app.add_typer(server_app, name="server")

@server_app.command("start")
def server_start(
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p"),
    host: str = typer.Option("127.0.0.1", "--host"),
    reload: bool = typer.Option(False, "--reload", help="Enable hot-reload (dev only)."),
):
    """Start the FastAPI server in the background."""
    if _server_running():
        console.print(f"[yellow]Server already running at {BASE_URL}[/yellow]")
        return

    LOG_FILE.touch(exist_ok=True)
    cmd = [
        sys.executable, "-m", "uvicorn",
        "web.app:app",
        "--host", host,
        "--port", str(port),
    ]
    if reload:
        cmd.append("--reload")

    log_fd = open(LOG_FILE, "a")
    proc = subprocess.Popen(
        cmd, cwd=str(ROOT),
        stdout=log_fd, stderr=log_fd,
        start_new_session=True,
    )
    log_fd.close()  # subprocess inherits fd; close our handle
    PID_FILE.write_text(str(proc.pid))
    console.print(f"[green]Server started[/green]  pid {proc.pid}  → http://{host}:{port}")
    console.print(f"  Logs: ta logs server")

    # Wait up to 5s for first health check
    for _ in range(10):
        time.sleep(0.5)
        if _server_running():
            console.print(f"[green]✓ Health check passed[/green]  → {BASE_URL}/app")
            return
    console.print("[yellow]Server started but health check pending — check: ta logs server[/yellow]")


@server_app.command("stop")
def server_stop():
    """Stop the background server."""
    if not PID_FILE.exists():
        console.print("[yellow]No PID file found. Server may not be managed by ta.[/yellow]")
        return
    pid = int(PID_FILE.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        PID_FILE.unlink(missing_ok=True)
        console.print(f"[green]Server stopped[/green]  (pid {pid})")
    except ProcessLookupError:
        PID_FILE.unlink(missing_ok=True)
        console.print("[dim]Process not found (already stopped).[/dim]")


@server_app.command("status")
def server_status():
    """Check server health."""
    if _server_running():
        console.print(f"[green]● Server online[/green]  {BASE_URL}")
        if PID_FILE.exists():
            console.print(f"  pid {PID_FILE.read_text().strip()}")
    else:
        console.print(f"[red]○ Server offline[/red]  ({BASE_URL} unreachable)")


@server_app.command("restart")
def server_restart(
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p"),
    host: str = typer.Option("127.0.0.1", "--host"),
):
    """Restart the server (stop then start)."""
    server_stop()
    time.sleep(1)
    server_start(port=port, host=host, reload=False)


# ── BUILD / DEV ────────────────────────────────────────────────────────────────

@app.command()
def build(
    watch: bool = typer.Option(False, "--watch", "-w", help="Watch mode (vite build --watch)."),
):
    """Build the React frontend into web/static/dist/."""
    if not FRONTEND.exists():
        console.print("[red]frontend/ directory not found.[/red]")
        raise typer.Exit(1)

    cmd = ["npm", "run", "build"]
    if watch:
        cmd = ["npx", "vite", "build", "--watch"]

    console.print(f"[cyan]Building frontend{'  (watch mode)' if watch else ''}...[/cyan]")
    start = time.time()
    result = subprocess.run(cmd, cwd=str(FRONTEND))
    elapsed = time.time() - start

    if result.returncode == 0:
        console.print(f"[green]✓ Build complete[/green]  ({elapsed:.1f}s)  → {DIST}")
    else:
        console.print(f"[red]✗ Build failed[/red]  (exit {result.returncode})")
        raise typer.Exit(result.returncode)


@app.command()
def dev(
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="Backend port."),
    frontend_port: int = typer.Option(5173, "--frontend-port", "-f", help="Vite dev port."),
):
    """Start both the uvicorn backend and Vite dev server simultaneously."""
    console.print("[cyan]Starting dev environment...[/cyan]")
    console.print(f"  Backend  → http://127.0.0.1:{port}")
    console.print(f"  Frontend → http://127.0.0.1:{frontend_port}/app")
    console.print("  Ctrl+C to stop both.\n")

    backend  = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "web.app:app", "--host", "127.0.0.1", "--port", str(port), "--reload"],
        cwd=str(ROOT),
    )
    frontend = subprocess.Popen(
        ["npm", "run", "dev", "--", "--port", str(frontend_port)],
        cwd=str(FRONTEND),
    )
    try:
        backend.wait()
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutting down...[/yellow]")
        frontend.terminate()
        backend.terminate()


# ── PAPER ─────────────────────────────────────────────────────────────────────

paper_app = typer.Typer(help="Paper trading controls.")
app.add_typer(paper_app, name="paper")

@paper_app.command("status")
def paper_status():
    """Show paper trading run status."""
    r = _get("/paper/status")
    if not r:
        console.print("[red]Server unreachable.[/red]"); return
    d = r.json()
    running = d.get("running", False)
    color = "green" if running else "dim"
    console.print(f"  Paper trading: [{color}]{'RUNNING' if running else 'IDLE'}[/{color}]")
    for k in ("pid", "capital", "initial_capital", "tickers", "start_time", "elapsed"):
        if k in d and d[k] is not None:
            console.print(f"  {k:<16} {d[k]}")


@paper_app.command("start")
def paper_start(
    capital: float = typer.Option(10_000, "--capital", "-c"),
    tickers: Optional[str] = typer.Option(None, "--tickers", "-t", help="Comma-separated tickers."),
):
    """Start a paper trading run."""
    payload: dict = {"capital": capital}
    if tickers:
        payload["tickers"] = [t.strip().upper() for t in tickers.split(",")]
    r = _post("/paper/run", json=payload)
    if not r:
        console.print("[red]Server unreachable.[/red]"); return
    console.print(f"[green]Paper run started[/green]  capital {_fmt_price(capital)}")
    if r.status_code != 200:
        console.print(f"[red]Error {r.status_code}:[/red] {r.text}")


@paper_app.command("stop")
def paper_stop():
    """Stop the current paper trading run."""
    r = _post("/paper/stop")
    if not r:
        console.print("[red]Server unreachable.[/red]"); return
    console.print("[yellow]Paper run stopped.[/yellow]")


@paper_app.command("positions")
def paper_positions():
    """List current paper positions."""
    r = _get("/paper/positions")
    if not r:
        console.print("[red]Server unreachable.[/red]"); return
    d = r.json()
    positions = d if isinstance(d, list) else d.get("positions", [])
    if not positions:
        console.print("[dim]No open positions.[/dim]"); return

    t = Table(title="Paper Positions", header_style="bold cyan", box=box.SIMPLE_HEAD, padding=(0, 1))
    t.add_column("Ticker", width=8)
    t.add_column("Shares", justify="right", width=8)
    t.add_column("Entry", justify="right", width=10)
    t.add_column("Value", justify="right", width=12)
    t.add_column("Unreal P&L", justify="right", width=12)
    t.add_column("P&L %", justify="right", width=8)
    for p in positions:
        pnl = p.get("unrealized_pnl", 0)
        pnl_pct = p.get("unrealized_pnl_pct", 0)
        color = "green" if pnl >= 0 else "red"
        t.add_row(
            p.get("ticker", "—"),
            str(p.get("shares", "—")),
            _fmt_price(p.get("cost_basis") or p.get("entry_price")),
            _fmt_price(p.get("market_value")),
            f"[{color}]{_fmt_price(pnl)}[/{color}]",
            _fmt_pct(pnl_pct),
        )
    console.print(t)


@paper_app.command("candidates")
def paper_candidates(
    history: bool = typer.Option(False, "--history", "-H", help="Show historical candidates instead of live."),
):
    """Show current (or historical) paper trading candidates."""
    endpoint = "/paper/candidates-history" if history else "/paper/candidates"
    r = _get(endpoint)
    if not r:
        console.print("[red]Server unreachable.[/red]"); return
    d = r.json()
    rows = d if isinstance(d, list) else d.get("candidates", d.get("rows", []))
    if not rows:
        console.print("[dim]No candidates.[/dim]"); return

    t = Table(
        title=f"{'Historical' if history else 'Live'} Candidates",
        header_style="bold magenta", box=box.SIMPLE_HEAD, padding=(0, 1),
    )
    t.add_column("Ticker", width=8)
    for col in ("score", "signal", "entry", "stop", "target", "rr", "ml_prob", "gate"):
        t.add_column(col.upper(), justify="right", width=9)
    for row in rows[:30]:
        signal = str(row.get("signal", "—"))
        sig_color = "green" if "buy" in signal.lower() or "long" in signal.lower() else \
                    "red" if "sell" in signal.lower() or "short" in signal.lower() else "white"
        t.add_row(
            row.get("ticker", "—"),
            str(row.get("score", "—")),
            f"[{sig_color}]{signal}[/{sig_color}]",
            _fmt_price(row.get("entry")),
            _fmt_price(row.get("stop")),
            _fmt_price(row.get("target")),
            f"{row.get('rr', '—')}",
            f"{row.get('ml_prob', row.get('ml_probability', '—'))}",
            str(row.get("gate", row.get("gate_pass", "—"))),
        )
    console.print(t)


# ── ML ─────────────────────────────────────────────────────────────────────────

ml_app = typer.Typer(help="ML model commands.")
app.add_typer(ml_app, name="ml")

@ml_app.command("status")
def ml_status():
    """Show ML model status."""
    r = _get("/ml/status")
    if not r:
        console.print("[red]Server unreachable.[/red]"); return
    d = r.json()
    up = d.get("up_to_date", False)
    color = "green" if up else "yellow"
    console.print(Panel(
        f"Status:    [{color}]{d.get('status_label', '—')}[/{color}]\n"
        f"Created:   {d.get('created_at', '—')}\n"
        f"Days old:  {d.get('days_old', '—')}\n"
        f"Bundle:    {'✓' if d.get('bundle_exists') else '✗'}\n"
        f"Report:    {'✓' if d.get('report_exists') else '✗'}",
        title="ML Status", border_style="cyan",
    ))
    if settings := d.get("settings"):
        console.print(f"  Threshold  {settings.get('ml_probability_threshold', '—')}")
        console.print(f"  Features   {settings.get('feature_count', '—')}")


@ml_app.command("train")
def ml_train(
    model_type: str = typer.Argument("all", help="Model type to train (or 'all')."),
):
    """Trigger ML model training."""
    r = _post("/ml/train", json={"model_type": model_type})
    if not r:
        console.print("[red]Server unreachable.[/red]"); return
    if r.status_code == 200:
        console.print(f"[green]Training started[/green]  model_type={model_type}")
    else:
        console.print(f"[red]Error {r.status_code}:[/red] {r.text}")


@ml_app.command("predict")
def ml_predict(ticker: str = typer.Argument(..., help="Ticker to predict.")):
    """Get ML prediction for a ticker."""
    r = _post("/ml/predict", json={"ticker": ticker.upper()})
    if not r:
        console.print("[red]Server unreachable.[/red]"); return
    d = r.json()
    prob = d.get("probability", d.get("ml_prob", d.get("score")))
    signal = d.get("signal", d.get("prediction", "—"))
    sig_color = "green" if str(signal).lower() in ("buy", "long", "1") else \
                "red" if str(signal).lower() in ("sell", "short", "0") else "white"
    console.print(f"  {ticker.upper():<8}  signal [{sig_color}]{signal}[/{sig_color}]  prob {prob}")


@ml_app.command("retrain")
def ml_retrain(
    months: int = typer.Option(
        84, "--months", "-m",
        help="Rolling window in months. 84 = 7 years (recommended for full feature coverage).",
    ),
    tickers: str = typer.Option(
        "all_tickers.txt", "--tickers", "-t",
        help="Ticker file: all_tickers.txt | tickers_liquid.txt | tickers_quality.txt",
    ),
    n_estimators: int = typer.Option(600, "--n-estimators", "-n"),
    executed_weight: float = typer.Option(
        20.0, "--executed-weight",
        help="Sample weight for rule-passing rows vs rejected rows (20 = 20× upweight).",
    ),
    min_roc: float = typer.Option(0.56, "--min-roc", help="Minimum win AUC required to accept bundle."),
    max_brier: float = typer.Option(0.24, "--max-brier", help="Maximum Brier score to accept bundle."),
    threshold: float = typer.Option(0.60, "--threshold", help="Starting ML probability gate threshold."),
    hold: int = typer.Option(3, "--hold", help="Forward hold period in days for labels."),
    output_dir: str = typer.Option("ml_models/latest", "--output-dir", "-o"),
    skip_holdout: bool = typer.Option(False, "--skip-holdout", help="Skip holdout validation step."),
    skip_gates: bool = typer.Option(False, "--skip-gates", help="Skip ROC/Brier quality gates (dev only)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the full pipeline without running it."),
):
    """
    Full production ML retrain via retrain_weekly.py.

    Pipeline:
      1. Backtest tickers over rolling window → export trades CSV
      2. Train XGB+RF ensemble with executed-row weighting + calibration
      3. Leakage check (abort if features leak future data)
      4. Quality gates: ROC >= min_roc, Brier < max_brier
      5. Swap bundle into output_dir, log to ml_models/retrain_history.jsonl
      6. Holdout validation (diagnostic only)

    Why 84 months / all_tickers / executed-weight 20:
      New features (atr_expansion, spy_momentum_accel, setup_rr, stock_regime,
      slope_sma20/50, obv_above_sma, pvt_above_sma, dmi_bull) require a wide
      training window to see enough market regimes. Executed-weight=20 ensures
      the model learns from actual rule-passing setups, not noisy rejected rows.

    Examples:
      ta ml retrain                          # recommended defaults, ~4-8h
      ta ml retrain --dry-run                # preview full command chain
      ta ml retrain --tickers tickers_liquid.txt  # faster, liquid universe only
      ta ml retrain --skip-holdout           # skip holdout, save ~30 min
    """
    import json as _json

    retrain_script = ROOT / "scripts" / "retrain_weekly.py"
    if not retrain_script.exists():
        console.print(f"[red]retrain_weekly.py not found at {retrain_script}[/red]")
        raise typer.Exit(1)

    ticker_file = ROOT / tickers
    if not ticker_file.exists():
        console.print(f"[red]Ticker file not found: {ticker_file}[/red]")
        raise typer.Exit(1)

    today = datetime.now().date()
    from datetime import date
    window_start = date(today.year, today.month, 1)  # approx
    window_start_str = (datetime.now() - timedelta(days=months * 30)).strftime("%Y-%m-%d")

    # Count tickers
    ticker_count = sum(1 for line in ticker_file.open() if line.strip())

    # ── Show plan ─────────────────────────────────────────────────────────────
    console.print()
    console.print(Panel(
        f"Script:        retrain_weekly.py\n"
        f"Tickers:       {tickers}  ({ticker_count:,} symbols)\n"
        f"Window:        {months} months  ({window_start_str} → today)\n"
        f"Estimators:    {n_estimators}\n"
        f"Exec-weight:   {executed_weight}×\n"
        f"Threshold:     {threshold}\n"
        f"Hold period:   {hold}d\n"
        f"Gates:         ROC ≥ {min_roc}  |  Brier < {max_brier}\n"
        f"Output:        {ROOT / output_dir}\n"
        f"\n[bold]New features being trained:[/bold]\n"
        f"  atr_expansion, spy_momentum_accel, setup_rr, stock_regime\n"
        f"  slope_sma20, slope_sma50, obv_above_sma, pvt_above_sma, dmi_bull\n"
        f"\n[bold yellow]Estimated time: 4-8 hours (all_tickers × 84 months)[/bold yellow]",
        title="ML Retrain Plan — Production Pipeline",
        border_style="cyan",
    ))

    cmd = [
        sys.executable, str(retrain_script),
        "--tickers",               tickers,
        "--months",                str(months),
        "--output-dir",            output_dir,
        "--hold",                  str(hold),
        "--n-estimators",          str(n_estimators),
        "--ml-probability-threshold", str(threshold),
        "--executed-weight",       str(executed_weight),
        "--min-roc",               str(min_roc),
        "--max-brier",             str(max_brier),
    ]
    if skip_holdout:
        cmd.append("--skip-holdout")
    if skip_gates:
        cmd.append("--skip-gates")

    if dry_run:
        console.print("\n[yellow]--dry-run  Command that would run:[/yellow]")
        console.print("  " + " \\\n    ".join(str(c) for c in cmd))
        console.print()
        # Also show retrain_weekly dry-run for full step breakdown
        console.print("[dim]Full step preview from retrain_weekly.py --dry-run:[/dim]")
        subprocess.run(cmd + ["--dry-run"], cwd=str(ROOT))
        return

    console.print()
    confirm = typer.prompt(
        "This will run a 4-8 hour backtest + retrain. Proceed?", default="Y"
    ).strip().upper()
    if confirm not in ("Y", "YES"):
        console.print("[dim]Aborted.[/dim]"); return

    console.print("\n[cyan]Starting production retrain pipeline...[/cyan]")
    console.print("[dim]Output streams live below. Ctrl+C to abort (won't auto-clean temp CSV).[/dim]\n")

    start_ts = time.time()
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    result = subprocess.run(cmd, cwd=str(ROOT), env=env)
    elapsed = time.time() - start_ts
    h, rem = divmod(int(elapsed), 3600)
    m, s = divmod(rem, 60)
    elapsed_str = f"{h}h {m}m {s}s" if h else f"{m}m {s}s"

    if result.returncode != 0:
        console.print(f"\n[red]✗ Retrain failed[/red]  (exit {result.returncode}  {elapsed_str})")
        console.print("  Check output above for step that failed.")
        console.print("  Retrain history: ml_models/retrain_history.jsonl")
        raise typer.Exit(result.returncode)

    # ── Parse results from report ──────────────────────────────────────────────
    report_path = ROOT / output_dir / "training_report.json"
    console.print(f"\n[green]✓ Retrain complete[/green]  ({elapsed_str})")

    if report_path.exists():
        try:
            report   = _json.loads(report_path.read_text())
            settings = report.get("settings", {})
            win_m    = report.get("models", {}).get("win_probability", {}).get("metrics", {})
            cal      = report.get("models", {}).get("win_probability", {}).get("calibration", {})
            thr_srch = report.get("threshold_search", {})
            rec_thr  = thr_srch.get("recommended_threshold")
            wf       = report.get("walk_forward", {})
            psi      = report.get("feature_psi", {})

            auc = win_m.get("roc_auc", "—")
            brier_after = cal.get("brier_after", "—")
            auc_color = "green" if isinstance(auc, float) and auc >= min_roc else "yellow"
            brier_color = "green" if isinstance(brier_after, float) and brier_after < max_brier else "red"

            summary = (
                f"Rows used:    {settings.get('rows_used', '?'):,}\n"
                f"Features:     {len(settings.get('feature_names', [])) or settings.get('feature_count', '?')}\n"
                f"Test period:  {settings.get('test_period', '?')}\n"
                f"Calibrated:   {settings.get('calibrated', False)}\n"
                f"\n[bold]Win Probability[/bold]\n"
                f"  AUC:        [{auc_color}]{auc}[/{auc_color}]  (gate ≥ {min_roc})\n"
                f"  Brier:      [{brier_color}]{brier_after}[/{brier_color}]  (gate < {max_brier})\n"
                f"  Precision:  {win_m.get('precision', '—')}\n"
                f"  Recall:     {win_m.get('recall', '—')}\n"
            )
            if rec_thr:
                rec = thr_srch.get(str(rec_thr), {})
                summary += (
                    f"\n[bold]Recommended threshold:[/bold] {rec_thr}\n"
                    f"  Win rate:   {rec.get('win_rate', '?')}\n"
                    f"  Avg ret:    {rec.get('avg_return_pct', '?')}%\n"
                    f"  N trades:   {rec.get('n', '?')}\n"
                )
            if wf.get("roc_auc"):
                wf_color = "green" if (wf.get("roc_auc", 0) or 0) >= min_roc else "yellow"
                summary += (
                    f"\n[bold]Walk-forward[/bold]\n"
                    f"  AUC:          [{wf_color}]{wf['roc_auc']}[/{wf_color}]\n"
                    f"  High-conf WR: {wf.get('high_conf_win_rate', '—')}\n"
                    f"  High-conf N:  {wf.get('high_conf_n', '—')}\n"
                )
            if psi.get("n_fail", 0) > 0:
                summary += f"\n[yellow]PSI drift: {psi['n_fail']} features shifted[/yellow]"
            else:
                summary += "\n[green]PSI: no drift detected[/green]"

            console.print(Panel(summary, title="Training Results", border_style="green"))
        except Exception as e:
            console.print(f"[dim]Could not parse training report: {e}[/dim]")

    # ── Retrain history ────────────────────────────────────────────────────────
    history_path = ROOT / "ml_models" / "retrain_history.jsonl"
    if history_path.exists():
        lines = history_path.read_text().strip().splitlines()
        if lines:
            last = _json.loads(lines[-1])
            console.print(f"\n  History entry: [green]{last.get('outcome', '?')}[/green]  {last.get('retrain_date', '')}")

    console.print(f"\n  Bundle:  {ROOT / output_dir / 'model_bundle.joblib'}")
    console.print(f"  Report:  {report_path}")
    console.print()
    console.print("[dim]Reload model in running server: ta server restart[/dim]")


# ── TICKER ─────────────────────────────────────────────────────────────────────

@app.command()
def ticker(
    symbols: List[str] = typer.Argument(..., help="Ticker symbols to look up."),
    detail: bool = typer.Option(False, "--detail", "-d", help="Show full quote detail."),
):
    """Quick price quote(s). Example: ta ticker AAPL MSFT NVDA"""
    t = Table(header_style="bold cyan", box=box.SIMPLE_HEAD, padding=(0, 1), show_header=True)
    t.add_column("Symbol", width=8)
    t.add_column("Price", justify="right", width=10)
    t.add_column("Change", justify="right", width=10)
    t.add_column("Change %", justify="right", width=10)
    if detail:
        t.add_column("Volume", justify="right", width=10)
        t.add_column("Mkt Cap", justify="right", width=12)
        t.add_column("P/E", justify="right", width=8)
        t.add_column("52w Low", justify="right", width=9)
        t.add_column("52w High", justify="right", width=9)

    for sym in symbols:
        sym = sym.upper()
        endpoint = f"/market/quote-detail?symbol={sym}" if detail else f"/market/quote?symbol={sym}"
        r = _get(endpoint)
        if not r or r.status_code != 200:
            t.add_row(sym, "[red]error[/red]", "—", "—", *( ["—"] * (5 if detail else 0) ))
            continue
        d = r.json()
        price = d.get("price")
        chg   = d.get("change")
        chg_p = d.get("change_pct")
        chg_color = "green" if (chg or 0) >= 0 else "red"
        row = [
            f"[bold]{sym}[/bold]",
            _fmt_price(price),
            f"[{chg_color}]{_fmt_price(chg)}[/{chg_color}]" if chg is not None else "—",
            _fmt_pct(chg_p),
        ]
        if detail:
            row += [
                _fmt_vol(d.get("volume")),
                _fmt_vol(d.get("market_cap")),
                f"{d.get('pe_ratio', '—')}",
                _fmt_price(d.get("week52_low")),
                _fmt_price(d.get("week52_high")),
            ]
        t.add_row(*row)

    console.print(t)


# ── SCAN ───────────────────────────────────────────────────────────────────────

@app.command()
def scan(
    symbols: List[str] = typer.Argument(..., help="Tickers to scan."),
    threshold: float = typer.Option(85.0, "--threshold", "-T"),
):
    """Quick technical screen on given tickers. Example: ta scan AAPL MSFT NVDA"""
    console.print(f"[cyan]Screening {len(symbols)} ticker(s) (threshold {threshold:.0f}/100)...[/cyan]")
    r = _post("/scanner/screen", json={
        "tickers": [s.upper() for s in symbols],
        "threshold": threshold,
    })
    if not r:
        console.print("[red]Server unreachable — run ta server start[/red]"); return
    if r.status_code != 200:
        console.print(f"[red]Error {r.status_code}:[/red] {r.text}"); return

    results = r.json()
    rows = results if isinstance(results, list) else results.get("results", [])
    if not rows:
        console.print("[dim]No results.[/dim]"); return

    t = Table(header_style="bold magenta", box=box.SIMPLE_HEAD, padding=(0, 1))
    t.add_column("Ticker", width=8)
    t.add_column("Score", justify="center", width=7)
    t.add_column("Status", justify="center", width=8)
    for row in rows:
        score = row.get("score", 0)
        passed = row.get("passed", score >= threshold)
        color = "green" if passed else ("yellow" if score >= threshold * 0.85 else "dim")
        status = "[bold green]PASS ✓[/bold green]" if passed else "[dim]skip[/dim]"
        if row.get("error"):
            status = "[red]ERROR[/red]"
        t.add_row(row.get("ticker", "—"), f"[{color}]{score:.1f}[/{color}]", status)
    console.print(t)


# ── HISTORY ────────────────────────────────────────────────────────────────────

@app.command()
def history(
    ticker_sym: Optional[str] = typer.Option(None, "--ticker", "-t"),
    days: int = typer.Option(30, "--days", "-d"),
    export: bool = typer.Option(False, "--export", "-e", help="Export to CSV."),
    limit: int = typer.Option(50, "--limit", "-n"),
):
    """View trade history. Example: ta history --ticker AAPL --days 7"""
    params: dict = {"limit": limit}
    if ticker_sym:
        params["ticker"] = ticker_sym.upper()
    date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    params["date_from"] = date_from

    r = _get("/history", params=params)
    if not r:
        console.print("[red]Server unreachable.[/red]"); return
    d = r.json()
    rows = d if isinstance(d, list) else d.get("history", d.get("rows", []))
    if not rows:
        console.print(f"[dim]No history for the last {days} days.[/dim]"); return

    t = Table(
        title=f"Trade History  ({date_from} → today)",
        header_style="bold cyan", box=box.SIMPLE_HEAD, padding=(0, 1),
    )
    t.add_column("Date", width=11)
    t.add_column("Ticker", width=8)
    t.add_column("Signal", width=10)
    t.add_column("Price", justify="right", width=10)
    t.add_column("P&L", justify="right", width=10)

    csv_rows = [["date", "ticker", "signal", "price", "pnl"]]
    for row in rows:
        signal = str(row.get("signal", row.get("decision", "—")))
        sig_color = "green" if "buy" in signal.lower() else "red" if "sell" in signal.lower() else "white"
        pnl = row.get("pnl", row.get("gain_loss"))
        pnl_txt = _fmt_price(pnl) if pnl is not None else "—"
        pnl_color = "green" if (pnl or 0) >= 0 else "red"
        date_val = str(row.get("date", row.get("analysis_date", "—")))
        ticker_val = str(row.get("ticker", "—"))
        price_val = _fmt_price(row.get("price", row.get("entry_price")))
        t.add_row(
            date_val, ticker_val,
            f"[{sig_color}]{signal}[/{sig_color}]",
            price_val,
            f"[{pnl_color}]{pnl_txt}[/{pnl_color}]",
        )
        csv_rows.append([date_val, ticker_val, signal, price_val, str(pnl)])
    console.print(t)

    if export:
        fname = f"history-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
        import csv
        with open(fname, "w", newline="") as f:
            csv.writer(f).writerows(csv_rows)
        console.print(f"[green]Exported {len(csv_rows)-1} rows → {fname}[/green]")


# ── HIL ───────────────────────────────────────────────────────────────────────

hil_app = typer.Typer(help="Human-in-the-Loop approval commands.")
app.add_typer(hil_app, name="hil")

@hil_app.command("pending")
def hil_pending():
    """List pending HIL trade approvals."""
    r = _get("/hil/pending")
    if not r:
        console.print("[red]Server unreachable.[/red]"); return
    d = r.json()
    pending = d if isinstance(d, list) else d.get("pending", [])
    if not pending:
        console.print("[dim]No pending approvals.[/dim]"); return

    t = Table(title="Pending HIL Approvals", header_style="bold yellow", box=box.SIMPLE_HEAD, padding=(0, 1))
    t.add_column("ID", width=10)
    t.add_column("Ticker", width=8)
    t.add_column("Signal", width=10)
    t.add_column("Price", justify="right", width=10)
    t.add_column("Time", width=20)
    for item in pending:
        t.add_row(
            str(item.get("id", item.get("approval_id", "—"))),
            str(item.get("ticker", "—")),
            str(item.get("signal", item.get("decision", "—"))),
            _fmt_price(item.get("price", item.get("entry_price"))),
            str(item.get("created_at", item.get("timestamp", "—"))),
        )
    console.print(t)
    console.print("[dim]  Approve: ta hil approve <ID>    Reject: ta hil reject <ID>[/dim]")


@hil_app.command("approve")
def hil_approve(approval_id: str = typer.Argument(...)):
    """Approve a pending HIL trade."""
    r = _post(f"/hil/approve", json={"approval_id": approval_id})
    if not r:
        console.print("[red]Server unreachable.[/red]"); return
    if r.status_code == 200:
        console.print(f"[green]✓ Approved[/green]  id={approval_id}")
    else:
        console.print(f"[red]Error {r.status_code}:[/red] {r.text}")


@hil_app.command("reject")
def hil_reject(approval_id: str = typer.Argument(...)):
    """Reject a pending HIL trade."""
    r = _post(f"/hil/reject", json={"approval_id": approval_id})
    if not r:
        console.print("[red]Server unreachable.[/red]"); return
    if r.status_code == 200:
        console.print(f"[yellow]✗ Rejected[/yellow]  id={approval_id}")
    else:
        console.print(f"[red]Error {r.status_code}:[/red] {r.text}")


@hil_app.command("history")
def hil_history(limit: int = typer.Option(20, "--limit", "-n")):
    """Show HIL approval history."""
    r = _get("/hil/history", params={"limit": limit})
    if not r:
        console.print("[red]Server unreachable.[/red]"); return
    d = r.json()
    rows = d if isinstance(d, list) else d.get("history", [])
    if not rows:
        console.print("[dim]No HIL history.[/dim]"); return
    t = Table(title="HIL History", header_style="bold cyan", box=box.SIMPLE_HEAD, padding=(0, 1))
    t.add_column("ID", width=10)
    t.add_column("Ticker", width=8)
    t.add_column("Signal", width=10)
    t.add_column("Action", width=10)
    t.add_column("Time", width=20)
    for item in rows:
        action = str(item.get("action", item.get("status", "—")))
        action_color = "green" if action.lower() == "approved" else "red" if action.lower() == "rejected" else "white"
        t.add_row(
            str(item.get("id", "—")),
            str(item.get("ticker", "—")),
            str(item.get("signal", "—")),
            f"[{action_color}]{action}[/{action_color}]",
            str(item.get("decided_at", item.get("timestamp", "—"))),
        )
    console.print(t)


# ── LOGS ───────────────────────────────────────────────────────────────────────

@app.command()
def logs(
    source: str = typer.Argument("server", help="Log source: server | paper | ml | web"),
    lines: int = typer.Option(50, "--lines", "-n"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Tail -f mode (server log only)."),
):
    """Tail logs. Example: ta logs server -n 100 -f"""
    if source == "server":
        if not LOG_FILE.exists():
            console.print("[dim]No server log yet. Start server with: ta server start[/dim]")
            return
        if follow:
            subprocess.run(["tail", "-f", str(LOG_FILE)])
        else:
            subprocess.run(["tail", f"-{lines}", str(LOG_FILE)])
        return

    # API-served logs
    endpoint_map = {
        "paper": "/paper/logs",
        "ml":    "/ml/logs",
        "web":   "/logs",
    }
    endpoint = endpoint_map.get(source)
    if not endpoint:
        console.print(f"[red]Unknown source '{source}'. Use: server | paper | ml | web[/red]")
        return
    r = _get(endpoint, params={"limit": lines})
    if not r:
        console.print("[red]Server unreachable.[/red]"); return
    d = r.json()
    log_lines = d if isinstance(d, list) else d.get("logs", d.get("lines", []))
    if not log_lines:
        console.print("[dim]No log lines.[/dim]"); return
    for line in log_lines:
        console.print(str(line))


# ── BACKTEST ───────────────────────────────────────────────────────────────────

backtest_app = typer.Typer(help="Backtest commands.")
app.add_typer(backtest_app, name="backtest")

@backtest_app.command("run")
def backtest_run(
    ticker_sym: str = typer.Argument(..., help="Ticker to backtest."),
    start: str = typer.Option("2024-01-01", "--start", "-s"),
    end:   str = typer.Option("", "--end", "-e"),
    capital: float = typer.Option(10_000, "--capital", "-c"),
):
    """Run a backtest. Example: ta backtest run AAPL --start 2024-01-01"""
    if not end:
        end = datetime.now().strftime("%Y-%m-%d")
    payload = {"ticker": ticker_sym.upper(), "start_date": start, "end_date": end, "capital": capital}
    console.print(f"[cyan]Running backtest for {ticker_sym.upper()}  {start} → {end}...[/cyan]")
    r = _post("/backtest/run", json=payload)
    if not r:
        console.print("[red]Server unreachable.[/red]"); return
    if r.status_code != 200:
        console.print(f"[red]Error {r.status_code}:[/red] {r.text}"); return
    d = r.json()
    console.print(Panel(
        f"Ticker:       {ticker_sym.upper()}\n"
        f"Period:       {start} → {end}\n"
        f"Total Return: {_fmt_pct(d.get('total_return_pct'))}\n"
        f"Sharpe:       {d.get('sharpe_ratio', '—')}\n"
        f"Max Drawdown: {_fmt_pct(d.get('max_drawdown_pct'))}\n"
        f"Trades:       {d.get('num_trades', '—')}\n"
        f"Win Rate:     {_fmt_pct(d.get('win_rate'))}",
        title="Backtest Results", border_style="cyan",
    ))


@backtest_app.command("results")
def backtest_results(limit: int = typer.Option(20, "--limit", "-n")):
    """Show recent backtest results."""
    r = _get("/backtest/results", params={"limit": limit})
    if not r:
        console.print("[red]Server unreachable.[/red]"); return
    d = r.json()
    rows = d if isinstance(d, list) else d.get("results", [])
    if not rows:
        console.print("[dim]No backtest results.[/dim]"); return

    t = Table(title="Recent Backtests", header_style="bold cyan", box=box.SIMPLE_HEAD, padding=(0, 1))
    t.add_column("Ticker", width=8)
    t.add_column("Start", width=12)
    t.add_column("End", width=12)
    t.add_column("Return", justify="right", width=10)
    t.add_column("Sharpe", justify="right", width=8)
    t.add_column("Trades", justify="right", width=8)
    for row in rows:
        t.add_row(
            str(row.get("ticker", "—")),
            str(row.get("start_date", "—")),
            str(row.get("end_date", "—")),
            _fmt_pct(row.get("total_return_pct")),
            str(row.get("sharpe_ratio", "—")),
            str(row.get("num_trades", "—")),
        )
    console.print(t)


# ── DB ─────────────────────────────────────────────────────────────────────────

db_app = typer.Typer(help="Database utilities.")
app.add_typer(db_app, name="db")

@db_app.command("backup")
def db_backup(
    output: str = typer.Option("", "--output", "-o", help="Output path (default: ./backups/ta_TIMESTAMP.db)"),
):
    """Back up the SQLite backtest database."""
    import shutil
    src = ROOT / "backtest_index.db"
    if not src.exists():
        console.print("[dim]No backtest_index.db found.[/dim]"); return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = Path(output) if output else ROOT / "backups" / f"ta_{ts}.db"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    console.print(f"[green]Backup saved → {dest}[/green]")


@db_app.command("stats")
def db_stats():
    """Show database file sizes and record counts."""
    import sqlite3
    dbs = list(ROOT.glob("*.db")) + list(ROOT.glob("**/*.db"))
    t = Table(title="Databases", header_style="bold cyan", box=box.SIMPLE_HEAD, padding=(0, 1))
    t.add_column("File", no_wrap=True)
    t.add_column("Size", justify="right", width=10)
    t.add_column("Tables", justify="right", width=8)
    for db in sorted(dbs)[:20]:
        size_kb = db.stat().st_size / 1024
        try:
            conn = sqlite3.connect(str(db))
            tables = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
            conn.close()
        except Exception:
            tables = "?"
        t.add_row(str(db.relative_to(ROOT)), f"{size_kb:.1f} KB", str(tables))
    console.print(t)


# ── WATCH ──────────────────────────────────────────────────────────────────────

@app.command()
def watch(
    symbols: List[str] = typer.Argument(..., help="Tickers to watch."),
    interval: int = typer.Option(30, "--interval", "-i", help="Refresh interval in seconds."),
):
    """Live price watcher. Refreshes every N seconds. Ctrl+C to stop."""
    import shutil
    console.print(f"[cyan]Watching {', '.join(s.upper() for s in symbols)}  (every {interval}s)  Ctrl+C to stop[/cyan]\n")
    try:
        while True:
            w = shutil.get_terminal_size().columns
            ts = datetime.now().strftime("%H:%M:%S")
            header = f"  [dim]{ts}[/dim]"
            rows = []
            for sym in symbols:
                r = _get(f"/market/quote?symbol={sym.upper()}")
                if r and r.status_code == 200:
                    d = r.json()
                    price = d.get("price", "?")
                    chg_p = d.get("change_pct", 0)
                    color = "green" if (chg_p or 0) >= 0 else "red"
                    rows.append(f"  [bold]{sym.upper():<6}[/bold]  ${price:<10.2f}  [{color}]{chg_p:+.2f}%[/{color}]")
                else:
                    rows.append(f"  [bold]{sym.upper():<6}[/bold]  [red]error[/red]")
            # Print block, then sleep
            console.print(header)
            for row in rows:
                console.print(row)
            console.print()
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[dim]Watch stopped.[/dim]")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app()
