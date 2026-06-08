# Implementation Report: Reference Library Analysis

**Generated:** 2026-06-07  
**Author:** AI Analysis Pass  
**Zips analyzed:**
- `backtesting.py-master.zip` (kernc/backtesting.py)
- `FinancePy-master.zip` (domokane/FinancePy)
- `machine-learning-for-trading-main.zip` (stefan-jansen/machine-learning-for-trading, "ML4T")
- `financial-machine-learning-master.zip` (firmai/financial-machine-learning — curated research wiki)
- `PatternPy-main.zip` (twopirllc/PatternPy)

**Reference libs stored at:** `.external_research/` (excluded from git tracking)

---

## Executive Summary

Five libraries were analyzed against the current codebase. The project already has strong foundations: HRP optimizer, triple-barrier labeling, BreakoutScanner, PositionSizer with Kelly, PreTradeGate, PredictionLedger, CalibrationBuckets, DailyAudit, and a Qlib integration layer.

The gaps that these libraries directly fill, ranked by expected alpha/risk-reduction impact:

| Rank | Source | Component | Impact |
|------|--------|-----------|--------|
| 1 | backtesting.py | Slippage/commission model + multi-metric tearsheet | Closes the "paper vs live" cost gap |
| 2 | ML4T ch6/8 | Deflated Sharpe Ratio (DSR) for backtest selection | Eliminates backtest overfitting bias |
| 3 | PatternPy | Chart pattern detection layer on BreakoutScanner | Adds 8 confirmatory signals for free |
| 4 | ML4T ch22 | RL trading environment scaffold (OpenAI Gym) | Foundation for `train_rl_agent.py` improvements |
| 5 | FinancePy | Black-Scholes analytics: IV, greeks, protective puts | Tail-risk pricing without external library |
| 6 | AFML wiki | Research index: CVaR, CPCV, sequential bootstrap | Roadmap items already documented elsewhere |

---

## 1. backtesting.py-master

**Source:** kernc/backtesting.py  
**Files of interest:** `backtesting/backtesting.py`, `backtesting/_stats.py`, `backtesting/lib.py`

### What it does

Pure-Python vectorized backtesting engine. Key design: `Strategy.init()` + `Strategy.next()` pattern, a `_Broker` that simulates fill at next bar open or close, and a comprehensive `compute_stats()` function that produces 25+ metrics including Kelly Criterion inline.

### What we already have

`tradingagents/backtesting/backtest_engine.py` — `BacktestEngine.backtest_strategy()` exists. `scripts/paper_backtest_drift.py` runs daily drift backtests.

### Gap

Current `BacktestEngine` lacks:
- **Commission + slippage model** — trades are simulated at exact close prices with no fill cost
- **Drawdown duration tracking** — `compute_drawdown_duration_peaks()` in `_stats.py` computes max AND average drawdown duration, which is not in current `StatsReport`
- **Profit Factor, SQN, Expectancy** — standard institutional metrics not currently computed
- **Kelly Criterion** in backtest context — `_stats.py` line 187: `win_rate - (1 - win_rate) / (mean_win / mean_loss)` — current `PositionSizer.calculate_kelly_size()` exists but is live only, not fed from backtest results
- **CAGR** and **Alpha/Beta vs buy-and-hold** — not in current tearsheet

### Recommended implementation

**Priority: HIGH**

#### 1a. Slippage + commission model in BacktestEngine

```python
# tradingagents/backtesting/backtest_engine.py

@dataclass
class FillModel:
    commission_pct: float = 0.0005   # 5 bps per side (Alpaca default)
    slippage_pct: float = 0.0002     # 2 bps market impact estimate
    spread_half_bps: float = 2.5     # half-spread cost

    def fill_price(self, signal_price: float, direction: int) -> float:
        """direction: +1=buy, -1=sell"""
        cost = signal_price * (self.slippage_pct + self.spread_half_bps / 10_000)
        return signal_price + direction * cost

    def commission(self, notional: float) -> float:
        return notional * self.commission_pct
```

Add `fill_model: FillModel = field(default_factory=FillModel)` to `BacktestEngine.__init__`. Apply `fill_price()` in `backtest_strategy()` wherever entry/exit prices are recorded.

