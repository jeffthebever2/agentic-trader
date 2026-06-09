"""Live algorithm scanner — runs the same score_at algorithm as backtest.py
on today's data, streams results via WebSocket as each ticker is scored.
"""
import asyncio
import concurrent.futures
import datetime as dt
import sys
from pathlib import Path
from typing import Optional, List

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from web.auth import get_current_user

router = APIRouter()
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

# ── helpers ──────────────────────────────────────────────────────────────────

def _emit(queue, main_loop, msg: dict):
    asyncio.run_coroutine_threadsafe(queue.put(msg), main_loop)


def _is_market_day(d: dt.date) -> bool:
    return d.weekday() < 5  # Mon-Fri; holidays not checked


def _last_trading_day() -> dt.date:
    from zoneinfo import ZoneInfo
    ny = dt.datetime.now(ZoneInfo("America/New_York"))
    d = ny.date()
    # If before 4 PM ET, use yesterday; if weekend back to Friday
    if ny.hour < 16:
        d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


# ── scan worker ──────────────────────────────────────────────────────────────

def _run_scan(cfg: dict, queue: asyncio.Queue, main_loop: asyncio.AbstractEventLoop):
    emit = lambda msg: _emit(queue, main_loop, msg)

    try:
        import pandas as pd
        import yfinance as yf
        from backtest import (
            MIN_HISTORY, SECTOR_ETFS,
            precompute, score_at, load_tickers,
            build_spy_regime, build_vix_regime,
            build_vix_term_structure, build_sector_breadth,
        )
        from scripts.paper_trade_today import (
            download_daily_history, clean_daily_frame,
            regime_value, numeric_series_value, spy_gate_values,
            load_model_bundle, predict_ml, DEFAULT_MODEL_PATHS,
        )

        trade_date = _last_trading_day()
        emit({"type": "info", "message": f"Scanning as of {trade_date} (last trading day)"})

        # ── load tickers ──────────────────────────────────────────────────
        tickers_input = cfg.get("tickers", [])
        tickers_file  = cfg.get("tickers_file", "")
        if tickers_file:
            _tf = str(tickers_file)
            if ".." in _tf or _tf.startswith("/") or _tf.startswith("~"):
                emit({"type": "error", "message": "Invalid tickers_file path"})
                return
            try:
                tickers = load_tickers(_tf)
            except Exception as e:
                emit({"type": "error", "message": f"Cannot load tickers file: {e}"})
                return
        elif isinstance(tickers_input, list) and tickers_input:
            tickers = [t.strip().upper() for t in tickers_input if t.strip()]
        else:
            emit({"type": "error", "message": "No tickers provided — supply tickers list or tickers_file path"})
            return

        max_t = int(cfg.get("max_tickers", 0))
        if max_t > 0:
            tickers = tickers[:max_t]
        tickers = list(dict.fromkeys(tickers))

        emit({"type": "info", "message": f"Loaded {len(tickers)} tickers", "total": len(tickers)})

        score_mode  = cfg.get("score_mode", "breakout")
        threshold   = float(cfg.get("threshold", 65.0))
        target_mult = float(cfg.get("target_mult", 2.0))
        stop_mult   = float(cfg.get("stop_mult", 1.0))
        use_ml      = bool(cfg.get("use_ml", True))
        ml_prob_min = float(cfg.get("ml_prob_min", 0.50))
        lookback    = int(cfg.get("lookback_days", 420))
        batch_size  = int(cfg.get("batch_size", 200))
        benchmark   = cfg.get("benchmark", "SPY")

        # ── download data ────────────────────────────────────────────────
        start = trade_date - dt.timedelta(days=lookback)
        end   = trade_date + dt.timedelta(days=2)
        emit({"type": "info", "message": f"Downloading {len(tickers)} tickers + context data…"})

        market_symbols = list(dict.fromkeys(tickers + [benchmark] + list(SECTOR_ETFS)))
        raw = download_daily_history(market_symbols, start, end, batch_size)

        spy_raw = raw.get(benchmark)
        spy_df  = clean_daily_frame(spy_raw, trade_date) if spy_raw is not None else None
        spy_regime = build_spy_regime(spy_df) if spy_df is not None and len(spy_df) >= 200 else pd.Series(dtype=str)

        try:
            vix_raw  = yf.download("^VIX",  start=start, end=end, progress=False, auto_adjust=True)
            vix3m_raw = yf.download("^VIX3M", start=start, end=end, progress=False, auto_adjust=True)
            vix_df   = clean_daily_frame(vix_raw,  trade_date) if vix_raw  is not None and len(vix_raw)  > 5 else None
            vix3m_df = clean_daily_frame(vix3m_raw, trade_date) if vix3m_raw is not None and len(vix3m_raw) > 5 else None
        except Exception:
            vix_df = vix3m_df = None
        vix_regime = build_vix_regime(vix_df)   if vix_df   is not None and not vix_df.empty   else None
        vix_ts     = build_vix_term_structure(vix_df, vix3m_df) if vix_df is not None and vix3m_df is not None else None

        sector_dfs = {
            t: clean_daily_frame(raw[t], trade_date)
            for t in SECTOR_ETFS if raw.get(t) is not None
        }
        sector_breadth = build_sector_breadth(sector_dfs) if sector_dfs else None

        # ── load ML model ─────────────────────────────────────────────────
        bundle = None
        if use_ml:
            for p in DEFAULT_MODEL_PATHS:
                full = ROOT / p
                if full.exists():
                    try:
                        bundle = load_model_bundle(full, disabled=False)
                        emit({"type": "info", "message": f"ML model loaded ({full.name})"})
                    except Exception:
                        pass
                    break
            if bundle is None:
                emit({"type": "info", "message": "No ML model found — ML gate disabled"})

        # ── scan tickers ──────────────────────────────────────────────────
        emit({"type": "info", "message": "Scoring tickers…"})
        results = []
        scanned = 0
        passed  = 0

        for ticker in tickers:
            scanned += 1
            df_raw = raw.get(ticker)
            if df_raw is None:
                continue
            df = clean_daily_frame(df_raw, trade_date)
            if df is None or len(df) <= MIN_HISTORY:
                continue

            pos    = len(df) - 1
            as_of  = pd.Timestamp(df.index[pos])

            try:
                pc     = precompute(df)
                regime = regime_value(spy_regime, as_of)
                vix_r  = regime_value(vix_regime, as_of) if vix_regime is not None else "unknown"
                vix_ts_val = numeric_series_value(vix_ts, as_of)
                sb_val     = numeric_series_value(sector_breadth, as_of)
                gate_vals  = spy_gate_values(spy_df, as_of)

                score, signals = score_at(
                    pc, df, pos,
                    target_mult=target_mult,
                    stop_mult=stop_mult,
                    regime=regime,
                    vix_reg=vix_r,
                    vix_ts=vix_ts_val,
                    sector_breadth=sb_val,
                    score_mode=score_mode,
                    **gate_vals,
                )
            except Exception:
                continue

            if not signals:
                continue

            # Emit progress every 50 tickers
            if scanned % 50 == 0:
                emit({"type": "progress", "scanned": scanned, "total": len(tickers), "passed": passed})

            if score < threshold:
                continue

            # ML gate
            ml_data = {}
            ml_pass = True
            if bundle is not None:
                try:
                    row = {
                        "ticker": ticker,
                        "score": score,
                        "signal_date": str(trade_date),
                        "entry": signals.get("entry", 0),
                        **signals,
                    }
                    ml_data = predict_ml(row, bundle)
                    win_prob = ml_data.get("win_probability")
                    if win_prob is not None and win_prob < ml_prob_min:
                        ml_pass = False
                except Exception:
                    pass

            passed += 1
            result = {
                "ticker": ticker,
                "score": round(score, 1),
                "entry": round(signals.get("entry", 0), 2),
                "target": round(signals.get("target", 0), 2),
                "stop": round(signals.get("stop", 0), 2),
                "atr": round(signals.get("atr", 0), 3),
                "atr_pct": round(signals.get("atr_pct", 0) * 100, 2),
                "risk_reward": round(
                    (signals.get("target", 0) - signals.get("entry", 0)) /
                    max(signals.get("entry", 0) - signals.get("stop", 0), 0.01),
                    2
                ),
                "regime": regime,
                "vix_regime": vix_r,
                "rsi9": round(signals.get("rsi9", 0), 1),
                "rsi14": round(signals.get("rsi14", 0), 1),
                "macd_hist": round(signals.get("macd_hist", 0), 4),
                "vol_ratio_20d": round(signals.get("vol_ratio_20d") or 0, 2),
                "ml_win_prob": round(ml_data.get("win_probability", 0) * 100, 1) if ml_data.get("win_probability") is not None else None,
                "ml_expected_return": round(ml_data.get("expected_return", 0) * 100, 2) if ml_data.get("expected_return") is not None else None,
                "ml_large_loss_prob": round(ml_data.get("large_loss_probability", 0) * 100, 1) if ml_data.get("large_loss_probability") is not None else None,
                "ml_pass": ml_pass,
                "gate_status": signals.get("confirmed_pullback_gates", ""),
            }
            results.append(result)
            emit({"type": "signal", **result})

        # Final sorted summary
        results.sort(key=lambda r: (r["ml_pass"], r["score"]), reverse=True)
        emit({
            "type": "complete",
            "scanned": scanned,
            "passed": passed,
            "total_tickers": len(tickers),
            "results": results,
            "trade_date": str(trade_date),
            "score_mode": score_mode,
            "threshold": threshold,
        })

    except Exception as e:
        import traceback, logging
        logging.getLogger("scanner.ws").error("Scanner run error: %s", traceback.format_exc())
        emit({"type": "error", "message": "Scan failed — check server logs"})
    finally:
        _emit(queue, main_loop, None)


