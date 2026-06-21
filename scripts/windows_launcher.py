#!/usr/bin/env python3
"""Agentic Trader - Windows launcher and doctor.

Invoked by start.bat (which only has to find a Python). This script is
stdlib-only on purpose, so it runs *before* any dependencies are installed.
It sets up the virtual environment, installs dependencies, prepares .env,
builds the dashboard UI, checks the port, and starts the server - printing
plain-English status and, on any failure, exactly how to fix it.

It is Windows-focused but OS-aware, so it can also be run on macOS/Linux for
testing the checks.

Usage (normally via start.bat):
    start.bat            set up anything missing, then start the dashboard
    start.bat setup      prepare everything but do not start
    start.bat doctor     report what is installed / wrong (changes nothing)
    start.bat paper      run the paper-trading engine
    start.bat train      run the full training pipeline
    start.bat retrain    weekly model refresh
    start.bat all        paper trading (new window) + dashboard
    start.bat stop       stop the dashboard (free port 8001)
    start.bat help       show help
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # repo root (scripts/..)
IS_WINDOWS = os.name == "nt"
PORT = 8001
URL = f"http://localhost:{PORT}/app"


# --------------------------------------------------------------------------- #
# plain-text output helpers
# --------------------------------------------------------------------------- #
def ok(msg: str) -> None:
    print(f"[ OK ] {msg}")


def info(msg: str) -> None:
    print(f"[ .. ] {msg}")


def warn(msg: str) -> None:
    print(f"[WARN] {msg}")


def fail(msg: str) -> None:
    print(f"[FAIL] {msg}")


def line() -> None:
    print("-" * 66)


def banner() -> None:
    print()
    print("=" * 66)
    print("   Agentic Trader  -  Windows launcher")
    print("=" * 66)


def die(msg: str, hint: str | None = None, code: int = 1) -> "NoReturn":  # type: ignore[name-defined]
    """Print a clear failure with a remediation hint, then exit non-zero."""
    print()
    line()
    fail(msg)
    if hint:
        print()
        for ln in hint.splitlines():
            print("   " + ln)
    line()
    sys.exit(code)


def venv_python() -> Path:
    if IS_WINDOWS:
        return ROOT / ".venv" / "Scripts" / "python.exe"
    return ROOT / ".venv" / "bin" / "python"


def _deps_ok(vpy: Path) -> bool:
    if not vpy.exists():
        return False
    return (
        subprocess.run(
            [str(vpy), "-c", "import fastapi, uvicorn"],
            capture_output=True,
        ).returncode
        == 0
    )


def port_in_use(port: int = PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex(("127.0.0.1", port)) == 0


# --------------------------------------------------------------------------- #
# setup steps
# --------------------------------------------------------------------------- #
def check_python_version() -> None:
    v = sys.version_info
    if v < (3, 10):
        die(
            f"Python {v.major}.{v.minor} is too old - this project needs 3.10 or newer.",
            "Install a newer Python from https://www.python.org/downloads/\n"
            "On the first install screen, TICK 'Add python.exe to PATH'.\n"
            "Then run start.bat again.",
        )
    ok(f"Python {v.major}.{v.minor}.{v.micro}")


def ensure_venv() -> Path:
    vpy = venv_python()
    if vpy.exists():
        ok("Virtual environment present (.venv)")
        return vpy
    info("Creating virtual environment (.venv) - first run only ...")
    rc = subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")]).returncode
    if rc != 0 or not vpy.exists():
        die(
            "Could not create the virtual environment.",
            "If you installed Python from the Microsoft Store, that build can be\n"
            "restricted - reinstall from https://www.python.org/downloads/ instead.\n"
            "Otherwise delete the .venv folder and run start.bat again.",
        )
    ok("Virtual environment created")
    return vpy


def ensure_deps(vpy: Path, force: bool = False) -> None:
    if _deps_ok(vpy) and not force:
        ok("Dependencies installed")
        return
    info("Installing dependencies - first run can take a few minutes ...")
    subprocess.run(
        [str(vpy), "-m", "pip", "install", "--upgrade", "pip"],
        cwd=str(ROOT),
    )
    rc = subprocess.run(
        [str(vpy), "-m", "pip", "install", "-e", ".[web]"],
        cwd=str(ROOT),
    ).returncode
    if rc != 0 or not _deps_ok(vpy):
        die(
            "Installing dependencies failed (see the pip output above).",
            "Most common causes:\n"
            "  * No internet connection.\n"
            "  * A package needs a compiler: install the free\n"
            "    'Microsoft C++ Build Tools' then run start.bat again.\n"
            "  * Behind a company proxy: set the HTTPS_PROXY environment\n"
            "    variable (and PIP_INDEX_URL if you use a private mirror).\n"
            "If it still fails, delete the .venv folder and retry for a clean install.",
        )
    ok("Dependencies installed")


def ensure_env() -> None:
    env = ROOT / ".env"
    example = ROOT / ".env.example"
    if env.exists():
        ok(".env present")
        return
    if example.exists():
        shutil.copyfile(example, env)
        warn("No .env found - created one from .env.example.")
        print("       It runs in safe PAPER mode as-is. Live trading stays OFF.")
        print("       Edit .env later to add broker / API keys.")
    else:
        warn("No .env or .env.example found - using built-in defaults.")


def check_frontend(build: bool = True) -> None:
    dist = ROOT / "web" / "static" / "dist" / "index.html"
    if dist.exists():
        ok("Dashboard UI is built")
        return
    warn("Dashboard UI (web/static/dist) is not built.")
    npm = shutil.which("npm")
    if not npm:
        print("       Node.js / npm not found, so I cannot build it here.")
        print("       The server still runs, but the web page may be blank.")
        print("       Fix: install Node 18+ from https://nodejs.org , then run")
        print("       start.bat again.")
        return
    if not build:
        return
    info("Building the dashboard UI (npm) - one-time, a couple of minutes ...")
    fe = ROOT / "frontend"
    if subprocess.run([npm, "install"], cwd=str(fe)).returncode == 0:
        subprocess.run([npm, "run", "build"], cwd=str(fe))
    if dist.exists():
        ok("Dashboard UI built")
    else:
        warn("UI build did not finish cleanly - the server will still start.")


def check_model() -> None:
    latest = ROOT / "ml_models" / "latest" / "model_bundle.joblib"
    fallback = ROOT / "ml_models" / "stock_universe" / "model_bundle.joblib"
    if latest.exists() or fallback.exists():
        ok("ML model bundle found")
    else:
        warn("No ML model bundle (ml_models/latest). ML scoring is limited until")
        print("       you train one (start.bat train). The dashboard still runs.")


# --------------------------------------------------------------------------- #
# run / process control
# --------------------------------------------------------------------------- #
def _open_browser_later() -> None:
    def _open() -> None:
        try:
            import webbrowser

            webbrowser.open(URL)
        except Exception:
            pass

    threading.Timer(3.0, _open).start()


def _run_in_venv(vpy: Path, args: list[str], new_window: bool = False) -> int:
    kwargs: dict = {"cwd": str(ROOT)}
    if new_window and IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE  # type: ignore[attr-defined]
        return subprocess.Popen([str(vpy), *args], **kwargs).pid
    return subprocess.run([str(vpy), *args], **kwargs).returncode


def start_web(vpy: Path) -> int:
    if port_in_use():
        warn(f"Port {PORT} is already in use - the dashboard may already be running.")
        print(f"       Open {URL} in your browser,")
        print("       or run 'start.bat stop' first to free the port.")
        return 0
    line()
    ok("Starting the Agentic Trader dashboard")
    print(f"       URL:   {URL}")
    print("       Stop:  press Ctrl+C in this window (or run start.bat stop)")
    line()
    _open_browser_later()
    try:
        return _run_in_venv(vpy, ["run_web.py"])
    except KeyboardInterrupt:
        print()
        ok("Dashboard stopped.")
        return 0


def stop() -> int:
    banner()
    if not port_in_use():
        ok(f"Nothing is listening on port {PORT}. Already stopped.")
        return 0
    info(f"Stopping whatever is using port {PORT} ...")
    if IS_WINDOWS:
        out = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True
        ).stdout
        pids = {
            ln.split()[-1]
            for ln in out.splitlines()
            if f":{PORT} " in ln and "LISTENING" in ln
        }
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/PID", pid])
    else:
        subprocess.run(["bash", "-c", f"lsof -ti tcp:{PORT} | xargs -r kill -9"])
    ok("Stopped." if not port_in_use() else f"Port {PORT} may still be releasing.")
    return 0


# --------------------------------------------------------------------------- #
# top-level commands
# --------------------------------------------------------------------------- #
def setup() -> int:
    banner()
    check_python_version()
    vpy = ensure_venv()
    ensure_deps(vpy)
    ensure_env()
    check_frontend()
    check_model()
    line()
    ok("Setup complete. Run start.bat to launch the dashboard.")
    line()
    return 0


def run() -> int:
    banner()
    check_python_version()
    vpy = ensure_venv()
    ensure_deps(vpy)
    ensure_env()
    check_frontend()
    check_model()
    return start_web(vpy)


def run_script(label: str, args: list[str], new_window: bool = False) -> int:
    banner()
    check_python_version()
    vpy = ensure_venv()
    ensure_deps(vpy)
    ensure_env()
    line()
    ok(f"Starting: {label}")
    print("       Press Ctrl+C in this window to stop.")
    line()
    return _run_in_venv(vpy, args, new_window=new_window) and 0


def run_all() -> int:
    banner()
    check_python_version()
    vpy = ensure_venv()
    ensure_deps(vpy)
    ensure_env()
    check_frontend()
    if IS_WINDOWS:
        info("Launching the paper-trading engine in a separate window ...")
        _run_in_venv(vpy, ["scripts/paper_trade_today.py"], new_window=True)
    else:
        info("(On non-Windows, run paper trading separately.)")
    return start_web(vpy)


def doctor() -> int:
    banner()
    print("Checking your setup (this changes nothing)...\n")
    blocking = 0

    v = sys.version_info
    if v >= (3, 10):
        ok(f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        fail(f"Python {v.major}.{v.minor} - need 3.10+ (install from python.org)")
        blocking += 1

    vpy = venv_python()
    if vpy.exists():
        ok("Virtual environment (.venv)")
    else:
        warn("No .venv yet - start.bat will create it")

    if _deps_ok(vpy):
        ok("Dependencies installed (fastapi, uvicorn)")
    else:
        warn("Dependencies not installed yet - start.bat will install them")

    if (ROOT / ".env").exists():
        ok(".env present")
    else:
        warn("No .env yet - start.bat will create it from .env.example")

    if (ROOT / "web" / "static" / "dist" / "index.html").exists():
        ok("Dashboard UI is built")
    else:
        warn("Dashboard UI not built - needs Node 18+ (start.bat will try)")

    if shutil.which("npm"):
        ok("Node / npm available")
    else:
        warn("Node / npm not found (only needed to build the dashboard UI)")

    if shutil.which("git"):
        ok("git available")
    else:
        warn("git not found (only needed to pull updates)")

    if port_in_use():
        warn(f"Port {PORT} is in use - server may already be running")
        print(f"       Open {URL} , or run 'start.bat stop' to free it.")
    else:
        ok(f"Port {PORT} is free")

    check_model()

    print()
    line()
    if blocking:
        fail(f"{blocking} blocking problem(s) - fix the [FAIL] line(s) above, then re-run.")
    else:
        ok("No blocking problems. Run start.bat to launch.")
    line()
    return 1 if blocking else 0


def help_() -> int:
    banner()
    print("Usage:  start.bat [command]\n")
    print("  (no command)   Set up anything missing, then start the dashboard")
    print("  setup          Prepare everything but do not start")
    print("  doctor         Check what is installed / wrong (changes nothing)")
    print("  paper          Run the paper-trading engine")
    print("  train          Full training pipeline (long-running)")
    print("  retrain        Weekly model refresh")
    print("  all            Paper trading (new window) + dashboard")
    print("  stop           Stop the dashboard (free port 8001)")
    print("  help           Show this help")
    print()
    print(f"  Dashboard runs at {URL}")
    return 0


COMMANDS = {
    "": run,
    "run": run,
    "start": run,
    "web": run,
    "setup": setup,
    "doctor": doctor,
    "check": doctor,
    "stop": stop,
    "help": help_,
    "-h": help_,
    "--help": help_,
    "paper": lambda: run_script("paper trading", ["scripts/paper_trade_today.py"]),
    "train": lambda: run_script(
        "full training pipeline",
        ["scripts/train_everything.py", "--tickers", "all_tickers.txt",
         "--include-qlib-features", "--profile", "safe"],
    ),
    "retrain": lambda: run_script(
        "weekly retrain",
        ["scripts/retrain_weekly.py", "--tickers", "all_tickers.txt",
         "--include-qlib-features"],
    ),
    "all": run_all,
}


def main(argv: list[str]) -> int:
    cmd = (argv[1] if len(argv) > 1 else "").lower()
    fn = COMMANDS.get(cmd)
    if fn is None:
        banner()
        warn(f"Unknown command: {cmd}")
        return help_()
    return fn()


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except KeyboardInterrupt:
        print("\n[ OK ] Cancelled.")
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as exc:  # last-resort plain-text error
        print()
        line()
        fail(f"Unexpected error: {exc}")
        print("   Copy the lines above when asking for help.")
        line()
        sys.exit(1)
