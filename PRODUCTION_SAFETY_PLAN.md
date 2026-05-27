# Production Safety and Monitoring Layer — Design Plan
*Created: 2026-05-26*

---

## 1. Current State — What Exists

| Safety Feature | Where | Status |
|----------------|-------|--------|
| Crisis VIX halt (>35) | SafeTradeGuard | ✅ |
| Hostile regime halt (bear+VIX>25) | SafeTradeGuard | ✅ |
| Portfolio drawdown halt | SafeTradeGuard | ✅ |
| Model drift halt (|pred-actual|>0.20) | SafeTradeGuard | ✅ |
| Win rate floor (rolling WR<0.30) | SafeTradeGuard | ✅ |
| Model staleness warning | SafeTradeGuard | ✅ (warn, not halt) |
| Daily loss limit | paper_trade_today.py | ✅ (2% default) |
| Heat cap / max exposure | paper_trade_today.py | ✅ (80%) |
| Max positions | paper_trade_today.py | ✅ |
| Circuit breaker (yfinance failures) | paper_trade_today.py | ✅ |
| Sector concentration | paper_trade_today.py | ✅ |
| Feature drift check (PSI) | check_live_feature_drift | ✅ |
| Stale data check (age_days) | build_candidates | ⚠️ (partial, returns False not no-trade) |
| MarketRegimeEngine no_trade | paper_trade_today.py | ✅ |

## 2. Gaps — What's Missing

| Safety Feature | Gap |
|----------------|-----|
| **Weekly drawdown limit** | No. Only daily + portfolio max. |
| **Max consecutive loss shutdown** | No. Win-rate floor helps but fires late. |
| **Max trades per day** | No. Only PDT (pattern day trader) tracking. |
| **Emergency kill-switch config** | No. Only CLI flags (must restart to change). |
| **Model confidence floor** | No structured HALT if all signals below floor. |
| **Model health report** | No. Age check in SafeTradeGuard but no structured status object. |
| **Data health checks** | No NaN rate, duplicate row, or abnormal move detection. |
| **Structured runtime safety report** | No. Logs events but no `safe_to_trade: bool` summary file. |
| **Missing feature detection** | No. ML apply silently uses NaN fills. |
| **Weekly drawdown tracking** | No persistence across days. |

---

## 3. Architecture: New File

### `tradingagents/portfolio/production_safety.py`

Single source of truth for all safety conditions. Returns a `SafetyReport` dataclass that drives the no-trade decision.

```
ProductionSafetyMonitor.check_all() → SafetyReport
  ├── check_kill_switch()         # file-based emergency stop
  ├── check_model_health()        # age, calibration, drift, validation
  ├── check_data_health()         # freshness, NaN, duplicates, abnormal moves
  ├── check_account_health()      # drawdown, daily loss, weekly loss, streak, trades/day
  ├── check_market_conditions()   # VIX, regime (delegates to SafeTradeGuard)
  └── check_exposure()            # heat, positions, sector
```

```python
@dataclass
class SafetyReport:
    safe_to_trade: bool
    halt_reasons: List[str]        # critical → no entries
    warn_reasons: List[str]        # non-critical → log + continue
    gates_active: List[str]        # every check that returned non-default
    model_health: Dict[str, Any]   # age, drift, calibration, etc.
    data_health: Dict[str, Any]    # NaN rates, stale flags, anomaly flags
    account_health: Dict[str, Any] # drawdown, streak, daily/weekly pnl
    exposure: Dict[str, Any]       # deployed pct, positions, sectors
    checked_at: str                # ISO timestamp
    
    def to_json(self) -> str:      # for safety_report.json
    def summary_str(self) -> str:  # one-line for dashboard
```

### Kill-switch config: `safety_config.json`

Runtime-editable (no restart needed):
```json
{
  "kill_switch": false,
  "kill_switch_reason": "",
  "max_daily_loss_pct": 2.0,
  "max_weekly_loss_pct": 5.0,
  "max_consecutive_losses": 4,
  "max_trades_per_day": 8,
  "min_model_confidence_floor": 0.55,
  "max_model_age_days": 45,
  "max_nan_rate": 0.30,
  "max_stale_data_hours": 6.0,
  "max_abnormal_move_pct": 0.25
}
```

---

## 4. Safety Gates (Full List)

### Critical (halt new entries)
| Gate | Default | Condition |
|------|---------|-----------|
| `kill_switch` | false | `safety_config.json` kill_switch = true |
| `daily_loss_limit` | 2% | Today's realized PnL < -(account × daily_loss_pct) |
| `weekly_loss_limit` | 5% | Week's realized PnL < -(account × weekly_loss_pct) |
| `consecutive_loss_shutdown` | 4 | Consecutive losses ≥ max_consecutive_losses |
| `max_trades_per_day` | 8 | Trades opened today ≥ max_trades_per_day |
| `model_load_failure` | — | Model bundle missing or corrupt |
| `model_too_old` | 45d | Model age > max_model_age_days (halt, not just warn) |
| `model_drift` | 0.20 | |pred_wr - actual_wr| > threshold (existing) |
| `wr_collapse` | 0.30 | Rolling WR < floor (existing) |
| `data_stale` | 6h | Data freshness > max_stale_data_hours |
| `high_nan_rate` | 30% | Feature NaN rate > max_nan_rate |
| `crisis_vix` | 35 | VIX > threshold (existing) |
| `portfolio_drawdown` | 12% | Drawdown < -max_dd_pct (existing) |
| `hostile_regime` | — | Bear + elevated VIX (existing) |
| `regime_no_trade` | — | MarketRegimeEngine.no_trade (existing) |