#### 1b. Extend StatsReport with full tearsheet

Port these from `_stats.py` into `tradingagents/portfolio/reliability_stats.py` or a new `tradingagents/backtesting/tearsheet.py`:

```python
def compute_tearsheet(trades_df: pd.DataFrame, equity: np.ndarray, 
                       ohlc_close: np.ndarray, risk_free_rate: float = 0.045) -> dict:
    """
    Returns dict with: sharpe, sortino, calmar, max_dd, avg_dd,
    max_dd_duration, avg_dd_duration, profit_factor, sqn,
    expectancy_pct, kelly_criterion, alpha_vs_bh, beta,
    cagr, win_rate, n_trades, avg_trade_duration
    """
    ...
```

Key formulas verbatim from backtesting.py `_stats.py`:
- Sharpe: `annualized_return / annualized_volatility` (geometric mean based)
- Sortino: `annualized_return / sqrt(mean(downside_returns^2) * annual_days)`
- Calmar: `annualized_return / abs(max_drawdown)`
- SQN: `sqrt(n_trades) * mean(pnl) / std(pnl)`
- Kelly: `win_rate - (1 - win_rate) / (mean_win / abs(mean_loss))`

#### 1c. Feed backtest Kelly back to PositionSizer

`PositionSizer.calculate_kelly_size()` currently takes `win_prob` and `win_loss_ratio` as parameters. After each backtest run, persist these from tearsheet → feed into live sizer:

```python
# scripts/paper_trade_today.py after backtest validation
sizer = PositionSizer()
sizer.backtest_kelly = tearsheet["kelly_criterion"]
```

---

## 2. ML4T (machine-learning-for-trading)

**Source:** stefan-jansen/machine-learning-for-trading (book companion code)  
**Chapters of interest:** 6 (ML process), 8 (ML4T workflow), 22 (Deep RL)

### 2a. Deflated Sharpe Ratio (DSR) — Chapter 8

**Priority: HIGH**

`08_ml4t_workflow/01_multiple_testing/deflated_sharpe_ratio.py` implements Lopez de Prado's DSR, which adjusts the observed Sharpe ratio for selection bias when many strategies were tried.

**Problem it solves:** `model_readiness_report.py` currently gates on WF ROC ≥ 0.49. But if 50 hyperparameter configs were tried during retrain (they are — see `retrain_weekly.py`), the best WF ROC is upward-biased. DSR corrects this.

**Implementation:**

```python
# tradingagents/backtesting/deflated_sharpe.py

import numpy as np
import scipy.stats as ss


def expected_max_sharpe(trials: int, mean_sr: float = 0.0, std_sr: float = 1.0) -> float:
    """E[max SR] across `trials` independent strategies."""
    emc = 0.5772156649
    z = (1 - emc) * ss.norm.ppf(1 - 1.0 / trials) + emc * ss.norm.ppf(1 - 1.0 / (trials * np.e))
    return mean_sr + std_sr * z


def deflated_sharpe_ratio(
    observed_sr: float,
    n_trials: int,
    sr_std: float = 1.0,
    sr_skew: float = 0.0,
    sr_kurt: float = 3.0,
    n_obs: int = 252,
) -> float:
    """
    Returns DSR — probability that true SR > 0 after correcting for
    selection bias from testing n_trials strategies.
    
    Source: Lopez de Prado (2014), via ML4T ch8.
    """
    sr0 = expected_max_sharpe(n_trials, 0.0, sr_std)
    sr_adj = ((1 - sr_skew * observed_sr + (sr_kurt - 1) / 4 * observed_sr**2)
              / (n_obs - 1)) ** 0.5
    dsr = ss.norm.cdf((observed_sr - sr0) / sr_adj)
    return float(dsr)
```

Add DSR check to `scripts/model_readiness_report.py` as a SOFT gate: DSR < 0.50 → WARN.

### 2b. Deep RL Trading Environment — Chapter 22

**Priority: MEDIUM (existing train_rl_agent.py needs this)**

`22_deep_reinforcement_learning/trading_env.py` provides a battle-tested `DataSource` + `TradingSimulator` + `TradingEnvironment` scaffold. Current `scripts/train_rl_agent.py` likely has its own env.

