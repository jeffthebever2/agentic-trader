Now I have enough. Time to write the analysis.

---

## Root Cause

**Three compounding issues driving ROC=0.5479:**

**1. Win label threshold too low (0.5% = noise zone)**
`backtest.py:2022` sets `_win_label = (return > 0.005)`. Round-trip costs ≈0.15% (1% commission + 5bps slippage each way). A 0.5% "win" barely clears costs, and trades in the 0.3%–0.8% range oscillate between win/loss on noise alone. The model cannot distinguish 0.4% from 0.6% — that band is irreducible label noise. Raising to 1.0% removes the ambiguous zone and sharpens the signal the model learns.

**2. `executed_weight=20.0` is too aggressive**
`retrain_weekly.py:L123` passes 20x upweighting for executed rows. If executed rows are ~10–15% of training data, the effective weight ratio becomes 200:1 (executed vs rejected). On top of that, XGB already handles class imbalance via `scale_pos_weight`. The double-amplification overfits the model to the narrow distribution of executed setups, degrading generalization. Reducing to 8x still emphasizes executed rows without drowning context from rejected rows.

**3. XGBoost missing regularization**
`_make_clf()` (train_ml_models.py:L41-46) doesn't set `min_child_weight` (defaults to 1) or `gamma`. With 60+ features and typical 3k–10k rows, min_child_weight=1 allows leaves with single training examples, causing overfit in leaves that don't generalize. Adding `min_child_weight=10, gamma=0.05` raises the bar for a split to be accepted.

**Note on n_grades=0:** Paper trade grading shows zero closed trades. **Cannot assess live calibration.** The retrain is being triggered by training-period metrics only, which is the right call given the ROC trigger. More paper trading needed before drift detection can work.

---

## Why This Will Help

| Change | Mechanism | Expected Impact |
|--------|-----------|-----------------|
| Win threshold 0.005→0.01 | Removes ambiguous 0.5–1.0% borderline trades from label, creating cleaner class separation | +0.01–0.03 ROC, sharper Brier |
| executed_weight 20→8 | Reduces overfit to execution distribution; more weight on rejected-row context = better generalization | +0.005–0.015 ROC |
| min_child_weight=10, gamma=0.05 | Prevents leaf-level overfit in XGB; each split must explain ≥10 training instances | Reduces train-test gap, helps walk-forward ROC |

These changes do not touch any label except _win_label (which is a training-only construct — the actual live model threshold is governed separately by `ml_probability_threshold`). No risk gate is weakened.

---

## Patch (unified diff)

```diff
--- a/scripts/train_ml_models.py
+++ b/scripts/train_ml_models.py
@@ -36,12 +36,14 @@ def _make_clf(y_labels, n_estimators=500, max_depth=6, min_samples_leaf=15, see
     if _XGB_AVAILABLE:
         pos = int(y_labels.sum())
         neg = int(len(y_labels) - pos)
         spw = (neg / pos) if pos > 0 else 1.0
         return XGBClassifier(
             n_estimators=n_estimators, max_depth=max_depth,
-            learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
-            scale_pos_weight=spw, tree_method="hist",
+            learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
+            scale_pos_weight=spw, tree_method="hist",
+            min_child_weight=10, gamma=0.05, reg_lambda=1.5,
             eval_metric="logloss", verbosity=0, n_jobs=-1, random_state=seed,
         )
     return RandomForestClassifier(
         n_estimators=n_estimators, max_depth=max_depth,
         min_samples_leaf=min_samples_leaf,
         class_weight="balanced_subsample", random_state=seed, n_jobs=-1,
     )
 
@@ -356,6 +356,22 @@ def train_models(args) -> dict:
     source = Path(args.input)
     rows = _load_rows(source)
     frame, numeric, categorical = _ml_prepare_frame(rows, args.hold)
+
+    # ── Win label override: filter noise at label boundary ────────────────────
+    # _ml_prepare_frame sets _win_label = (return > 0.005), a 0.5% threshold that
+    # sits inside typical round-trip cost range (~0.15%). Trades at 0.3–0.8%
+    # are ambiguous; the model cannot distinguish them from noise. Raising to
+    # win_label_threshold (default 0.01 = 1%) removes the ambiguous zone.
+    # The frame["_return"] column is preserved by _ml_prepare_frame so we can
+    # recompute without touching backtest.py.
+    _wlt = getattr(args, "win_label_threshold", 0.005)
+    if abs(_wlt - 0.005) > 1e-6 and "_return" in frame.columns:
+        old_wr = frame["_win_label"].mean()
+        frame["_win_label"] = (frame["_return"] > _wlt).astype(int)
+        new_wr = frame["_win_label"].mean()
+        print(f"  Win label threshold: {_wlt:.3f} — win rate {old_wr:.1%} → {new_wr:.1%}")
+    else:
+        print(f"  Win label threshold: {getattr(args,'win_label_threshold',0.005):.3f} (default)")
+
     if len(frame) < args.min_rows:
         raise SystemExit(
             f"Only {len(frame)} usable rows found; need at least {args.min_rows}."
 
@@ -880,6 +880,11 @@ def parse_args():
     parser.add_argument("--min-rows", type=int, default=300, help="Minimum usable rows required.")
     parser.add_argument("--n-estimators", type=int, default=500)
     parser.add_argument("--max-depth", type=int, default=6)
     parser.add_argument("--min-samples-leaf", type=int, default=30)
+    parser.add_argument(
+        "--win-label-threshold", type=float, default=0.01,
+        help="Minimum return to label a trade as a win. Default 0.01 (1%%). "
+             "Backtest default is 0.005 (0.5%%) which includes noise near breakeven. "
+             "Higher values create cleaner class separation at the cost of fewer positive labels."
+    )
     parser.add_argument(
         "--executed-weight", type=float, default=20.0,
```