### Warning (log + continue)
| Gate | Condition |
|------|-----------|
| `model_stale_warn` | Age > 30d (below halt threshold) |
| `feature_drift_psi` | PSI > 0.1 for any feature |
| `data_abnormal_move` | Any ticker moved >25% in single day |
| `duplicate_rows_detected` | Duplicate scan_date × ticker rows in data |

---

## 5. Data Health Checks

`DataHealthChecker.check(df, ticker, expected_columns)`:
- `freshness`: last bar timestamp vs now (hours old)
- `nan_rate`: pct of cells that are NaN in key columns
- `duplicate_rows`: duplicate date rows
- `abnormal_move`: abs(pct_change) > threshold on any day
- `price_jump`: close / prev_close > 1.25 or < 0.75 in single bar
- `zero_volume_days`: sessions with zero volume

---

## 6. Model Health Checks

`ModelHealthChecker.check(bundle_path, drift_log_path, validation_summary_path)`:
- `model_age_days`: days since `created_at`
- `calibration_age_days`: days since calibration was saved
- `drift_status`: from ml_drift.json
- `validation_gate`: from validation_summary.json (roc_auc, brier)
- `last_prediction_time`: last time ML scored a candidate (from events log)
- `n_features_expected` vs `n_features_live`: mismatch detection

---

## 7. Runtime Report

Written to `{output_dir}/safety_report.json` after every scan cycle.

```json
{
  "safe_to_trade": false,
  "halt_reasons": ["weekly_loss_limit: -5.2% < -5.0%"],
  "warn_reasons": ["WARN_model_stale: 38d > 30d"],
  "gates_active": ["daily_loss_limit", "weekly_loss_limit"],
  "model_health": {
    "age_days": 38,
    "drift": 0.08,
    "roc_auc": 0.614,
    "brier": 0.218,
    "load_status": "ok"
  },
  "data_health": {
    "freshness_hours": 0.4,
    "nan_rate": 0.02,
    "duplicates": 0,
    "abnormal_moves": []
  },
  "account_health": {
    "drawdown": -0.031,
    "daily_pnl": -142.0,
    "weekly_pnl": -521.0,
    "consecutive_losses": 2,
    "trades_today": 3
  },
  "exposure": {
    "deployed_pct": 45.2,
    "open_positions": 3,
    "heat_pct": 45.2
  },
  "checked_at": "2026-05-26T10:35:00"
}
```

---

## 8. Files to Create / Modify

### New:
- `tradingagents/portfolio/production_safety.py` — ProductionSafetyMonitor, SafetyReport, DataHealthChecker, ModelHealthChecker

### Modified:
- `scripts/paper_trade_today.py`:
  - `scan_account_once()`: replace standalone SafeTradeGuard call with ProductionSafetyMonitor.check_all()
  - Add kill-switch config loading at startup
  - Add weekly loss tracking
  - Write safety_report.json after each scan cycle
  - Add `max_trades_per_day` enforcement
  - Add `max_consecutive_losses` enforcement
- `tradingagents/portfolio/__init__.py`: export ProductionSafetyMonitor, SafetyReport

---

## 9. Dry-run / Test

```bash
# Dry-run: check safety conditions without trading
python scripts/paper_trade_today.py --dry-run --no-ml --max-tickers 5

# Force kill-switch test
echo '{"kill_switch": true, "kill_switch_reason": "test"}' > paper_accounts/algorithm/safety_config.json
python scripts/paper_trade_today.py --strategy algorithm

# Check safety report after run
cat paper_accounts/algorithm/safety_report.json | python3 -m json.tool
```

---

## 10. Anti-Weakening Checklist

- [x] Daily loss limit default unchanged (2%)
- [x] Portfolio drawdown threshold unchanged (5% flatten)
- [x] Stop distances not reduced
- [x] Max position sizes not increased
- [x] All new gates are additive (more conservative, not less)
- [x] Kill-switch default is false (must be explicitly enabled)

## 11. Implementation Status

- [x] `production_safety.py` written and parse-checked
- [x] `__init__.py` exports ProductionSafetyMonitor, SafetyReport, DataHealthChecker, ModelHealthChecker, DEFAULT_SAFETY_CONFIG, ensure_safety_config
- [x] `paper_trade_today.py` integrated: ProductionSafetyMonitor.check_all() replaces standalone SafeTradeGuard halt check
- [x] CLI args added: --max-consecutive-losses, --max-trades-per-day, --max-weekly-loss-pct, --max-stale-data-hours, --max-model-age-days
- [x] Safety config written at startup per strategy (ensure_safety_config); CLI args override thresholds
- [x] safety_report.json written to output_dir after each scan cycle (via report.save())
- [x] SafeTradeGuard kept for high_vol_adjustments sizing only (not for halt decision)
- [x] SAFETY_HALT / SAFETY_WARN events logged to account event log
- [x] Smoke tests passed: kill_switch, weekly_loss_limit, consecutive_loss_shutdown gates all verified