Key additions from ML4T's env worth porting:
- `time_cost_bps` — holding cost per bar (forces the agent to earn its keep)
- `trading_cost_bps` — friction applied at each trade, creating realistic incentive to not over-trade
- Feature preprocessing: `returns`, `ret_2`, `ret_5`, `ret_10`, `ret_21`, `rsi`, `macd`, `atr`, `stoch`, `ultosc` — all scale-normalized except returns

The `TradingSimulator` tracks `navs`, `market_navs`, `strategy_returns`, `positions`, `costs`, `trades` per step — this feeds directly into the reward function.

**Recommended RL reward function (from ch22):**

```python
reward = (strategy_nav / prev_nav) - 1  # relative return
reward -= trading_cost_bps * abs(position_change) / 1e4  # friction
reward -= time_cost_bps / 1e4           # holding tax forces profit
```

### 2c. Alpha Factor IC Pipeline — Chapter 4

**Priority: MEDIUM**

ML4T chapter 4 defines the standard alpha factor evaluation loop:
1. Compute factor values (e.g., momentum, volatility, quality ratios)
2. Compute forward returns for N periods (1d, 5d, 21d)
3. Compute **Information Coefficient (IC)** = Spearman rank correlation between factor and forward return
4. Track IC mean, IC std, **ICIR** = IC/std (analogous to Sharpe for factors)

This is partially implemented in `tradingagents/qlib_integration/adapter.py` (`extract_alpha_features()`). What's missing is the **IC scoring loop** itself.

```python
# tradingagents/qlib_integration/factor_ic.py

import pandas as pd
import scipy.stats as ss

def compute_ic(factor: pd.Series, forward_returns: pd.Series) -> float:
    """Spearman IC between factor values and forward returns, cross-sectionally."""
    aligned = pd.concat([factor, forward_returns], axis=1).dropna()
    if len(aligned) < 10:
        return float("nan")
    ic, _ = ss.spearmanr(aligned.iloc[:, 0], aligned.iloc[:, 1])
    return float(ic)


def ic_series(
    factor_df: pd.DataFrame,  # index=date, columns=tickers
    returns_df: pd.DataFrame, # index=date, columns=tickers
    forward_days: int = 5,
) -> pd.Series:
    """Rolling daily IC time series."""
    fwd = returns_df.shift(-forward_days)
    ics = []
    for date in factor_df.index:
        if date not in fwd.index:
            continue
        f = factor_df.loc[date].dropna()
        r = fwd.loc[date].dropna()
        both = f.index.intersection(r.index)
        if len(both) < 10:
            ics.append((date, float("nan")))
            continue
        ic, _ = ss.spearmanr(f[both], r[both])
        ics.append((date, ic))
    s = pd.Series(dict(ics))
    return s


def icir(ic_s: pd.Series, min_obs: int = 20) -> float:
    """IC Information Ratio (mean/std). Higher = more reliable factor."""
    clean = ic_s.dropna()
    if len(clean) < min_obs:
        return float("nan")
    return float(clean.mean() / clean.std())
```

Add `factor_ic.py` to `tradingagents/qlib_integration/`. Call from `QlibResearchEngine.run_tournament()` to surface per-factor ICIR alongside model WF ROC.

---

## 3. PatternPy-main

**Source:** twopirllc/PatternPy  
**Files:** `tradingpatterns/tradingpatterns.py` (187 lines, 9 functions)

### What it does

Vectorized pandas pattern detection using rolling windows:
- `detect_head_shoulder(df, window=3)` → adds `head_shoulder_pattern` column
- `detect_multiple_tops_bottoms(df, window=3)` → `multiple_top_bottom_pattern`
- `calculate_support_resistance(df, window=3)` → `support`, `resistance` (rolling mean ± 2σ)
- `detect_triangle_pattern(df, window=3)` → `triangle_pattern`
- `detect_wedge(df, window=3)` → `wedge_pattern`
- `detect_channel(df, window=3)` → `channel_pattern`
- `detect_double_top_bottom(df, window=3, threshold=0.05)` → `double_pattern`
- `detect_trendline(df, window=2)` → `slope`, `intercept`, `support`, `resistance`
- `find_pivots(df)` → `signal` column with HH/HL/LH/LL pivot labels