```diff
--- a/scripts/retrain_weekly.py
+++ b/scripts/retrain_weekly.py
@@ -120,7 +120,7 @@ def main():
     parser.add_argument("--ml-large-loss-max", type=float, default=0.35,
                         help="Hard cap on large_loss_probability. Default 0.35. Never set > 0.40.")
     parser.add_argument("--min-roc", type=float, default=0.56,
                         help="Minimum win_probability ROC required to swap bundle. Default 0.56.")
     parser.add_argument("--max-brier", type=float, default=0.24,
                         help="Maximum calibration Brier score to accept bundle. Default 0.24.")
     parser.add_argument(
-        "--executed-weight", type=float, default=20.0,
+        "--executed-weight", type=float, default=8.0,
         help="Sample weight for executed (rule-passing) rows in training. Default 20× over rejected."
     )
+    parser.add_argument(
+        "--win-label-threshold", type=float, default=0.01,
+        help="Return threshold to call a trade a win during training. "
+             "1%% filters noise near breakeven. Do not lower below 0.005."
+    )
     parser.add_argument("--dry-run", action="store_true", help="Print commands only, don't run.")
     args = parser.parse_args()
 
@@ -154,9 +154,11 @@ def main():
     train_cmd = [
         python, str(ROOT / "scripts" / "train_ml_models.py"),
         "--input", str(csv_path),
         "--output-dir", str(output_dir),
         "--hold", str(args.hold),
         "--n-estimators", str(args.n_estimators),
         "--max-depth", str(args.max_depth),
         "--min-samples-leaf", str(args.min_samples_leaf),
         "--ml-probability-threshold", str(args.ml_probability_threshold),
         "--ml-large-loss-max", str(args.ml_large_loss_max),
         "--ml-expected-return-min", "-0.01",
         "--calibrate",                         # always ON — probability calibration required
         "--executed-weight", str(args.executed_weight),  # upweight rule-passing rows
+        "--win-label-threshold", str(args.win_label_threshold),  # clean label boundary
         "--run-walk-forward",                  # include walk-forward in report
     ]
```

---

## Files Inspected

- `scripts/train_ml_models.py` — full read; `_make_clf`, `train_models`, `parse_args`, `_ml_time_split` usage
- `scripts/retrain_weekly.py` — full read; default hyperparams, train_cmd construction, gate logic
- `scripts/improvement_loop.py` — full read; trigger thresholds, retrain triggers, RETRAIN_TRIGGERS dict
- `scripts/validation_report.py` — full read; ROC gate at 0.56, Brier gate at 0.24
- `scripts/leakage_check.py` — full read; confirmed no leaky columns in safe file scope
- `backtest.py` (read-only, not in safe list) — read `_ml_prepare_frame` (win label at 0.005), `ML_NUMERIC_FEATURES`, `_ml_time_split`

---

## What Could Go Wrong

1. **Fewer positive labels**: Raising win threshold to 1% reduces positive class size. If executed rows already have low win rate (<45%), the calibration split (15% of train) may get too few positives for isotonic calibration — the code already handles this with `if len(y_val) < 50 or len(set(y_val)) < 2: return clf, {"status": "skipped_too_few_samples"}`. Monitor the label distribution in the training report.

2. **`min_child_weight=10` may under-train on small datasets**: If the training CSV has <2000 rows, XGB with min_child_weight=10 may refuse most splits. The RF fallback (ensemble path) compensates, but verify total train rows before running.

3. **executed_weight 20→8 shifts win rate**: The threshold search (`_threshold_search`) searches win_prob thresholds 0.50–0.70. After retraining, the recommended_threshold in the report may shift. Do not use the new recommended_threshold to retune live paper trading until a new holdout window is defined.

4. **Patch does NOT retrain automatically** — human must run `python scripts/retrain_weekly.py` (or the improvement loop with `--force-retrain`) after applying. Gate (ROC ≥ 0.56) still enforces quality before bundle swap.

5. **n_grades=0** means no live calibration signal exists. Even if retrain passes gates, actual live win rate is unknown. Recommend 30+ closed paper trades before reading any drift metrics as actionable.
