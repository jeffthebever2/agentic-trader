# Agentic Trader

Production algorithmic stock trading system. ML-driven candidate scanning, 15-portfolio paper-trading competition, and Qlib alpha-factor research — all wired into a single pipeline.

---

## Quick Start

```bash
# 1. Set up environment
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # fill in brokerage/API keys

# 2. Train all models (first run only — 30–90 min)
./start.sh train

# 3. Start the system
./start.sh all             # web dashboard + paper trading
```

Dashboard: **http://localhost:8001**  
Portfolio competition: **http://localhost:8001/portfolios**

---

## Commands

```
./start.sh web          Start the web dashboard only (port 8001)
./start.sh paper        Start 15-portfolio paper trading competition
./start.sh train        Full pipeline: ML + HMM + Qlib + validation
./start.sh retrain      Weekly model refresh (fastest, Qlib included)
./start.sh all          Web + paper trading together
./start.sh status       Show what's running + model health
./start.sh logs         Tail latest logs
./start.sh stop         Kill all managed processes
```

---

## Architecture

```
Market Data (yfinance / Fidelity API)
         │
         ▼
  Candidate Scanner          ← scripts/paper_trade_today.py
  (breakout + pullback)
         │
         ▼
  ML Scoring Layer           ← ml_models/latest/model_bundle.joblib
  (XGBoost win-probability)
  +  Qlib Alpha Factors      ← tradingagents/qlib_integration/
  (momentum, volatility,     (qlib_mom_252_21, qlib_mom_63,
   ATR-Z, close rank,         qlib_vol_ratio, qlib_atr_z,
   cross-sectional ranks)     cross-sectional percentile ranks)
         │
         ▼
  15 Portfolio Accounts      ← tradingagents/portfolios/
  (signal / risk / hold / filter groups)
  competing simultaneously
         │
         ▼
  Web Dashboard              ← web/app.py  +  web/static/
  + Portfolio Leaderboard    ← /portfolios  (live comparison)
```

---

## Portfolio Competition

15 named portfolios run simultaneously on the same candidate stream, each with different risk/sizing/hold parameters. The leaderboard at `/portfolios` ranks them by total return in real time.

**Groups:**
| Group | Hypothesis |
|-------|-----------|
| **Signal** | Different entry-signal sources (algo, ML, combined) |
| **Risk** | Conservative vs. aggressive stop/target/sizing |
| **Hold** | Quick exits (3d) vs. swing (25d) vs. standard |
| **Filter** | ML probability gates at different thresholds |

---

## Training Pipeline

### Quick retrain (recommended weekly)
```bash
./start.sh retrain
# or with full options:
python3 scripts/retrain_weekly.py --tickers all_tickers.txt --include-qlib-features
```

### Full pipeline (all models, resumable)
```bash
./start.sh train
# resume after interruption:
python3 scripts/train_everything.py --resume tmp/train_everything/<run_id>/state.json
```

**What gets trained:**
- `ml_models/latest/` — XGBoost win-probability model (walk-forward validated, gate: WF ROC ≥ 0.49)
- `ml_models/stock_universe/` — Stock universe ranker
- `ml_models/hmm_regime/` — Hidden Markov regime detector
- Qlib features merged in at training time when `--include-qlib-features` is set

### Model gate
A retrain only deploys if:
- Walk-forward ROC ≥ 0.49 (SE ≈ 0.009 over ~2000 OOS rows)
- Brier score ≤ 0.25 (calibration check)
- PSI feature stability pass

---

## Qlib Integration

Qlib (0.9.8.dev31) provides lagged alpha factors used as model features. Enable with `--include-qlib-features`.

**Factors:**
| Feature | Description | History needed |
|---------|-------------|---------------|
| `qlib_mom_252_21` | 12-month minus 1-month momentum | ~273 days |
| `qlib_mom_63` | 3-month minus 1-month momentum (fallback for short history) | ~85 days |
| `qlib_vol_ratio` | Short/long volatility ratio | ~63 days |
| `qlib_atr_z` | ATR normalized by 63-day mean | ~85 days |
| `qlib_close_rank` | Cross-sectional price level rank | 1 day |
| `qlib_cs_rank_*` | Per-scan-date percentile ranks of all 4 base factors | same as base |

All features are lagged ≥ 1 day at the computation layer — no look-ahead.

---

## Directory Structure

```
scripts/
  paper_trade_today.py     Main paper trading engine (15 portfolios)
  retrain_weekly.py        Weekly ML retrain pipeline
  train_everything.py      Full training orchestrator (resumable)
  paper_trade_unified.py   UnifiedBrain alternative runner
  daily_audit.py           Daily health check

tradingagents/
  portfolios/              15-portfolio competition framework
    config.py              PortfolioConfig dataclass
    registry.py            All 15 portfolio definitions
    comparison.py          Stats engine (Sharpe, drawdown, equity curve)
  ml/                      ML training + calibration
  qlib_integration/        Qlib alpha factor pipeline
  backtesting/             Backtest engine
  screening/               Candidate scanners

web/
  app.py                   FastAPI server
  api/portfolios.py        Portfolio leaderboard API
  static/portfolios.html   Portfolio comparison dashboard

tests/                     pytest suite (19 Qlib leakage tests, etc.)
ml_models/                 Deployed model artifacts
  latest/                  Active win-probability model
  stock_universe/          Stock ranker model
  hmm_regime/              Regime detector
```

---

## Configuration

Copy `.env.example` to `.env` and configure:

| Variable | Purpose |
|----------|---------|
| `FIDELITY_*` | Brokerage connection (paper mode is safe without this) |
| `OPENAI_API_KEY` | Optional: LLM-based signal augmentation |
| `ANTHROPIC_API_KEY` | Optional: LLM signal analysis |
| `WEB_PORT` | Dashboard port (default: 8001) |
| `PAPER_OUTPUT_DIR` | Where portfolio state files are written |

---

## Safety

- Paper trading only — no live order execution by default
- Model gate blocks deployment of underperforming retrains
- Leakage checks run automatically before every training (via `tests/test_qlib_leakage.py`)
- `FORCE_FLATTEN` halts all positions on catastrophic drawdown

See `SECURITY.md` for full threat model.

---

## Requirements

- Python 3.10+
- Qlib 0.9.8+ (for `--include-qlib-features`)
- Node.js 18+ (for frontend build only)

```bash
pip install -r requirements.txt
```