### Integration plan

**Priority: HIGH (zero new dependencies, pure pandas/numpy)**

`BreakoutScanner.score_one()` in `tradingagents/screening/breakout_scanner.py` already computes RSI, MACD, BB, Keltner, OBV, relative strength. PatternPy adds **8 confirmatory signals** that increase breakout conviction.

#### Add PatternPy wrapper to breakout_scanner.py

```python
# tradingagents/screening/pattern_signals.py
"""
Thin wrapper around PatternPy detection functions.
Returns a compact dict of detected patterns for a price DataFrame.
DataFrame must have columns: Open, High, Low, Close, Volume
"""
from __future__ import annotations
import pandas as pd
import numpy as np


def detect_all_patterns(df: pd.DataFrame, window: int = 5) -> dict:
    """
    Run all PatternPy detectors on the last `window*3` bars.
    Returns a dict of detected pattern labels (empty string = none detected).
    """
    # Work on a copy to avoid polluting caller's df
    d = df[["Open", "High", "Low", "Close", "Volume"]].copy().tail(window * 6)
    
    results = {}
    
    # Head & Shoulder
    d2 = _detect_head_shoulder(d.copy(), window)
    last = d2["head_shoulder_pattern"].dropna()
    results["head_shoulder"] = last.iloc[-1] if len(last) else ""
    
    # Double Top/Bottom
    d2 = _detect_double_top_bottom(d.copy(), window)
    last = d2["double_pattern"].dropna()
    results["double_pattern"] = last.iloc[-1] if len(last) else ""
    
    # Wedge
    d2 = _detect_wedge(d.copy(), window)
    last = d2["wedge_pattern"].dropna()
    results["wedge"] = last.iloc[-1] if len(last) else ""
    
    # Triangle
    d2 = _detect_triangle_pattern(d.copy(), window)
    last = d2["triangle_pattern"].dropna()
    results["triangle"] = last.iloc[-1] if len(last) else ""
    
    # Channel
    d2 = _detect_channel(d.copy(), window)
    last = d2["channel_pattern"].dropna()
    results["channel"] = last.iloc[-1] if len(last) else ""
    
    # Pivots - count recent HH, HL (bullish structure)
    d2 = _find_pivots(d.copy())
    recent_signals = d2["signal"].tail(window * 2).tolist()
    results["hh_count"] = recent_signals.count("HH")
    results["hl_count"] = recent_signals.count("HL")
    results["lh_count"] = recent_signals.count("LH")
    results["ll_count"] = recent_signals.count("LL")
    
    # Bullish structure score: HH+HL dominant = uptrend
    bullish = results["hh_count"] + results["hl_count"]
    bearish = results["lh_count"] + results["ll_count"]
    results["pivot_structure"] = "bullish" if bullish > bearish else ("bearish" if bearish > bullish else "mixed")
    
    return results


def pattern_score_delta(patterns: dict) -> float:
    """
    Returns a score adjustment for BreakoutComponents based on pattern signals.
    Range: -0.10 to +0.10 (additive to BreakoutComponents.total)
    """
    delta = 0.0
    
    # Bullish confirming patterns
    bullish_confirms = {
        "Inverse Head and Shoulder", "Multiple Bottom", 
        "Ascending Triangle", "Wedge Down",   # wedge down before breakout = bullish
        "Double Bottom", "Channel Up",
    }
    bearish_warns = {
        "Head and Shoulder", "Multiple Top",
        "Descending Triangle", "Wedge Up",    # wedge up = distribution
        "Double Top", "Channel Down",
    }
    
    for key in ["head_shoulder", "double_pattern", "wedge", "triangle", "channel"]:
        val = patterns.get(key, "")
        if val in bullish_confirms:
            delta += 0.02
        elif val in bearish_warns:
            delta -= 0.03  # penalize harder — bearish pattern on breakout = trap
    
    # Pivot structure
    if patterns.get("pivot_structure") == "bullish":
        delta += 0.02
    elif patterns.get("pivot_structure") == "bearish":
        delta -= 0.02
    
    return max(-0.10, min(0.10, delta))
```

#### Wire into BreakoutScanner.score_one()

