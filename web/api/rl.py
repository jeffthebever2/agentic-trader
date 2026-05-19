"""RL agent API — status, training stream, checkpoint info."""
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
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

CHECKPOINT_DIR = ROOT / "rl_models" / "td3_checkpoint"
META_PATH = CHECKPOINT_DIR / "meta.json"
META_NPY   = CHECKPOINT_DIR / "meta.npy"


def _load_status() -> dict:
    status = {
        "checkpoint_exists": False,
        "checkpoint_dir": str(CHECKPOINT_DIR),
        "tickers": [],
        "obs_dim": None,
        "act_dim": None,
        "hidden": [],
        "max_position_size": None,
        "starting_cash": None,
        "total_steps": None,
        "files": [],
        "status_label": "No checkpoint found",
    }

    if not CHECKPOINT_DIR.exists():
        return status

    files = [f.name for f in CHECKPOINT_DIR.iterdir() if f.is_file()]
    status["files"] = sorted(files)
    status["checkpoint_exists"] = any(f.endswith(".pt") for f in files)

    if META_PATH.exists():
        try:
            meta = json.loads(META_PATH.read_text())
            status.update({
                "tickers": meta.get("tickers", []),
                "obs_dim": meta.get("obs_dim"),
                "act_dim": meta.get("act_dim"),
                "hidden": meta.get("hidden", []),
                "max_position_size": meta.get("max_position_size"),
                "starting_cash": meta.get("starting_cash"),
                "status_label": "Checkpoint ready",
            })
        except Exception as e:
            status["status_label"] = f"Meta read error: {e}"

    if META_NPY.exists():
        try:
            import numpy as np
            arr = np.load(str(META_NPY))
            status["total_steps"] = int(arr)
        except Exception:
            pass

    return status


@router.get("/rl/status")
async def get_rl_status():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _load_status)


@router.websocket("/ws/rl-train")
async def ws_rl_train(websocket: WebSocket):
    """Stream train_rl_agent.py output. Expects JSON config."""
    await websocket.accept()
    queue: asyncio.Queue = asyncio.Queue()
    main_loop = asyncio.get_running_loop()

    try:
        cfg = await websocket.receive_json()
    except Exception as e:
        await websocket.send_json({"type": "error", "message": str(e)})
        await websocket.close()
        return

    tickers      = cfg.get("tickers", ["NVDA", "AAPL", "MSFT", "GOOGL", "AMZN"])
    start        = cfg.get("start", "2018-01-01")
    end          = cfg.get("end", "2023-12-31")
    iterations   = int(cfg.get("iterations", 500_000))
    device       = cfg.get("device", "cpu")
    checkpoint   = cfg.get("checkpoint", "")
    checkpoint_dir = cfg.get("checkpoint_dir", str(CHECKPOINT_DIR))

    def run_sync():
        try:
            cmd = [
                sys.executable,
                str(ROOT / "scripts" / "train_rl_agent.py"),
                "--tickers", *tickers,
                "--start", start,
                "--end", end,
                "--iterations", str(iterations),
                "--device", device,
                "--checkpoint-dir", checkpoint_dir,
            ]
            if checkpoint:
                cmd += ["--checkpoint", checkpoint]

            asyncio.run_coroutine_threadsafe(
                queue.put({"type": "info", "message": f"Running: {' '.join(cmd)}"}),
                main_loop,
            )
            proc = subprocess.Popen(
                cmd, cwd=str(ROOT),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
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
