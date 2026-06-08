import asyncio
import concurrent.futures
import json
import subprocess
import tempfile
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, field_validator

from web.auth import require_admin

router = APIRouter()

# Concurrency guard — prevent DoS via multiple simultaneous expensive backtests
_MAX_CONCURRENT_BACKTESTS = 2
_active_backtests = 0
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)


class BacktestRequest(BaseModel):
    tickers: List[str]
    start_date: str
    end_date: str
    analysts: List[str]
    llm_provider: str
    deep_think_llm: str
    quick_think_llm: str
    max_debate_rounds: int = 1
    frequency: str = "weekly"
    initial_capital: float = 100000.0

    @field_validator("tickers")
    @classmethod
    def validate_tickers(cls, v: list) -> list:
        if not v:
            raise ValueError("tickers must not be empty")
        if len(v) > 50:
            raise ValueError("too many tickers — maximum 50 per backtest run")
        return [t.strip().upper() for t in v if t.strip()]

    @field_validator("analysts")
    @classmethod
    def validate_analysts(cls, v: list) -> list:
        allowed = {"market", "social", "news", "fundamentals"}
        invalid = [a for a in v if a not in allowed]
        if invalid:
            raise ValueError(f"unknown analysts: {invalid}. Allowed: {sorted(allowed)}")
        return v


class ScreenRequest(BaseModel):
    tickers: List[str]
    date: Optional[str] = None
    threshold: float = 75.0
    mode: str = "standard"


@router.get("/backtest/results")
async def list_backtest_results(_user: dict = Depends(require_admin)):
    """List existing backtest result JSON files from project root (metadata only, no full read)."""
    results = []
    MAX_READ_BYTES = 512 * 1024  # Only read first 512 KB for summary
    for f in sorted(ROOT.glob("backtest_results_*.json"), reverse=True):
        try:
            size_bytes = f.stat().st_size
            entry = {
                "filename": f.name,
                "path": str(f),
                "timestamp": f.stem.replace("backtest_results_", ""),
                "size_mb": round(size_bytes / 1024 / 1024, 1),
                "summary": {},
            }
            if size_bytes <= MAX_READ_BYTES:
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if isinstance(data, list) and data:
                        entry["summary"]["total_trades"] = len(data)
                        decisions = [d.get("decision", d.get("signal", "")) for d in data if isinstance(d, dict)]
                        entry["summary"]["decisions"] = {
                            d: decisions.count(d) for d in set(decisions) if d
                        }
                    elif isinstance(data, dict):
                        entry["summary"] = {k: v for k, v in data.items() if not isinstance(v, (list, dict))}
                except Exception:
                    pass
            else:
                entry["summary"]["note"] = f"Large file ({entry['size_mb']} MB) — click View to load"
            results.append(entry)
        except Exception:
            pass
    return {"results": results}