# ── WebSocket endpoint ────────────────────────────────────────────────────────

class ScanRequest(BaseModel):
    tickers: Optional[List[str]] = None
    tickers_file: Optional[str] = "all_tickers.txt"
    score_mode: str = "breakout"
    threshold: float = 65.0
    target_mult: float = 2.0
    stop_mult: float = 1.0
    use_ml: bool = True
    ml_prob_min: float = 0.50
    max_tickers: int = 0
    lookback_days: int = 420
    benchmark: str = "SPY"


@router.websocket("/ws/scanner/scan")
async def ws_scanner(websocket: WebSocket):
    await websocket.accept()
    # ── Admin auth gate (Cloudflare Access JWT verified) ──
    from web.auth import ws_require_admin
    _ws_user = await ws_require_admin(websocket)
    if _ws_user is None:
        return

    queue: asyncio.Queue = asyncio.Queue()
    main_loop = asyncio.get_running_loop()

    try:
        cfg = await websocket.receive_json()
    except Exception as e:
        await websocket.send_json({"type": "error", "message": str(e)})
        await websocket.close()
        return

    future = main_loop.run_in_executor(_executor, _run_scan, cfg, queue, main_loop)

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
        try:
            await future
        except Exception:
            pass


class ScreenRequest(BaseModel):
    tickers: List[str]
    date: Optional[str] = None  # YYYY-MM-DD; defaults to last trading day
    mode: str = "confirmed_pullback"
    threshold: float = 50.0


