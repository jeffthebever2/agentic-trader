"""
Deep research backtest runner.
Runs: python run_deep.py
No CLI arguments needed — all settings are configured here.
"""
import sys
import types

# ── Configure all settings here ──────────────────────────────────────
args = types.SimpleNamespace(
    tickers        = "all_tickers.txt",
    start          = "2019-01-01",
    end            = "2024-12-31",
    threshold      = 100.0,
    score_mode     = "confirmed_pullback",  # strict gated setup, not point-chasing
    entry_timing   = "trigger_break",       # next-day confirmation above signal high
    freq           = 1,
    no_cache       = False,
    min_price      = 15.0,
    max_price      = 100.0,
    max_atr_pct    = 0.05,
    min_adv        = None,          # confirmed_pullback uses $20M 20d dollar-volume gate
    target_mult    = 0.9,           # smaller 3-day target: entry + 0.9 ATR
    stop_mult      = 1.1,           # stop includes tighter of signal low - .2 ATR / entry - 1.1 ATR
    hold_periods   = [1, 2, 3],
    primary_hold   = 3,
    allow_friday   = False,
    benchmark      = "SPY",
    score_min      = None,
    score_max      = None,
    # Mechanical gate mode is binary: rejected=0, accepted=100.
    grid_search    = False,
    grid_thresholds= [100.0],
    # Walk-forward: out-of-sample rolling windows
    walk_forward   = True,
    wf_window      = 252,           # 1-year training window
    wf_step        = 63,            # 3-month test steps
    # Monte Carlo: equity curve confidence bands
    monte_carlo    = True,
    mc_sims        = 1000,
    mc_sim_trades  = 252,           # trades per MC sim (~1 trading year)
    regime_filter  = "all",         # "all" | "bear" | "bull"
    # Practical account simulation + PNG charts
    account_size   = 5000.0,
    generate_charts= True,
    charts_dir     = None,          # default: backtest_charts_<timestamp>
    account_position_cap_pct = 20.0,
    account_commission = 0.0,
    # Diagnostics: explain misses and losses so rules can be improved
    diagnostics    = True,
    ml_analysis    = True,
    ml_max_rows    = 0,             # 0 = train/evaluate ML on every available row
    ml_candidate_sample = 100000,
    ml_min_train_rows = 200,
    ml_probability_threshold = 0.58,
    ml_expected_return_min = 0.0,
    ml_large_loss_max = 0.20,
    gate_diagnostics_limit = 250,
    missed_big_win_pct = 0.05,      # rejected setup that would have made >= +5%
    bad_loss_pct   = -0.03,         # taken trade losing <= -3%
    diagnostic_max_examples = 25,
    missed_max_examples = 50,
    # Output
    export_csv     = None,          # set to "ml_training_trades.csv" to train with scripts/train_ml_models.py
    no_trades_json = True,          # skip writing 80k+ rows to JSON (faster save)
)

# ── Run ──────────────────────────────────────────────────────────────
from backtest import run_backtest
run_backtest(args)
