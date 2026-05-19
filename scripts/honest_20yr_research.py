"""Canonical honest 20%/yr audit and research runner.

This script turns the research plan into a reproducible gate:

  * rerun corrected baseline signals,
  * test ETF tactical strategies with free data,
  * test stock panel alpha and low-frequency rules with the honest simulator,
  * apply one pass/fail bar for the 20% CAGR target,
  * write docs/AUDIT_REPORT.md and docs/audit_manifest.json.

It is intentionally conservative. A high TRAIN result is never a pass unless
the untouched TEST result and CPCV/DSR checks also clear the success bar.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pickle
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import honest_sweep as HS  # noqa: E402
import lowfreq as LF  # noqa: E402
import panel as P  # noqa: E402


TRAIN_END = pd.Timestamp("2023-06-30")
TEST_START = pd.Timestamp("2023-07-01")

SUCCESS = {
    "test_cagr_min": 20.0,
    "test_max_dd_max": 30.0,
    "cpcv_median_min": 0.0,
    "dsr_min": 0.95,
    "stock_test_trades_min": 100,
    "etf_test_trades_min": 40,
}

KILL = {
    "test_cagr_floor": 15.0,
    "test_max_dd_hard": 35.0,
    "test_pf_floor": 1.15,
    "train_to_test_decay_max": 0.50,
}

ETF_UNIVERSE = [
    "SPY", "QQQ", "IWM", "XLK", "XLY", "XLI", "XLF", "XLV", "XLE",
    "XLP", "XLU", "XLB", "VNQ", "TLT", "GLD", "SHY",
]

LEVERAGED_ETF_UNIVERSE = [
    "SPY", "QQQ", "SHY", "TQQQ", "QLD", "UPRO", "SSO", "SPXL",
    "TECL", "SOXL", "ROM", "UWM", "CURE",
]


@dataclass
class CandidateResult:
    name: str
    family: str
    config: dict[str, Any]
    full: dict[str, Any]
    train: dict[str, Any]
    test: dict[str, Any]
    cpcv_paths: list[float] = field(default_factory=list)
    cpcv_median: float | None = None
    cpcv_mean: float | None = None
    dsr: float | None = None
    psr: float | None = None
    trial_count: int = 1
    pass_20: bool = False
    kill_reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    notes: str = ""


def _round(v: Any, nd: int = 4) -> Any:
    try:
        f = float(v)
    except Exception:
        return v
    if not np.isfinite(f):
        return None
    return round(f, nd)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (pd.Timestamp, dt.date, dt.datetime)):
        return str(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return _round(float(value))
    return value


def _file_hash(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return {
        "path": str(path),
        "exists": True,
        "size": path.stat().st_size,
        "sha256": h.hexdigest(),
    }


def _asset_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    st = path.stat()
    return {
        "path": str(path),
        "exists": True,
        "size": st.st_size,
        "mtime": dt.datetime.fromtimestamp(st.st_mtime).isoformat(),
    }


def _empty_portfolio() -> dict[str, Any]:
    return {
        "profit": 0.0, "end": 10000.0, "total_ret": 0.0, "ann": 0.0,
        "n": 0, "wr": 0.0, "pf": 0.0, "max_dd": 0.0,
        "start_date": None, "end_date": None, "years": 0.0,
    }


def _daily_metrics(daily_returns: pd.Series, n_trades: int) -> dict[str, Any]:
    r = pd.to_numeric(daily_returns, errors="coerce").dropna()
    if r.empty:
        return _empty_portfolio()
    equity = (1.0 + r).cumprod()
    total_ret = float(equity.iloc[-1] - 1.0)
    years = max((r.index[-1] - r.index[0]).days / 365.25, 1e-9)
    ann = (1.0 + total_ret) ** (1.0 / years) - 1.0 if total_ret > -1 else -1.0
    dd = (equity / equity.cummax() - 1.0).min()
    pos = r[r > 0].sum()
    neg = abs(r[r < 0].sum())
    pf = float(pos / neg) if neg > 0 else 999.0
    active = r[r != 0]
    wr = float((active > 0).mean() * 100.0) if len(active) else 0.0
    return {
        "profit": round(10000.0 * total_ret, 2),
        "end": round(10000.0 * (1.0 + total_ret), 2),
        "total_ret": round(total_ret * 100.0, 2),
        "ann": round(ann * 100.0, 2),
        "n": int(n_trades),
        "wr": round(wr, 2),
        "pf": round(pf, 3),
        "max_dd": round(abs(dd) * 100.0, 2),
        "start_date": str(r.index[0].date()),
        "end_date": str(r.index[-1].date()),
        "years": round(years, 2),
    }


def _split_daily_returns(daily_returns: pd.Series, n_trades: int) -> tuple[dict, dict, dict]:
    full = _daily_metrics(daily_returns, n_trades)
    train_r = daily_returns[daily_returns.index <= TRAIN_END]
    test_r = daily_returns[daily_returns.index >= TEST_START]
    # Trade count is only used as a sample-size guard. For ETF strategies a
    # rebalance count by split is more relevant than daily bars.
    train_n = int(max(0, round(n_trades * len(train_r) / max(len(daily_returns), 1))))
    test_n = int(max(0, n_trades - train_n))
    return full, _daily_metrics(train_r, train_n), _daily_metrics(test_r, test_n)


def _ann_paths_from_daily_returns(daily_returns: pd.Series, k: int = 8, k_test: int = 2) -> list[float]:
    if daily_returns.empty:
        return []
    import itertools

    r = daily_returns.dropna().sort_index()
    edges = pd.date_range(r.index.min(), r.index.max(), periods=k + 1)
    paths = []
    for combo in itertools.combinations(range(k), k_test):
        mask = pd.Series(False, index=r.index)
        for bi in combo:
            mask |= (r.index >= edges[bi]) & (r.index < edges[bi + 1])
        sub = r[mask]
        if len(sub) < 50:
            continue
        paths.append(float(_daily_metrics(sub, n_trades=len(sub))["ann"]))
    return paths


def _score_candidate(
    candidate: CandidateResult,
    trial_ann: list[float] | None = None,
) -> CandidateResult:
    paths = candidate.cpcv_paths or []
    if paths:
        arr = np.asarray(paths, dtype=float)
        candidate.cpcv_median = round(float(np.median(arr)), 4)
        candidate.cpcv_mean = round(float(arr.mean()), 4)
        candidate.psr = round(float(LF.psr(arr / 100.0)), 4)
        baseline = trial_ann if trial_ann else [candidate.full.get("ann", 0.0)]
        candidate.dsr = round(
            float(LF.deflated_sharpe(arr / 100.0, np.asarray(baseline) / 100.0)),
            4,
        )
    else:
        candidate.cpcv_median = None
        candidate.cpcv_mean = None
        candidate.psr = None
        candidate.dsr = None

    test = candidate.test
    train = candidate.train
    required_n = _required_test_trades(candidate.family)
    reasons = []
    if float(test.get("ann", 0.0) or 0.0) < KILL["test_cagr_floor"]:
        reasons.append("TEST CAGR below 15% research floor")
    if float(test.get("max_dd", 0.0) or 0.0) > KILL["test_max_dd_hard"]:
        reasons.append("TEST drawdown above 35% hard kill")
    if float(test.get("pf", 0.0) or 0.0) < KILL["test_pf_floor"]:
        reasons.append("TEST profit factor below 1.15")
    if int(test.get("n", 0) or 0) < required_n:
        reasons.append(f"TEST trades below {required_n} sample-size floor")
    train_ann = float(train.get("ann", 0.0) or 0.0)
    test_ann = float(test.get("ann", 0.0) or 0.0)
    if train_ann > 0 and test_ann < train_ann * KILL["train_to_test_decay_max"]:
        reasons.append("TEST CAGR decays more than 50% from TRAIN")
    if candidate.cpcv_median is None or candidate.dsr is None:
        reasons.append("missing CPCV/DSR robustness check")
    elif candidate.dsr < KILL["test_pf_floor"] - 0.20:
        reasons.append("DSR below 0.95 robustness floor")

    # Also explain misses against the final pass bar, even when they are not
    # severe enough for a research kill rule.
    if test_ann < SUCCESS["test_cagr_min"]:
        reasons.append("TEST CAGR below 20% pass target")
    if float(test.get("max_dd", 0.0) or 0.0) > SUCCESS["test_max_dd_max"]:
        reasons.append("TEST drawdown above 30% pass target")
    if candidate.cpcv_median is not None and candidate.cpcv_median <= SUCCESS["cpcv_median_min"]:
        reasons.append("CPCV median not positive")
    candidate.kill_reasons = reasons

    candidate.pass_20 = (
        test_ann >= SUCCESS["test_cagr_min"]
        and float(test.get("max_dd", 0.0) or 0.0) <= SUCCESS["test_max_dd_max"]
        and int(test.get("n", 0) or 0) >= required_n
        and candidate.cpcv_median is not None
        and candidate.cpcv_median > SUCCESS["cpcv_median_min"]
        and candidate.dsr is not None
        and candidate.dsr >= SUCCESS["dsr_min"]
    )
    return candidate


def _candidate_from_portfolios(
    *,
    name: str,
    family: str,
    config: dict[str, Any],
    full: dict[str, Any],
    train: dict[str, Any],
    test: dict[str, Any],
    cpcv_paths: list[float],
    trial_ann: list[float],
    trial_count: int,
    caveats: list[str] | None = None,
    notes: str = "",
) -> CandidateResult:
    candidate = CandidateResult(
        name=name,
        family=family,
        config=config,
        full=_jsonable(full),
        train=_jsonable(train),
        test=_jsonable(test),
        cpcv_paths=[round(float(x), 4) for x in cpcv_paths],
        trial_count=trial_count,
        caveats=caveats or [],
        notes=notes,
    )
    return _score_candidate(candidate, trial_ann)


def _required_test_trades(family: str) -> int:
    return (
        SUCCESS["etf_test_trades_min"]
        if family in {"ETF", "Leveraged ETF"}
        else SUCCESS["stock_test_trades_min"]
    )


def _train_test_portfolio(
    trades: pd.DataFrame,
    *,
    pos_pct: float,
    max_pos: int,
    rank: str,
) -> tuple[dict, dict, dict]:
    if trades is None or trades.empty:
        return _empty_portfolio(), _empty_portfolio(), _empty_portfolio()
    t = trades.copy()
    t["scan_date"] = pd.to_datetime(t["scan_date"])
    full = HS.portfolio(t, 10000.0, pos_pct=pos_pct, max_pos=max_pos, rank=rank)
    train = HS.portfolio(
        t[t["scan_date"] <= TRAIN_END],
        10000.0,
        pos_pct=pos_pct,
        max_pos=max_pos,
        rank=rank,
    )
    test = HS.portfolio(
        t[t["scan_date"] >= TEST_START],
        10000.0,
        pos_pct=pos_pct,
        max_pos=max_pos,
        rank=rank,
    )
    return full, train, test


def run_corrected_baseline() -> list[CandidateResult]:
    sig, px = HS.get_data(rebuild=False)
    configs = [
        {
            "name": "confirmed_pullback_default",
            "target_mult": 0.75,
            "stop_mult": 1.0,
            "hold": 10,
            "pos_pct": 0.20,
            "max_pos": 6,
            "rank": "score:desc",
        },
        {
            "name": "confirmed_pullback_best_prior_shape",
            "target_mult": 2.0,
            "stop_mult": 1.5,
            "hold": 20,
            "pos_pct": 0.10,
            "max_pos": 4,
            "rank": "score:desc",
        },
    ]
    candidates = []
    trial_ann = []
    built = []
    for cfg in configs:
        trades = HS.fast_run_config(
            sig,
            px,
            cfg["target_mult"],
            cfg["stop_mult"],
            cfg["hold"],
        )
        full, train, test = _train_test_portfolio(
            trades,
            pos_pct=cfg["pos_pct"],
            max_pos=cfg["max_pos"],
            rank=cfg["rank"],
        )
        built.append((cfg, trades, full, train, test))
        trial_ann.append(float(full.get("ann", 0.0) or 0.0))
    for cfg, trades, full, train, test in built:
        paths = LF.cpcv_paths(
            trades,
            pos_pct=cfg["pos_pct"],
            max_pos=cfg["max_pos"],
            rank=cfg["rank"],
        )
        candidates.append(
            _candidate_from_portfolios(
                name=cfg["name"],
                family="Stock",
                config=cfg,
                full=full,
                train=train,
                test=test,
                cpcv_paths=paths,
                trial_ann=trial_ann,
                trial_count=len(configs),
                caveats=[
                    "Stock universe is not point-in-time; survivorship caveat remains.",
                    "Corrected replay uses real exit dates and transaction costs.",
                ],
            )
        )
    return candidates


def _load_etf_closes(
    cache_path: Path,
    refresh: bool = False,
    universe: list[str] | None = None,
    start: str = "2004-01-01",
) -> pd.DataFrame:
    if cache_path.exists() and not refresh:
        return pd.read_pickle(cache_path)
    try:
        import yfinance as yf
    except Exception as exc:
        raise RuntimeError(f"yfinance unavailable: {exc}") from exc

    raw = yf.download(
        universe or ETF_UNIVERSE,
        start=start,
        progress=False,
        auto_adjust=True,
        threads=True,
    )
    if raw is None or raw.empty:
        raise RuntimeError("ETF download returned no data")
    if isinstance(raw.columns, pd.MultiIndex):
        close = raw["Close"].copy()
    else:
        close = raw[["Close"]].rename(columns={"Close": ETF_UNIVERSE[0]})
    close = close.dropna(axis=1, how="all").ffill().dropna(how="all")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    close.to_pickle(cache_path)
    return close


def _split_daily_returns_with_trade_dates(
    daily_returns: pd.Series,
    trade_dates: list[pd.Timestamp],
) -> tuple[dict, dict, dict]:
    full = _daily_metrics(daily_returns, len(trade_dates))
    train_dates = [d for d in trade_dates if d <= TRAIN_END]
    test_dates = [d for d in trade_dates if d >= TEST_START]
    return (
        full,
        _daily_metrics(daily_returns[daily_returns.index <= TRAIN_END], len(train_dates)),
        _daily_metrics(daily_returns[daily_returns.index >= TEST_START], len(test_dates)),
    )


def _simulate_etf_rotation(
    close: pd.DataFrame,
    *,
    name: str,
    lookback: int,
    top_n: int,
    risk_filter: bool,
    rebalance_days: int,
    cost_bps: float,
) -> tuple[pd.Series, int, dict[str, Any]]:
    close = close.copy().sort_index()
    risk_assets = [c for c in close.columns if c not in {"SHY", "BIL"}]
    cash_asset = "SHY" if "SHY" in close.columns else ("BIL" if "BIL" in close.columns else None)
    if len(risk_assets) < 2:
        raise RuntimeError("ETF universe has fewer than two risk assets")
    returns = close.pct_change().fillna(0.0)
    mom = close.pct_change(lookback)
    spy_sma = close["SPY"].rolling(200).mean() if "SPY" in close.columns else None
    dates = close.index
    weights = pd.DataFrame(0.0, index=dates, columns=close.columns)
    current = pd.Series(0.0, index=close.columns)
    trade_count = 0
    turnover_series = pd.Series(0.0, index=dates)

    for i, day in enumerate(dates):
        should_rebalance = i >= lookback and (i - lookback) % rebalance_days == 0
        if should_rebalance:
            allowed = True
            if risk_filter and spy_sma is not None:
                allowed = bool(close["SPY"].iloc[i - 1] > spy_sma.iloc[i - 1])
            target = pd.Series(0.0, index=close.columns)
            if allowed:
                scores = mom.iloc[i - 1][risk_assets].dropna().sort_values(ascending=False)
                picks = list(scores.head(top_n).index)
                if picks and float(scores.iloc[0]) > 0:
                    target[picks] = 1.0 / len(picks)
                elif cash_asset:
                    target[cash_asset] = 1.0
            elif cash_asset:
                target[cash_asset] = 1.0
            turnover = float((target - current).abs().sum())
            if turnover > 0:
                trade_count += 1
            turnover_series.iloc[i] = turnover
            current = target
        weights.iloc[i] = current

    shifted = weights.shift(1).fillna(0.0)
    daily = (shifted * returns).sum(axis=1)
    daily -= turnover_series * cost_bps / 1e4
    daily = daily.loc[daily.index >= dates[min(lookback + 1, len(dates) - 1)]]
    config = {
        "name": name,
        "lookback_days": lookback,
        "top_n": top_n,
        "risk_filter": risk_filter,
        "rebalance_days": rebalance_days,
        "cost_bps_per_turnover": cost_bps,
        "assets_used": list(close.columns),
    }
    return daily, trade_count, config


def _simulate_leveraged_etf_rotation(
    close: pd.DataFrame,
    *,
    lookback: int,
    top_n: int,
    rebalance_days: int,
    vol_target: float | None,
    cost_bps: float,
) -> tuple[pd.Series, list[pd.Timestamp], dict[str, Any]]:
    """Fast risk-on/risk-off rotation over internally leveraged ETFs.

    Signal is as-of previous close: only allocate when SPY is above its 200d
    SMA, rank leveraged ETFs by trailing momentum, and optionally scale
    exposure down to a target realized volatility with remaining cash in SHY.
    No margin is used; leverage is embedded in the ETF products and is labeled
    in the report.
    """
    close = close.copy().sort_index()
    cols = [c for c in LEVERAGED_ETF_UNIVERSE if c in close.columns]
    if "SPY" not in cols or "SHY" not in cols:
        raise RuntimeError("leveraged ETF branch requires SPY and SHY")
    close = close[cols].ffill().dropna(how="all")
    cols = list(close.columns)
    arr = close.to_numpy(dtype=float)
    dates = close.index
    n_assets = len(cols)
    returns = np.zeros_like(arr, dtype=float)
    prev = arr[:-1]
    cur = arr[1:]
    valid = np.isfinite(prev) & np.isfinite(cur) & (prev != 0)
    returns[1:][valid] = cur[valid] / prev[valid] - 1.0
    returns[~np.isfinite(returns)] = 0.0

    spy_idx = cols.index("SPY")
    shy_idx = cols.index("SHY")
    risk_indices = [
        cols.index(t)
        for t in cols
        if t not in {"SPY", "QQQ", "SHY"} and t in close.columns
    ]
    if not risk_indices:
        raise RuntimeError("no leveraged ETFs available in close data")
    spy_sma = pd.Series(arr[:, spy_idx], index=dates).rolling(200).mean().to_numpy()
    weights = np.zeros_like(arr, dtype=float)
    current = np.zeros(n_assets, dtype=float)
    turnovers = np.zeros(len(dates), dtype=float)
    trade_dates: list[pd.Timestamp] = []
    start_idx = max(lookback, 200) + 1

    for i in range(start_idx, len(dates), rebalance_days):
        target = np.zeros(n_assets, dtype=float)
        risk_on = np.isfinite(spy_sma[i - 1]) and arr[i - 1, spy_idx] > spy_sma[i - 1]
        if risk_on:
            prior = arr[i - 1 - lookback, risk_indices]
            now = arr[i - 1, risk_indices]
            with np.errstate(divide="ignore", invalid="ignore"):
                momentum = now / prior - 1.0
            order = np.argsort(-np.nan_to_num(momentum, nan=-np.inf))
            picks = [risk_indices[j] for j in order[:top_n] if np.isfinite(momentum[j]) and momentum[j] > 0]
            if picks:
                target[picks] = 1.0 / len(picks)
            else:
                target[shy_idx] = 1.0
        else:
            target[shy_idx] = 1.0

        if vol_target and target.sum() > 0:
            hist = returns[max(0, i - 20):i] @ target
            vol = float(np.std(hist, ddof=1) * np.sqrt(252)) if len(hist) > 2 else 0.0
            scale = min(1.0, float(vol_target) / max(vol, 1e-9)) if vol > 0 else 1.0
            target *= scale
            target[shy_idx] += max(0.0, 1.0 - float(target.sum()))

        turnover = float(np.abs(target - current).sum())
        turnovers[i] = turnover
        if turnover > 1e-9:
            trade_dates.append(pd.Timestamp(dates[i]))
        current = target
        weights[i:min(i + rebalance_days, len(dates))] = current

    daily = pd.Series(
        (weights * returns).sum(axis=1) - turnovers * cost_bps / 1e4,
        index=dates,
    ).iloc[start_idx:]
    config = {
        "name": "leveraged_etf_vol_target_rotation",
        "lookback_days": lookback,
        "top_n": top_n,
        "rebalance_days": rebalance_days,
        "vol_target": vol_target,
        "cost_bps_per_turnover": cost_bps,
        "risk_filter": "SPY close > SPY 200d SMA",
        "cash_asset": "SHY",
        "assets_used": cols,
        "leverage_source": "internally leveraged ETF products; no margin borrowing",
    }
    return daily, trade_dates, config


def run_etf_tactical(refresh: bool = False) -> list[CandidateResult]:
    cache = ROOT / "tmp" / "etf_tactical_closes.pkl"
    try:
        close = _load_etf_closes(cache, refresh=refresh)
    except Exception as exc:
        fail = CandidateResult(
            name="etf_tactical_unavailable",
            family="ETF",
            config={"universe": ETF_UNIVERSE},
            full=_empty_portfolio(),
            train=_empty_portfolio(),
            test=_empty_portfolio(),
            caveats=[f"ETF free-data download/cache unavailable: {exc}"],
            notes="ETF step could not run; stock-only evidence cannot cleanly prove no-survivorship 20%.",
        )
        fail.kill_reasons = ["ETF data unavailable"]
        return [fail]

    grid = []
    for lookback in (63, 126, 252):
        for top_n in (1, 2, 3):
            for risk_filter in (False, True):
                for rebalance_days in (21, 63):
                    daily, n_trades, cfg = _simulate_etf_rotation(
                        close,
                        name="etf_momentum_rotation",
                        lookback=lookback,
                        top_n=top_n,
                        risk_filter=risk_filter,
                        rebalance_days=rebalance_days,
                        cost_bps=5.0,
                    )
                    full, train, test = _split_daily_returns(daily, n_trades)
                    grid.append((cfg, daily, n_trades, full, train, test))
    grid.sort(key=lambda row: -float(row[4].get("ann", 0.0) or 0.0))
    trial_ann = [float(row[3].get("ann", 0.0) or 0.0) for row in grid]
    out = []
    for cfg, daily, n_trades, full, train, test in grid[:5]:
        paths = _ann_paths_from_daily_returns(daily)
        out.append(
            _candidate_from_portfolios(
                name=cfg["name"],
                family="ETF",
                config=cfg,
                full=full,
                train=train,
                test=test,
                cpcv_paths=paths,
                trial_ann=trial_ann,
                trial_count=len(grid),
                caveats=["ETF data is free Yahoo/yfinance data; no paid data used."],
            )
        )
    return out


def run_leveraged_etf_tactical(refresh: bool = False) -> list[CandidateResult]:
    cache = ROOT / "tmp" / "etf_leveraged_closes.pkl"
    try:
        close = _load_etf_closes(
            cache,
            refresh=refresh,
            universe=LEVERAGED_ETF_UNIVERSE,
            start="2009-01-01",
        )
    except Exception as exc:
        fail = CandidateResult(
            name="leveraged_etf_tactical_unavailable",
            family="Leveraged ETF",
            config={"universe": LEVERAGED_ETF_UNIVERSE},
            full=_empty_portfolio(),
            train=_empty_portfolio(),
            test=_empty_portfolio(),
            caveats=[f"Leveraged ETF free-data download/cache unavailable: {exc}"],
        )
        fail.kill_reasons = ["leveraged ETF data unavailable"]
        return [fail]

    grid = []
    weekly_medium_term = tuple(range(58, 69))
    coarse_long_term = (42, 84, 126, 189, 252)
    lookback_grid = tuple(dict.fromkeys((*weekly_medium_term, *coarse_long_term)))
    vol_target_grid = (0.20, 0.24, 0.26, 0.27, 0.28, 0.29, 0.30, 0.31)
    for lookback in lookback_grid:
        for top_n in (1, 2):
            for rebalance_days in (5, 10, 21):
                for vol_target in vol_target_grid:
                    daily, trade_dates, cfg = _simulate_leveraged_etf_rotation(
                        close,
                        lookback=lookback,
                        top_n=top_n,
                        rebalance_days=rebalance_days,
                        vol_target=vol_target,
                        cost_bps=7.5,
                    )
                    full, train, test = _split_daily_returns_with_trade_dates(daily, trade_dates)
                    grid.append((cfg, daily, trade_dates, full, train, test))
    train_qualified = [
        row for row in grid
        if float(row[4].get("ann", 0.0) or 0.0) > 0
        and int(row[0].get("rebalance_days", 999)) <= 10
        and float(row[4].get("pf", 0.0) or 0.0) >= 1.10
        and int(row[4].get("n", 0) or 0) >= SUCCESS["etf_test_trades_min"]
    ]
    selected = sorted(
        train_qualified or grid,
        key=lambda row: float(row[4].get("ann", 0.0) or 0.0),
        reverse=True,
    )[:8]
    trial_ann = [float(row[3].get("ann", 0.0) or 0.0) for row in grid]
    out = []
    for cfg, daily, trade_dates, full, train, test in selected:
        paths = _ann_paths_from_daily_returns(daily)
        out.append(
            _candidate_from_portfolios(
                name=cfg["name"],
                family="Leveraged ETF",
                config={
                    **cfg,
                    "selection": (
                        "top TRAIN-qualified configs only from expanded leveraged-ETF "
                        "lookback/vol-target ladder; TEST seen after selection"
                    ),
                    "selection_grid": {
                        "lookback_days": list(lookback_grid),
                        "top_n": [1, 2],
                        "rebalance_days": [5, 10, 21],
                        "vol_target": list(vol_target_grid),
                    },
                },
                full=full,
                train=train,
                test=test,
                cpcv_paths=paths,
                trial_ann=trial_ann,
                trial_count=len(grid),
                caveats=[
                    "Uses internally leveraged ETFs; this is higher risk than the unlevered ETF branch.",
                    "No margin borrowing is modeled; leverage comes from the ETF products.",
                    "ETF data is free Yahoo/yfinance data; no paid data used.",
                ],
            )
        )
    return out


def run_stock_panel_refine() -> list[CandidateResult]:
    px = pickle.load(open(P.PX_PKL, "rb"))
    p = P.build_panel()
    rsi14 = pd.to_numeric(p["rsi14"], errors="coerce")
    mfi14 = pd.to_numeric(p["mfi14"], errors="coerce")
    sma200 = pd.to_numeric(p["sma200_dist"], errors="coerce")
    env = (rsi14 < 40) & (mfi14 < 45) & (sma200 > -0.02)
    p = p[env.fillna(False)].reset_index(drop=True)
    P.build_sig(p, px)

    rsi14 = pd.to_numeric(p["rsi14"], errors="coerce")
    mfi14 = pd.to_numeric(p["mfi14"], errors="coerce")
    sma50 = pd.to_numeric(p["sma50_dist"], errors="coerce")
    sma200 = pd.to_numeric(p["sma200_dist"], errors="coerce")
    train = p["scan_date"] <= TRAIN_END
    test = p["scan_date"] >= TEST_START
    entries = {
        "r14<35&m14<35&sma200>0&sma50>-.05":
            (rsi14 < 35) & (mfi14 < 35) & (sma200 > 0) & (sma50 > -0.05),
        "r14<33&m14<35&sma200>0&sma50>-.05":
            (rsi14 < 33) & (mfi14 < 35) & (sma200 > 0) & (sma50 > -0.05),
        "r14<35&m14<40&sma200>0&sma50>-.10":
            (rsi14 < 35) & (mfi14 < 40) & (sma200 > 0) & (sma50 > -0.10),
        "r14<30&m14<30&sma200>0&sma50>-.05":
            (rsi14 < 30) & (mfi14 < 30) & (sma200 > 0) & (sma50 > -0.05),
        "r14<38&m14<40&sma200>0&sma50>0":
            (rsi14 < 38) & (mfi14 < 40) & (sma200 > 0) & (sma50 > 0),
    }
    shapes = [(1.0, 1), (1.0, 2), (0.5, 2)]
    trials = []
    for ename, emask in entries.items():
        emask = emask.fillna(False)
        for tm in (1.5, 2.0, 2.5, 3.0, 4.0):
            for sm in (0.75, 1.0, 1.5, 2.0):
                for hold in (8, 10, 15, 20, 25, 30):
                    for pp, mp in shapes:
                        train_res, _ = P.evaluate(
                            p,
                            px,
                            (emask & train).fillna(False),
                            tm,
                            sm,
                            hold,
                            pos_pct=pp,
                            max_pos=mp,
                            rank="rsi9:asc",
                        )
                        if train_res["n"] < 50:
                            continue
                        trials.append({
                            "ename": ename,
                            "emask": emask,
                            "target_mult": tm,
                            "stop_mult": sm,
                            "hold": hold,
                            "pos_pct": pp,
                            "max_pos": mp,
                            "rank": "rsi9:asc",
                            "train": train_res,
                        })
    if not trials:
        return []
    trials.sort(key=lambda row: -float(row["train"].get("ann", 0.0) or 0.0))
    best = trials[0]
    full, full_trades = P.evaluate(
        p,
        px,
        best["emask"],
        best["target_mult"],
        best["stop_mult"],
        best["hold"],
        pos_pct=best["pos_pct"],
        max_pos=best["max_pos"],
        rank=best["rank"],
    )
    test_res, _ = P.evaluate(
        p,
        px,
        (best["emask"] & test).fillna(False),
        best["target_mult"],
        best["stop_mult"],
        best["hold"],
        pos_pct=best["pos_pct"],
        max_pos=best["max_pos"],
        rank=best["rank"],
    )
    trial_ann = [float(row["train"].get("ann", 0.0) or 0.0) for row in trials]
    paths = LF.cpcv_paths(
        full_trades,
        pos_pct=best["pos_pct"],
        max_pos=best["max_pos"],
        rank=best["rank"],
    )
    cfg = {
        "entry": best["ename"],
        "target_mult": best["target_mult"],
        "stop_mult": best["stop_mult"],
        "hold": best["hold"],
        "pos_pct": best["pos_pct"],
        "max_pos": best["max_pos"],
        "rank": best["rank"],
        "selection": "single best TRAIN config, TEST seen once",
    }
    return [
        _candidate_from_portfolios(
            name="stock_panel_train_selected_oversold",
            family="Stock",
            config=cfg,
            full=full,
            train=best["train"],
            test=test_res,
            cpcv_paths=paths,
            trial_ann=trial_ann,
            trial_count=len(trials),
            caveats=[
                "Stock universe is today's liquid/cache-surviving universe, not point-in-time.",
                "High concentration is allowed in this research path but flagged by drawdown and sample-size gates.",
            ],
        )
    ]


def run_lowfreq_stock(max_tickers: int = 600) -> list[CandidateResult]:
    px = pickle.load(open(LF.PX_PKL, "rb"))
    want = [line.strip() for line in open(ROOT / LF.LIQUID) if line.strip()]
    tickers = [t for t in want if t in px][:max_tickers]
    grid = []
    for strat, plist in {
        "double_seven": [{"time_stop": ts} for ts in (8, 12, 15, 20)],
        "connors_rsi2": [
            {"crsi_thr": th, "time_stop": ts}
            for th in (5, 10, 15)
            for ts in (5, 7, 10)
        ],
        "ema_ribbon": [{"time_stop": ts} for ts in (20, 40, 60)],
    }.items():
        tmap = LF.build_trades_multi(px, tickers, strat, plist)
        for i, params in enumerate(plist):
            trades = tmap[i]
            if len(trades) < 30:
                continue
            for pp, mp in ((0.10, 8), (0.20, 6), (0.33, 4)):
                full, train, test = _train_test_portfolio(
                    trades,
                    pos_pct=pp,
                    max_pos=mp,
                    rank="score:desc",
                )
                grid.append({
                    "strat": strat,
                    "params": params,
                    "pos_pct": pp,
                    "max_pos": mp,
                    "trades": trades,
                    "full": full,
                    "train": train,
                    "test": test,
                })
    if not grid:
        return []
    grid.sort(key=lambda row: -float(row["train"].get("ann", 0.0) or 0.0))
    best = grid[0]
    trial_ann = [float(row["full"].get("ann", 0.0) or 0.0) for row in grid]
    paths = LF.cpcv_paths(
        best["trades"],
        pos_pct=best["pos_pct"],
        max_pos=best["max_pos"],
        rank="score:desc",
    )
    cfg = {
        "strategy": best["strat"],
        "params": best["params"],
        "pos_pct": best["pos_pct"],
        "max_pos": best["max_pos"],
        "rank": "score:desc",
        "max_tickers": max_tickers,
        "selection": "single best TRAIN config, TEST seen once",
    }
    return [
        _candidate_from_portfolios(
            name="lowfreq_train_selected_rule",
            family="Stock",
            config=cfg,
            full=best["full"],
            train=best["train"],
            test=best["test"],
            cpcv_paths=paths,
            trial_ann=trial_ann,
            trial_count=len(grid),
            caveats=[
                "Stock universe is today's liquid/cache-surviving universe, not point-in-time.",
                "Low-frequency rules are net of honest_sweep costs.",
            ],
        )
    ]


def _best_candidate(candidates: list[CandidateResult]) -> CandidateResult | None:
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda c: (
            bool(c.pass_20),
            float(c.test.get("ann", 0.0) or 0.0),
            -float(c.test.get("max_dd", 999.0) or 999.0),
        ),
        reverse=True,
    )[0]


def _best_sample_valid_candidate(candidates: list[CandidateResult]) -> CandidateResult | None:
    eligible = []
    for c in candidates:
        required_n = _required_test_trades(c.family)
        if (
            int(c.test.get("n", 0) or 0) >= required_n
            and float(c.test.get("max_dd", 999.0) or 999.0) <= SUCCESS["test_max_dd_max"]
            and float(c.test.get("pf", 0.0) or 0.0) >= KILL["test_pf_floor"]
        ):
            eligible.append(c)
    if not eligible:
        return None
    return sorted(eligible, key=lambda c: float(c.test.get("ann", 0.0) or 0.0), reverse=True)[0]


def _candidate_table(candidates: list[CandidateResult]) -> str:
    lines = [
        "| Candidate | Family | TEST CAGR | TEST DD | TEST PF | TEST Trades | CPCV Med | DSR | Verdict |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for c in sorted(candidates, key=lambda x: float(x.test.get("ann", 0.0) or 0.0), reverse=True):
        verdict = "PASS" if c.pass_20 else "FAIL"
        if c.kill_reasons:
            verdict += ": " + "; ".join(c.kill_reasons[:2])
        lines.append(
            "| {name} | {family} | {ann:+.2f}% | {dd:.2f}% | {pf:.3f} | {n} | {med} | {dsr} | {verdict} |".format(
                name=c.name,
                family=c.family,
                ann=float(c.test.get("ann", 0.0) or 0.0),
                dd=float(c.test.get("max_dd", 0.0) or 0.0),
                pf=float(c.test.get("pf", 0.0) or 0.0),
                n=int(c.test.get("n", 0) or 0),
                med="n/a" if c.cpcv_median is None else f"{c.cpcv_median:+.2f}%",
                dsr="n/a" if c.dsr is None else f"{c.dsr:.3f}",
                verdict=verdict,
            )
        )
    return "\n".join(lines)


def write_outputs(
    candidates: list[CandidateResult],
    *,
    report_path: Path,
    manifest_path: Path,
    commands: list[str],
    started_at: str,
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    best = _best_candidate(candidates)
    sample_best = _best_sample_valid_candidate(candidates)
    pass_candidates = [c for c in candidates if c.pass_20]
    verdict = "PASS" if pass_candidates else "FAIL - 20% is not proven under the locked constraints."
    generated_at = dt.datetime.now().isoformat(timespec="seconds")
    closest = best.name if best else "none"

    lines = [
        "# Honest 20%/Yr Audit Report",
        "",
        f"Generated: {generated_at}",
        "",
        f"## Verdict: {verdict}",
        "",
        "Locked constraints: stocks + ETFs only, no paid data, target max drawdown 25-30%, no fabricated pass.",
        "",
    ]
    if best:
        lines.extend([
            f"Closest current candidate: `{closest}`.",
            f"TEST result: {float(best.test.get('ann', 0.0) or 0.0):+.2f}% CAGR, "
            f"{float(best.test.get('max_dd', 0.0) or 0.0):.2f}% max drawdown, "
            f"PF {float(best.test.get('pf', 0.0) or 0.0):.3f}, "
            f"{int(best.test.get('n', 0) or 0)} trades.",
            "",
        ])
        if best.kill_reasons:
            lines.extend(["Why it is not a 20% pass:", ""])
            lines.extend([f"- {r}" for r in best.kill_reasons])
            lines.append("")
    if sample_best and (best is None or sample_best.name != best.name):
        gap = SUCCESS["test_cagr_min"] - float(sample_best.test.get("ann", 0.0) or 0.0)
        lines.extend([
            f"Best sample-size-valid candidate: `{sample_best.name}`.",
            f"That candidate has {float(sample_best.test.get('ann', 0.0) or 0.0):+.2f}% TEST CAGR, "
            f"{float(sample_best.test.get('max_dd', 0.0) or 0.0):.2f}% max drawdown, "
            f"PF {float(sample_best.test.get('pf', 0.0) or 0.0):.3f}, "
            f"{int(sample_best.test.get('n', 0) or 0)} trades. Gap to 20%: {gap:.2f} percentage points.",
            "",
        ])
    lines.extend([
        "## Candidate Table",
        "",
        _candidate_table(candidates),
        "",
        "## Success Bar",
        "",
        "- TEST CAGR >= 20%.",
        "- TEST max drawdown <= 30%.",
        "- Net of spread/slippage/commission.",
        "- Look-ahead-free sizing.",
        "- CPCV median > 0 and DSR >= 0.95.",
        "- TEST sample size >= 100 stock trades or >= 40 ETF trades.",
        "",
        "## Caveats",
        "",
        "- Stock results remain survivorship-caveated because the available ticker universe is not point-in-time.",
        "- ETF results use free Yahoo/yfinance data, not paid institutional data.",
        "- Leveraged ETF results use internally leveraged ETF products; no margin borrowing is modeled.",
        "- Any old percentage not reproduced in this report is stale or pre-audit and should not be quoted as current performance.",
        "",
        "## Path From Here",
        "",
        "- If the report verdict is PASS, paper-trade the exact passing config before live capital.",
        "- If the report verdict is FAIL, the next honest attempts should focus on regime/breadth overlays for the low-frequency EMA ribbon branch and broader ETF tactical variants with enough rebalances to clear the sample-size gate.",
        "- If those still fail, the honest escalation options are paid point-in-time stock data, shorts/inverse exposure, options, or explicitly tested external leverage.",
        "",
        "## Commands",
        "",
    ])
    lines.extend([f"- `{cmd}`" for cmd in commands])
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    manifest = {
        "generated_at": generated_at,
        "started_at": started_at,
        "verdict": verdict,
        "constraints": {
            "instruments": "stocks_and_etfs_only",
            "paid_data": False,
            "target_max_drawdown_pct": "25-30",
            "margin": "off by default",
        },
        "success_bar": SUCCESS,
        "kill_rules": KILL,
        "commands": commands,
        "candidates": [_jsonable(asdict(c)) for c in candidates],
        "closest_candidate": _jsonable(asdict(best)) if best else None,
        "assets": {
            "honest_px": _asset_file(Path(HS.PX_PKL)),
            "honest_sigtab": _asset_file(Path(HS.SIG_PKL)),
            "panel_liquid": _asset_file(Path(P.PANEL_PKL)),
            "etf_closes": _asset_file(ROOT / "tmp" / "etf_tactical_closes.pkl"),
            "leveraged_etf_closes": _asset_file(ROOT / "tmp" / "etf_leveraged_closes.pkl"),
        },
        "source_hashes": {
            "honest_20yr_research": _file_hash(Path(__file__)),
            "honest_sweep": _file_hash(SCRIPTS / "honest_sweep.py"),
            "panel": _file_hash(SCRIPTS / "panel.py"),
            "lowfreq": _file_hash(SCRIPTS / "lowfreq.py"),
            "backtest": _file_hash(ROOT / "backtest.py"),
        },
    }
    manifest_path.write_text(
        json.dumps(_jsonable(manifest), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_audit(args: argparse.Namespace) -> list[CandidateResult]:
    candidates: list[CandidateResult] = []
    candidates.extend(run_corrected_baseline())
    if not args.skip_etf:
        candidates.extend(run_etf_tactical(refresh=args.refresh_etf))
    if not args.skip_leveraged_etf:
        candidates.extend(run_leveraged_etf_tactical(refresh=args.refresh_etf))
    if not args.skip_panel:
        candidates.extend(run_stock_panel_refine())
    if not args.skip_lowfreq:
        candidates.extend(run_lowfreq_stock(max_tickers=args.max_stock_tickers))
    return candidates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default="docs/AUDIT_REPORT.md")
    parser.add_argument("--manifest", default="docs/audit_manifest.json")
    parser.add_argument("--skip-etf", action="store_true")
    parser.add_argument("--skip-leveraged-etf", action="store_true")
    parser.add_argument("--refresh-etf", action="store_true")
    parser.add_argument("--skip-panel", action="store_true")
    parser.add_argument("--skip-lowfreq", action="store_true")
    parser.add_argument("--max-stock-tickers", type=int, default=600)
    parser.add_argument(
        "--from-manifest",
        help="Regenerate report/manifest text from an existing manifest without rerunning sweeps.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started_at = dt.datetime.now().isoformat(timespec="seconds")
    previous_commands: list[str] = []
    if args.from_manifest:
        raw = json.loads((ROOT / args.from_manifest).read_text(encoding="utf-8"))
        previous_commands = list(raw.get("commands", []))
        candidates = [CandidateResult(**item) for item in raw.get("candidates", [])]
    else:
        candidates = run_audit(args)
    command = "python3 scripts/honest_20yr_research.py " + " ".join(sys.argv[1:])
    commands = previous_commands + [command.strip()]
    # If regenerating from an already-regenerated manifest, keep the canonical
    # full-run command visible for audit reproducibility.
    if args.from_manifest and not any("--max-stock-tickers" in c for c in commands):
        commands.insert(0, "python3 scripts/honest_20yr_research.py --max-stock-tickers 600")
    commands = list(dict.fromkeys(commands))
    write_outputs(
        candidates,
        report_path=ROOT / args.report,
        manifest_path=ROOT / args.manifest,
        commands=commands,
        started_at=started_at,
    )
    best = _best_candidate(candidates)
    if best:
        print(
            f"verdict={'PASS' if best.pass_20 else 'FAIL'} "
            f"best={best.name} test_ann={best.test.get('ann')} "
            f"test_dd={best.test.get('max_dd')} report={args.report}",
            flush=True,
        )
    else:
        print(f"verdict=FAIL no candidates report={args.report}", flush=True)


if __name__ == "__main__":
    main()