@router.post("/scanner/screen")
async def scanner_screen(req: ScreenRequest, _user: dict = Depends(get_current_user)):
    """Synchronous score for a small set of tickers on a specific date.
    Uses the same algorithm as the backtest scanner.
    """
    def _run_sync():
        import datetime as dt
        import pandas as pd
        try:
            import yfinance as yf
            from backtest import precompute, score_at, build_spy_regime, build_vix_regime
        except ImportError as e:
            return {"error": str(e), "results": []}

        if req.date:
            try:
                trade_date = dt.date.fromisoformat(req.date)
            except ValueError:
                trade_date = _last_trading_day()
        else:
            trade_date = _last_trading_day()

        tickers = [t.strip().upper() for t in req.tickers if t.strip()][:50]
        if not tickers:
            return {"results": []}

        lookback_start = trade_date - dt.timedelta(days=600)
        all_tickers = list(set(tickers + ["SPY", "^VIX"]))
        try:
            raw = yf.download(
                all_tickers,
                start=lookback_start.isoformat(),
                end=(trade_date + dt.timedelta(days=2)).isoformat(),
                auto_adjust=True,
                progress=False,
                threads=True,
            )
        except Exception as e:
            return {"error": f"Download failed: {e}", "results": []}

        if isinstance(raw.columns, pd.MultiIndex):
            prices = {col: raw[col] for col in raw.columns.get_level_values(0).unique()}
        else:
            prices = {c: raw[[c]].rename(columns={c: tickers[0]}) for c in raw.columns}

        close_all = prices.get("Close", pd.DataFrame())
        spy_s = close_all.get("SPY", pd.Series(dtype=float))
        vix_s = close_all.get("^VIX", pd.Series(dtype=float))
        spy_regime = build_spy_regime(spy_s) if not spy_s.empty else {}
        vix_regime = build_vix_regime(vix_s) if not vix_s.empty else {}

        results = []
        for ticker in tickers:
            try:
                df = pd.DataFrame({
                    "Open": prices.get("Open", pd.DataFrame()).get(ticker, pd.Series()),
                    "High": prices.get("High", pd.DataFrame()).get(ticker, pd.Series()),
                    "Low": prices.get("Low", pd.DataFrame()).get(ticker, pd.Series()),
                    "Close": prices.get("Close", pd.DataFrame()).get(ticker, pd.Series()),
                    "Volume": prices.get("Volume", pd.DataFrame()).get(ticker, pd.Series()),
                }).dropna()
                if df.empty:
                    results.append({"ticker": ticker, "error": "No data"})
                    continue
                computed = precompute(df)
                sc = score_at(computed, trade_date, spy_regime=spy_regime, vix_regime=vix_regime, mode=req.mode)
                if sc is not None:
                    results.append({
                        "ticker": ticker,
                        "score": round(sc.score, 1),
                        "entry": round(sc.entry, 2) if sc.entry else None,
                        "target": round(sc.target, 2) if sc.target else None,
                        "stop": round(sc.stop, 2) if sc.stop else None,
                        "rr": round(sc.rr, 2) if sc.rr else None,
                        "atr_pct": round(sc.atr_pct * 100, 2) if sc.atr_pct else None,
                        "gate": getattr(sc, "gate_status", None),
                        "passes": sc.score >= req.threshold,
                    })
                else:
                    results.append({"ticker": ticker, "score": None, "passes": False, "note": "No signal"})
            except Exception as e:
                results.append({"ticker": ticker, "error": str(e)})
        return {"date": trade_date.isoformat(), "results": results}

    try:
        result = await asyncio.get_running_loop().run_in_executor(_executor, _run_sync)
    except Exception as e:
        result = {"error": str(e), "results": []}
    return result


@router.get("/scanner/ticker-files")
async def list_ticker_files_compat():
    """Compatibility alias for /scanner/tickers — returns files list."""
    files = []
    for p in ROOT.glob("*.txt"):
        try:
            lines = [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
            if lines and all(1 <= len(l) <= 12 and l.replace(".", "").replace("-", "").isalnum() for l in lines[:5]):
                files.append(p.name)
        except Exception:
            pass
    return {"files": sorted(files)}


@router.get("/scanner/tickers")
async def list_ticker_files():
    """Return available ticker list files in project root."""
    files = []
    for p in ROOT.glob("*.txt"):
        try:
            lines = [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
            if lines and all(1 <= len(l) <= 12 and l.replace(".", "").replace("-", "").isalnum() for l in lines[:5]):
                files.append({"name": p.name, "path": str(p), "count": len(lines)})
        except Exception:
            pass
    return {"files": sorted(files, key=lambda f: f["count"], reverse=True)}