In `tradingagents/screening/breakout_scanner.py`, after computing `comp` (BreakoutComponents):

```python
from tradingagents.screening.pattern_signals import detect_all_patterns, pattern_score_delta

# Inside score_one(), after comp is computed but before returning
try:
    patterns = detect_all_patterns(ohlcv_df, window=5)
    result.extra["patterns"] = patterns
    result.extra["pattern_score_delta"] = pattern_score_delta(patterns)
    # Adjust breakout score — patterns are confirmatory not primary
    adjusted_total = comp.total() + pattern_score_delta(patterns)
    result = dataclasses.replace(result, breakout_score=adjusted_total)
except Exception:
    pass  # pattern detection is best-effort
```

---

## 4. FinancePy-master

**Source:** domokane/FinancePy  
**Key modules:** `financepy/models/black_scholes_analytic.py`, `financepy/products/equity/equity_vanilla_option.py`

### What it does

Full derivatives pricing library: Black-Scholes (vectorized via numba), Heston, SABR, binomial trees, Monte Carlo for exotics. Also rates (Hull-White, BDT), FX, credit (Gaussian copula).

### Relevant to this project

**Protective put / covered call pricing for tail-risk management.**

The project has no options pricing capability. For paper trading a long-only equity strategy, the natural hedge is:
1. Price OTM put options at current market prices
2. Determine if protective put cost < expected drawdown reduction
3. Flag in daily audit when portfolio heat is high and puts are cheap (low VIX)

#### Minimal implementation — IV + protective put cost

Rather than importing the full FinancePy library (heavy numba compilation), extract the analytic core:

```python
# tradingagents/portfolio/options_pricing.py
"""
Analytic Black-Scholes for protective put sizing.
No external dependencies beyond numpy/scipy.
"""
from __future__ import annotations
import math
import scipy.stats as ss


def bs_put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """European put price via Black-Scholes. T in years."""
    if T <= 0 or sigma <= 0:
        return max(K - S, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * ss.norm.cdf(-d2) - S * ss.norm.cdf(-d1)


def implied_volatility(
    market_price: float, S: float, K: float, T: float, r: float,
    tol: float = 1e-6, max_iter: int = 100
) -> float:
    """Newton-Raphson IV solver for European put."""
    sigma = 0.25
    for _ in range(max_iter):
        price = bs_put_price(S, K, T, r, sigma)
        vega = S * math.sqrt(T) * ss.norm.pdf(
            (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        )
        diff = price - market_price
        if abs(diff) < tol:
            break
        if vega < 1e-10:
            break
        sigma -= diff / vega
        sigma = max(0.001, min(sigma, 5.0))
    return sigma


def protective_put_cost_annualized(
    S: float, K: float, T_years: float, r: float, sigma: float
) -> float:
    """
    Annual cost of protective put as % of position value.
    Useful for comparing to expected max drawdown protection.
    """
    put = bs_put_price(S, K, T_years, r, sigma)
    return put / S / T_years  # annualized cost rate
```

**Usage in daily_audit.py:** If portfolio heat > 70% and VIX > 20, compute OTM put cost for a 30-day 5% OTM put on SPY and surface as WARN finding.

### Lower priority — FinancePy not recommended for direct import

The full FinancePy library requires numba JIT compilation on first use (adds ~10s startup) and has complex date handling. For this project's needs (protective put cost, IV lookup), the standalone `options_pricing.py` above is sufficient.

---

## 5. financial-machine-learning-master (firmai wiki)

**Note:** This zip is NOT the Lopez de Prado AFML code library. It is a curated GitHub repo index/wiki maintained by firmai/sov.ai, cataloging 500+ financial ML repositories with star counts and activity status. No executable code.

### High-value references surfaced from the wiki

From `generated_wiki/portfolio_selection_and_optimisation.md`:
- **PyPortfolioOpt** (4,425 stars, active): `robertmartin8/PyPortfolioOpt` — efficient frontier, Black-Litterman, HRP. Project already has `HRPOptimizer`; PyPortfolioOpt adds Black-Litterman views.
- **Riskfolio-Lib** (2,985 stars, active): `dcajasn/Riskfolio-Lib` — CVaR, CDaR, hierarchical equal risk contribution. More sophisticated than current HRP.
- **cvxportfolio** (968 stars, active): `cvxgrp/cvxportfolio` — convex portfolio optimization with transaction cost modeling.

