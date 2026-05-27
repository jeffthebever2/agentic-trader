## Validation Summary
Claude’s patch should be **MODIFY**, not approve as-is.

It targets only safe files and does not touch broker/live order files, but it does **not apply cleanly as written** and it misses a current runtime bug in `scripts/train_ml_models.py`: `y_win_train` is used in label diagnostics before it is assigned. That would still break retraining before any ROC improvement can be measured.

Also, the claimed metric lift is plausible but not proven from the packet. The packet gives `win_roc=0.5479`, `Brier=None`, `WF Win Rate=None`, `n_grades=0`, and “No drift detected.” There are no feature importances, calibration error values, or executed-row ratios in the packet to justify the specific expected ROC gains.

## Code Correctness Check
`git apply --check` failed on Claude’s proposal as provided: the patch text is malformed/truncated in hunk headers.

Actual code findings:

- `backtest.py` currently sets `_win_label = (_return > 0.005)` in `_ml_prepare_frame`.
- `scripts/train_ml_models.py` currently has no `--win-label-threshold` argument.
- `scripts/retrain_weekly.py` currently passes `--executed-weight 20.0` by default.
- `scripts/train_ml_models.py` currently computes `_ld_pos = int(y_win_train.sum())` before `y_win_train` is assigned, so the training path is vulnerable to `UnboundLocalError`.

## Will It Actually Help? (cite specific lines/metrics)
Partially plausible, but overstated.

- Raising the win label threshold from `0.005` to `0.01` may reduce noisy near-breakeven positives, since `_ml_prepare_frame` labels wins at `> 0.5%`. This can improve separability, but it also changes the modeled target and may reduce positive-class count.
- Reducing `executed_weight` from `20.0` to `8.0` may reduce overfitting to executed rows. The current code applies sample weights to executed rows in `train_models()`, so this is a real behavioral change.
- Adding XGBoost `min_child_weight`, `gamma`, and higher `reg_lambda` is a reasonable regularization change for weak ROC, but the packet does not include train/test gap, feature importances, or fold metrics proving overfit.
- Since `Brier=None`, `WF Win Rate=None`, and `n_grades=0`, Claude cannot validly cite calibration error or live drift as support. With `n_grades < 30`, more paper trading is still needed before retraining decisions based on live reliability.

## Safety Check (does it touch forbidden files or weaken gates?)
The intended patch only touches:

- `scripts/train_ml_models.py`
- `scripts/retrain_weekly.py`

It does not touch `web/api/fidelity.py`, `web/api/webull_portfolio.py`, `web/api/paper.py`, `web/api/admin.py`, or `scripts/paper_trade_today.py`.

It does not weaken stops, drawdown limits, kill-switches, or production safety. `tradingagents/portfolio/production_safety.py` remains untouched. It also does not tune on the 2026-05-08 to 2026-05-26 holdout window.