@router.get("/backtest/results/{filename}")
async def get_backtest_result(filename: str, _user: dict = Depends(require_admin)):
    """Load a specific backtest result file (capped at 50 MB)."""
    import re
    if not re.match(r"^backtest_results_[0-9A-Za-z_-]+\.json$", filename):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Break CodeQL taint by using string from filesystem
    valid_files = [p.name for p in ROOT.glob("backtest_results_*.json")]
    if filename not in valid_files:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="File not found")
    
    safe_filename = next(f for f in valid_files if f == filename)
    path = ROOT / safe_filename
    if not path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="File not found")
    MAX_BYTES = 50 * 1024 * 1024
    size = path.stat().st_size
    if size > MAX_BYTES:
        return {
            "filename": filename,
            "size_mb": round(size / 1024 / 1024, 1),
            "error": f"File too large ({round(size/1024/1024,1)} MB) to display in browser. Use CLI or direct file access.",
            "data": None,
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # If list, cap at 500 rows for display
        if isinstance(data, list) and len(data) > 500:
            return {"filename": filename, "data": data[:500], "truncated": True, "total_rows": len(data)}
        return {"filename": filename, "data": data}
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/backtest/screen")
async def screen_tickers(req: ScreenRequest, admin: dict = Depends(require_admin)):
    """Run technical screener (no LLM calls) on a list of tickers."""
    try:
        if req.mode == "swing":
            from tradingagents.screening import SwingScreener
            screener = SwingScreener(threshold=req.threshold)
        else:
            from tradingagents.screening import StockScreener
            screener = StockScreener(threshold=req.threshold)

        date = req.date or datetime.now().strftime("%Y-%m-%d")
        results = screener.screen_batch(req.tickers, as_of_date=date)

        output = []
        for r in results:
            entry = {
                "ticker": r.ticker,
                "score": r.score if r.score is not None else 0,
                "passed": r.passed,
                "error": r.error,
            }
            if hasattr(r, "signals") and r.signals:
                s = r.signals
                entry["signals"] = {
                    "trend": getattr(s, "trend", 0),
                    "momentum": getattr(s, "momentum", 0),
                    "rsi": getattr(s, "rsi", 0),
                    "volume": getattr(s, "volume", 0),
                    "macd": getattr(s, "macd", 0),
                }
            if hasattr(r, "targets") and r.targets:
                t = r.targets
                entry["targets"] = {
                    "entry": getattr(t, "entry", 0),
                    "target": getattr(t, "target", 0),
                    "stop": getattr(t, "stop", 0),
                    "risk_reward": getattr(t, "risk_reward", 0),
                    "atr": getattr(t, "atr", 0),
                }
            output.append(entry)

        return {"results": output, "date": date, "threshold": req.threshold}
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))


@router.websocket("/ws/backtest")
async def ws_backtest(websocket: WebSocket):
    """Run LLM-powered backtest: analysis for each ticker/date combo."""
    global _active_backtests
    await websocket.accept()
    # ── Admin auth gate (Cloudflare Access JWT verified) ──
    from web.auth import ws_require_admin
    _ws_user = await ws_require_admin(websocket)
    if _ws_user is None:
        return

    if _active_backtests >= _MAX_CONCURRENT_BACKTESTS:
        await websocket.send_json({"type": "error", "message": f"Server busy: {_active_backtests} backtest(s) already running. Try again in a moment."})
        await websocket.close()
        return

    _active_backtests += 1
    queue: asyncio.Queue = asyncio.Queue()
    main_loop = asyncio.get_running_loop()

    try:
        config_data = await websocket.receive_json()
        req = BacktestRequest(**config_data)
    except Exception as e:
        _active_backtests -= 1
        await websocket.send_json({"type": "error", "message": str(e)})
        await websocket.close()
        return

    def run_sync():
        try:
            import pandas as pd
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            from tradingagents.default_config import DEFAULT_CONFIG

            freq_map = {"daily": "B", "weekly": "W-FRI", "monthly": "BMS"}
            freq = freq_map.get(req.frequency, "W-FRI")
            dates = pd.date_range(req.start_date, req.end_date, freq=freq)
            dates = [d.strftime("%Y-%m-%d") for d in dates]

            total_steps = len(req.tickers) * len(dates)
            step = 0

            asyncio.run_coroutine_threadsafe(
                queue.put({
                    "type": "info",
                    "message": f"Running {total_steps} analyses ({len(req.tickers)} tickers × {len(dates)} dates)",
                    "total": total_steps,
                }),
                main_loop,
            )

            config = DEFAULT_CONFIG.copy()
            config["max_debate_rounds"] = req.max_debate_rounds
            config["max_risk_discuss_rounds"] = req.max_debate_rounds
            config["quick_think_llm"] = req.quick_think_llm
            config["deep_think_llm"] = req.deep_think_llm
            config["llm_provider"] = req.llm_provider
            config["checkpoint_enabled"] = False

            graph = TradingAgentsGraph(req.analysts, config=config, debug=False)
            trades = []

            for ticker in req.tickers:
                for date in dates:
                    step += 1
                    asyncio.run_coroutine_threadsafe(
                        queue.put({
                            "type": "progress",
                            "step": step,
                            "total": total_steps,
                            "ticker": ticker,
                            "date": date,
                        }),
                        main_loop,
                    )
                    try:
                        final_state, decision = graph.propagate(ticker, date)
                        trade = {"ticker": ticker, "date": date, "decision": decision}
                        trades.append(trade)
                        asyncio.run_coroutine_threadsafe(
                            queue.put({"type": "trade", **trade}),
                            main_loop,
                        )
                    except Exception as e:
                        asyncio.run_coroutine_threadsafe(
                            queue.put({
                                "type": "trade_error",
                                "ticker": ticker,
                                "date": date,
                                "error": str(e),
                            }),
                            main_loop,
                        )

            decisions = [t["decision"] for t in trades]
            buy = sum(1 for d in decisions if d in ("Buy", "Overweight"))
            sell = sum(1 for d in decisions if d in ("Sell", "Underweight"))
            hold = sum(1 for d in decisions if d == "Hold")

            asyncio.run_coroutine_threadsafe(
                queue.put({
                    "type": "complete",
                    "trades": trades,
                    "stats": {
                        "total": len(trades),
                        "buy": buy,
                        "sell": sell,
                        "hold": hold,
                    },
                }),
                main_loop,
            )
        except Exception as e:
            import traceback, logging
            logging.getLogger("backtest.ws").error("Backtest run error: %s", traceback.format_exc())
            asyncio.run_coroutine_threadsafe(
                queue.put({"type": "error", "message": "Backtest failed — check server logs"}),
                main_loop,
            )
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(None), main_loop)

    future = main_loop.run_in_executor(_executor, run_sync)

    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            try:
                await websocket.send_json(item)
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    finally:
        _active_backtests -= 1
        try:
            await future
        except Exception:
            pass