From `generated_wiki/factor_and_risk_analysis.md`:
- **Alphalens** (3,282 stars): `quantopian/alphalens` — factor IC, ICIR, turnover analysis. Pairs with the IC pipeline in section 2c.
- **toraniko** (376 stars, active 2024): `0xfdf/toraniko` — equity risk model (factor decomposition). Worth examining.

From `generated_wiki/deep_learning_and_reinforcement_learning.md`:
- **DeepDow** (901 stars): `jankrepl/deepdow` — portfolio optimization as deep learning. Longer-term research direction.

### Recommended action

Add these as research pointers in `docs/QUANT_RESEARCH_ROADMAP.md`. Do not import PyPortfolioOpt or Riskfolio-Lib until the RL agent and current ML pipeline are stabilized — adding optimization layers to an unstable signal is noise amplification, not improvement.

---

## Implementation Prioritization

### Phase 1 — Zero-dependency, high confidence (implement now)

| ID | Action | File(s) to create/modify | Effort |
|----|--------|--------------------------|--------|
| P1-A | PatternPy wrapper + BreakoutScanner integration | `tradingagents/screening/pattern_signals.py`, `breakout_scanner.py` | 2h |
| P1-B | Slippage/commission FillModel in BacktestEngine | `tradingagents/backtesting/backtest_engine.py` | 1h |
| P1-C | Standalone `options_pricing.py` (BS put + IV) | `tradingagents/portfolio/options_pricing.py` | 1h |
| P1-D | Extended tearsheet metrics (Profit Factor, SQN, Kelly, CAGR, Alpha/Beta) | `tradingagents/backtesting/tearsheet.py` | 2h |

### Phase 2 — Research quality improvements (next sprint)

| ID | Action | File(s) to create/modify | Effort |
|----|--------|--------------------------|--------|
| P2-A | Deflated Sharpe Ratio check in model_readiness_report.py | `tradingagents/backtesting/deflated_sharpe.py`, `scripts/model_readiness_report.py` | 1.5h |
| P2-B | IC/ICIR alpha factor pipeline in QlibResearchEngine | `tradingagents/qlib_integration/factor_ic.py`, `engine.py` | 2h |
| P2-C | Protective put cost flag in daily_audit.py | `tradingagents/portfolio/options_pricing.py`, `scripts/daily_audit.py` | 1h |

### Phase 3 — Structural (do after Phase 2 is validated)

| ID | Action | File(s) to create/modify | Effort |
|----|--------|--------------------------|--------|
| P3-A | RL env improvements using ML4T ch22 scaffold | `tradingagents/rl/`, `scripts/train_rl_agent.py` | 4h |
| P3-B | Black-Litterman views layer on top of HRPOptimizer | `tradingagents/portfolio/hrp_optimizer.py` | 3h |

---

## What to Avoid

1. **Importing full FinancePy** — numba compilation overhead, complex date system. Use the standalone `options_pricing.py` instead.
2. **Importing backtesting.py directly** — it wraps a full event loop and conflicts with the existing `BacktestEngine`. Port the math (stats, fill model) rather than the framework.
3. **Importing Alphalens** — it requires Zipline data format. Use the `factor_ic.py` implementation above instead.
4. **Adding PyPortfolioOpt or Riskfolio-Lib before signal quality is established** — portfolio optimizers amplify whatever signal you feed them. Current WF ROC=0.5121 and high-conf win rate=0.395 means the signal is borderline. Better optimization of a weak signal does not improve expected returns.

---

## Appendix: Source Attribution

All implementations above were derived by reading the reference library source code. The mathematical formulas (DSR, Sharpe/Sortino/Calmar, BS pricing) are in the public domain via their respective academic papers (Lopez de Prado 2014, Black-Scholes 1973). The PatternPy detection logic was studied and independently reimplemented with modifications for the project's BreakoutScanner integration.

Reference zip locations: `/Users/williamscott/Desktop/TradingAgents-0.2.4 copy/.external_research/`
