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

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

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
            try:
                tickers = load_tickers(tickers_file)
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
        import traceback
        emit({"type": "error", "message": str(e), "traceback": traceback.format_exc()})
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