class AlgoBacktestRequest(BaseModel):
    tickers: List[str]
    start_date: str
    end_date: str
    threshold: float = 100.0
    score_mode: str = "confirmed_pullback"
    freq: int = 1
    min_price: float = 5.0
    max_price: Optional[float] = None
    no_cache: bool = False
    account_size: float = 10000.0
    hold_periods: List[int] = [3, 5, 10]
    primary_hold: int = 3
    no_ml: bool = False
    no_charts: bool = True
    ml_probability_threshold: float = 0.50
    ml_large_loss_max: float = 0.35
    ml_expected_return_min: float = -0.01
    # Realistic cost model defaults (non-zero prevents fake edge in backtest)
    account_commission: float = 1.0  # $1 flat per entry and exit
    account_slippage_bps: float = 5.0  # 5 bps per side (10 bps round-trip)


@router.websocket("/ws/algo-backtest")
async def ws_algo_backtest(websocket: WebSocket):
    """Run the technical backtest engine (backtest.py) via WebSocket with live stdout streaming."""
    global _active_backtests
    await websocket.accept()
    # ── Admin auth gate (Cloudflare Access JWT verified) ──
    from web.auth import ws_require_admin
    _ws_user = await ws_require_admin(websocket)
    if _ws_user is None:
        return

    if _active_backtests >= _MAX_CONCURRENT_BACKTESTS:
        await websocket.send_json({"type": "error", "message": f"Server busy: {_active_backtests} backtest(s) already running. Try again in a moment."})
        await websocket.close()
        return

    _active_backtests += 1
    queue: asyncio.Queue = asyncio.Queue()
    main_loop = asyncio.get_running_loop()

    try:
        config_data = await websocket.receive_json()
        req = AlgoBacktestRequest(**config_data)
    except Exception as e:
        _active_backtests -= 1
        await websocket.send_json({"type": "error", "message": str(e)})
        await websocket.close()
        return

    def run_sync():
        ticker_file = None
        proc = None
        try:
            # Write tickers to temp file
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, dir=str(ROOT)) as f:
                f.write("\n".join(t.strip().upper() for t in req.tickers if t.strip()))
                ticker_file = f.name

            asyncio.run_coroutine_threadsafe(
                queue.put({
                    "type": "info",
                    "message": f"Starting algorithm backtest for {len(req.tickers)} tickers ({req.start_date} → {req.end_date})",
                }),
                main_loop,
            )

            cmd = [
                sys.executable, str(ROOT / "backtest.py"),
                "--tickers", ticker_file,
                "--start", req.start_date,
                "--end", req.end_date,
                "--threshold", str(req.threshold),
                "--score-mode", req.score_mode,
                "--freq", str(req.freq),
                "--min-price", str(req.min_price),
                "--hold-periods", *[str(h) for h in req.hold_periods],
                "--primary-hold", str(req.primary_hold),
                "--account-size", str(req.account_size),
            ]
            if req.max_price is not None:
                cmd += ["--max-price", str(req.max_price)]
            cmd += ["--ml-probability-threshold", str(req.ml_probability_threshold)]
            cmd += ["--ml-large-loss-max", str(req.ml_large_loss_max)]
            cmd += ["--ml-expected-return-min", str(req.ml_expected_return_min)]
            cmd += ["--account-commission", str(req.account_commission)]
            cmd += ["--account-slippage-bps", str(req.account_slippage_bps)]
            if req.no_cache:
                cmd.append("--no-cache")
            if req.no_ml:
                cmd += ["--no-ml-analysis"]
            if req.no_charts:
                cmd += ["--no-generate-charts"]

            proc = subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            for raw_line in proc.stdout:
                line = raw_line.rstrip()
                if not line:
                    continue
                asyncio.run_coroutine_threadsafe(
                    queue.put({"type": "log", "text": line}),
                    main_loop,
                )

            proc.wait()
            exit_code = proc.returncode

            if exit_code != 0:
                asyncio.run_coroutine_threadsafe(
                    queue.put({"type": "error", "message": f"Backtest process exited with code {exit_code}"}),
                    main_loop,
                )
            else:
                # Find newest results file (written during this run)
                result_files = sorted(
                    ROOT.glob("backtest_results_*.json"),
                    key=lambda f: f.stat().st_mtime,
                    reverse=True,
                )
                summary_data = {}
                filename = None
                if result_files:
                    filename = result_files[0].name
                    try:
                        data = json.loads(result_files[0].read_text(encoding="utf-8"))
                        meta = data.get("meta", {})
                        summ = data.get("summary", {})
                        acct = data.get("account_simulation", {})
                        acct_summ = acct.get("summary", {}) if isinstance(acct, dict) else {}
                        equity_raw = acct.get("equity_curve", []) if isinstance(acct, dict) else []
                        # Downsample equity curve to max 200 points for chart
                        step = max(1, len(equity_raw) // 200)
                        equity_curve = equity_raw[::step]

                        summary_data = {
                            "meta": {
                                "start": meta.get("start"),
                                "end": meta.get("end"),
                                "threshold": meta.get("threshold"),
                                "score_mode": meta.get("score_mode"),
                                "primary_hold": meta.get("primary_hold"),
                                "tickers_loaded": meta.get("tickers_loaded"),
                                "tickers_after_filter": meta.get("tickers_after_filter"),
                                "elapsed_seconds": meta.get("elapsed_seconds"),
                            },
                            "summary": {
                                "total_ticker_dates_scored": summ.get("total_ticker_dates_scored"),
                                "total_signals_passed": summ.get("total_signals_passed"),
                                "total_trades": summ.get("total_trades_with_outcome"),
                                "by_hold_period": summ.get("by_hold_period", {}),
                            },
                            "account_simulation": {
                                "summary": acct_summ,
                                "equity_curve": equity_curve,
                            },
                            "per_ticker_stats": data.get("per_ticker_stats", {}),
                            "yearly_analysis": data.get("yearly_analysis", {}),
                        }
                    except Exception as parse_err:
                        summary_data = {"parse_error": str(parse_err)}

                asyncio.run_coroutine_threadsafe(
                    queue.put({
                        "type": "complete",
                        "filename": filename,
                        "summary": summary_data,
                    }),
                    main_loop,
                )
        except Exception as e:
            import traceback
            asyncio.run_coroutine_threadsafe(
                queue.put({"type": "error", "message": str(e), "traceback": traceback.format_exc()}),
                main_loop,
            )
        finally:
            if ticker_file:
                try:
                    Path(ticker_file).unlink(missing_ok=True)
                except Exception:
                    pass
            asyncio.run_coroutine_threadsafe(queue.put(None), main_loop)

    future = main_loop.run_in_executor(_executor, run_sync)

    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            try:
                await websocket.send_json(item)
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    finally:
        _active_backtests -= 1
        try:
            await future
        except Exception:
            pass
