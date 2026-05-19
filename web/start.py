#!/usr/bin/env python
"""Launch the TradingAgents Web UI.

Usage:
    python web/start.py              # default port 8000
    python web/start.py --port 8080
"""
import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    import uvicorn
except ImportError:
    print("uvicorn not installed. Run:  pip install fastapi uvicorn")
    sys.exit(1)

try:
    import fastapi  # noqa: F401
except ImportError:
    print("fastapi not installed. Run:  pip install fastapi uvicorn")
    sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TradingAgents Web UI")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8001, help="Port to listen on (default: 8001)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    args = parser.parse_args()

    print(f"\n  TradingAgents Web UI")
    print(f"  http://localhost:{args.port}\n")

    uvicorn.run(
        "web.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
