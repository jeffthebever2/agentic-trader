#!/usr/bin/env python3
"""Log rotation for Agentic Trader services.
Rotate files > MAX_BYTES, keep MAX_BACKUPS compressed archives.
Safe to run anytime — skips files that don't exist yet.
"""
from __future__ import annotations

import gzip
import os
import shutil
import time
from pathlib import Path

ROOT   = Path(__file__).resolve().parents[1]
LOGS   = ROOT / "logs"

MAX_BYTES   = 10 * 1024 * 1024   # 10 MB
MAX_BACKUPS = 7

LOG_FILES = [
    LOGS / "webserver.log",
    LOGS / "webserver.err",
    LOGS / "papertrader.log",
    LOGS / "papertrader.err",
    LOGS / "tunnel.log",
    LOGS / "tunnel.err",
    LOGS / "autofix.log",
    LOGS / "autofix_monitor.log",
    LOGS / "autofix_monitor.err",
]


def rotate(path: Path) -> None:
    if not path.exists():
        return
    if path.stat().st_size < MAX_BYTES:
        return

    stamp = time.strftime("%Y%m%d_%H%M%S")
    archive = path.with_suffix(f".{stamp}.log.gz")

    with open(path, "rb") as f_in, gzip.open(archive, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)

    # Truncate original (don't delete — launchd keeps writing to same inode)
    path.write_bytes(b"")

    # Prune old archives
    old = sorted(path.parent.glob(f"{path.stem}.*.log.gz"))
    for stale in old[:-MAX_BACKUPS]:
        stale.unlink(missing_ok=True)

    print(f"[rotate_logs] rotated {path.name} → {archive.name}")


if __name__ == "__main__":
    for log in LOG_FILES:
        try:
            rotate(log)
        except Exception as e:
            print(f"[rotate_logs] ERROR rotating {log}: {e}")
