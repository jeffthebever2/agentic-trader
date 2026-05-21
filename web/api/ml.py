import asyncio
import concurrent.futures
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

MODEL_DIR = ROOT / "ml_models" / "latest"
BUNDLE_PATH = MODEL_DIR / "model_bundle.joblib"
REPORT_PATH = MODEL_DIR / "training_report.json"
STALE_DAYS = 90


def _load_status() -> dict:
    status = {
        "bundle_exists": BUNDLE_PATH.exists(),
        "report_exists": REPORT_PATH.exists(),
        "bundle_path": str(BUNDLE_PATH),
        "report_path": str(REPORT_PATH),
        "created_at": None,
        "days_old": None,
        "up_to_date": False,
        "status_label": "No model found",
        "settings": {},
        "metrics": {},
        "feature_importance": [],
        "feature_names": [],
    }

    if not BUNDLE_PATH.exists():
        return status

    try:
        import joblib
        bundle = joblib.load(BUNDLE_PATH)
        created_at_raw = bundle.get("created_at")
        status["created_at"] = created_at_raw
        status["feature_names"] = bundle.get("feature_names", [])
        status["thresholds"] = bundle.get("thresholds", {})

        if created_at_raw:
            try:
                created_dt = datetime.fromisoformat(str(created_at_raw))
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                days_old = (now - created_dt).days
                status["days_old"] = days_old
                status["up_to_date"] = days_old <= STALE_DAYS
                if days_old <= STALE_DAYS:
                    status["status_label"] = f"Up to date ({days_old}d ago)"
                else:
                    status["status_label"] = f"Stale ({days_old} days old)"
            except Exception:
                status["status_label"] = "Unknown age"
    except Exception as e:
        status["status_label"] = f"Load error: {e}"
        return status

    if REPORT_PATH.exists():
        try:
            report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
            status["settings"] = report.get("settings", {})

            raw_models = report.get("models", {})
            metrics = {}
            for model_name, model_data in raw_models.items():
                if isinstance(model_data, dict) and "metrics" in model_data:
                    m = model_data["metrics"]
                    metrics[model_name] = {
                        "precision": m.get("precision"),
                        "recall": m.get("recall"),
                        "f1": m.get("f1"),
                        "roc_auc": m.get("roc_auc"),
                        "mae": m.get("mae"),
                        "r2": m.get("r2"),
                        "brier_score": m.get("brier_score"),
                        "average_precision": m.get("average_precision"),
                    }
                    fi = model_data.get("feature_importance", [])
                    if fi and not status["feature_importance"]:
                        status["feature_importance"] = fi[:15]
            status["metrics"] = metrics
        except Exception:
            pass

    return status


@router.get("/ml/status")
async def get_ml_status():
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(_executor, _load_status)
    return result


@router.websocket("/ws/ml-train")
async def ws_ml_train(websocket: WebSocket):
    """Run train_ml_models.py and stream stdout. Expects JSON: {input_file, output_dir?, hold?}"""
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

    input_file = cfg.get("input_file", "")
    output_dir = cfg.get("output_dir", str(MODEL_DIR))
    hold = cfg.get("hold", 3)

    if not input_file:
        await websocket.send_json({"type": "error", "message": "input_file required"})
        await websocket.close()
        return

    def run_sync():
        try:
            cmd = [
                sys.executable,
                str(ROOT / "scripts" / "train_ml_models.py"),
                "--input", input_file,
                "--output-dir", output_dir,
                "--hold", str(hold),
            ]
            asyncio.run_coroutine_threadsafe(
                queue.put({"type": "info", "message": f"Running: {' '.join(cmd)}"}),
                main_loop,
            )
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
                if line:
                    asyncio.run_coroutine_threadsafe(
                        queue.put({"type": "log", "text": line}),
                        main_loop,
                    )
            proc.wait()
            if proc.returncode != 0:
                asyncio.run_coroutine_threadsafe(
                    queue.put({"type": "error", "message": f"Exited with code {proc.returncode}"}),
                    main_loop,
                )
            else:
                new_status = _load_status()
                asyncio.run_coroutine_threadsafe(
                    queue.put({"type": "complete", "status": new_status}),
                    main_loop,
                )
        except Exception as e:
            import traceback
            asyncio.run_coroutine_threadsafe(
                queue.put({"type": "error", "message": str(e), "traceback": traceback.format_exc()}),
                main_loop,
            )
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(None), main_loop)

    _executor.submit(run_sync)

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