## VERDICT: MODIFY
## Revised Patch (if MODIFY — unified diff only, do not apply)
```diff
--- a/scripts/train_ml_models.py
+++ b/scripts/train_ml_models.py
@@ -43,6 +43,7 @@ def _make_clf(y_labels, n_estimators=500, max_depth=6, min_samples_leaf=15, seed
             n_estimators=n_estimators, max_depth=max_depth,
             learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
             scale_pos_weight=spw, tree_method="hist",
+            min_child_weight=10, gamma=0.05, reg_lambda=1.5,
             eval_metric="logloss", verbosity=0, n_jobs=-1, random_state=seed,
         )
     return RandomForestClassifier(
@@ -358,6 +359,18 @@ def train_models(args) -> dict:
     rows = _load_rows(source)
     frame, numeric, categorical = _ml_prepare_frame(rows, args.hold)
 
+    win_label_threshold = float(getattr(args, "win_label_threshold", 0.005))
+    if "_return" in frame.columns and abs(win_label_threshold - 0.005) > 1e-12:
+        old_wr = float(frame["_win_label"].mean()) if "_win_label" in frame.columns and len(frame) else 0.0
+        frame["_win_label"] = (frame["_return"] > win_label_threshold).astype(int)
+        new_wr = float(frame["_win_label"].mean()) if len(frame) else 0.0
+        print(
+            f"  Win label threshold: {win_label_threshold:.3f} "
+            f"(win rate {old_wr:.1%} -> {new_wr:.1%})"
+        )
+    else:
+        print(f"  Win label threshold: {win_label_threshold:.3f}")
+
     if len(frame) < args.min_rows:
         raise SystemExit(
             f"Only {len(frame)} usable rows found; need at least {args.min_rows}."
@@ -528,11 +541,13 @@ def train_models(args) -> dict:
     }
 
     # ── Label distribution diagnostics ───────────────────────────────────────
+    y_win_train = train_df["_win_label"].astype(int).to_numpy()
+    y_win_test = test_df["_win_label"].astype(int).to_numpy()
+
     # Records class balance and label validity before training.
     # Critical for catching: near-random labels, severe imbalance, degenerate splits.
     _ld_pos = int(y_win_train.sum()); _ld_neg = int(len(y_win_train) - _ld_pos)
-    _ld_test_pos = int((test_df["_win_label"].astype(int)).sum())
-    _ld_test_neg = int(len(test_df) - _ld_test_pos)
+    _ld_test_pos = int(y_win_test.sum())
+    _ld_test_neg = int(len(y_win_test) - _ld_test_pos)
     _ret_col = f"h{args.hold}_return"
     _rets_tr = pd.to_numeric(train_df.get(_ret_col, pd.Series([], dtype=float)), errors="coerce").dropna()
     _rets_te = pd.to_numeric(test_df.get(_ret_col, pd.Series([], dtype=float)), errors="coerce").dropna()
@@ -574,6 +589,7 @@ def train_models(args) -> dict:
             "ml_large_loss_max": args.ml_large_loss_max,
             "executed_weight": getattr(args, "executed_weight", 1.0),
             "executed_only": getattr(args, "executed_only", False),
+            "win_label_threshold": win_label_threshold,
         },
         "label_distribution": label_distribution,
         "models": {},
@@ -587,8 +603,6 @@ def train_models(args) -> dict:
           + (f" [{_executed_train:,} executed]" if _executed_train else ""))
 
     # ── Win/loss classifier — ensemble XGBoost + RandomForest when both available
-    y_win_train = train_df["_win_label"].astype(int).to_numpy()
-    y_win_test = test_df["_win_label"].astype(int).to_numpy()
     if len(set(y_win_train)) < 2:
         raise SystemExit("Win/loss labels contain one class only; cannot train classifier.")
 
@@ -887,9 +901,16 @@ def parse_args():
     parser.add_argument("--min-samples-leaf", type=int, default=30)
     parser.add_argument(
-        "--executed-weight", type=float, default=20.0,
+        "--executed-weight", type=float, default=20.0,
         help="Sample weight multiplier for executed (rule-passing) rows vs rejected rows. "
              "0 or 1.0 = no weighting. Default: 20 (executed rows count 20× in training)."
     )
+    parser.add_argument(
+        "--win-label-threshold", type=float, default=0.01,
+        help="Minimum forward return required to label a row as a win. "
+             "Default: 0.01 (1%%). The backtest frame default is 0.005 (0.5%%); "
+             "raising this threshold reduces near-breakeven label noise."
+    )
     parser.add_argument(
         "--executed-only", action="store_true", default=False,
         help="Train exclusively on executed (rule-passing) rows. "
--- a/scripts/retrain_weekly.py
+++ b/scripts/retrain_weekly.py
@@ -120,9 +120,15 @@ def main():
                         help="Maximum calibration Brier score to accept bundle. Default 0.24.")
     parser.add_argument("--skip-leakage-check", action="store_true",
                         help="DANGEROUS: skip leakage check. Only for debugging.")
@@ -122,8 +128,14 @@ def main():
     parser.add_argument(
-        "--executed-weight", type=float, default=20.0,
-        help="Sample weight for executed (rule-passing) rows in training. Default 20× over rejected."
+        "--executed-weight", type=float, default=8.0,
+        help="Sample weight for executed (rule-passing) rows in training. Default 8× over rejected."
+    )
+    parser.add_argument(
+        "--win-label-threshold", type=float, default=0.01,
+        help="Return threshold used to label a training row as a win. "
+             "Default 0.01 (1%%); do not lower below 0.005 without defining a new validation plan."
     )
     parser.add_argument("--dry-run", action="store_true", help="Print commands only, don't run.")
     args = parser.parse_args()
@@ -165,6 +177,7 @@ def main():
         "--ml-expected-return-min", "-0.01",
         "--calibrate",                         # always ON — probability calibration required
         "--executed-weight", str(args.executed_weight),  # upweight rule-passing rows
+        "--win-label-threshold", str(args.win_label_threshold),
         "--run-walk-forward",                  # include walk-forward in report
     ]
```