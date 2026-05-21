"""Restore and run Agentic Trader on a fresh/local machine.

This command is intentionally operational rather than clever: it checks the
things that commonly break after cloning the repo without the local 11GB of
generated artifacts, restores optional ML/data folders from a local backup, and
starts the web app plus Cloudflare Tunnel with useful diagnostics.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 8001
DEFAULT_ARTIFACT_PATHS = [
    "ml_models",
    "rl_models",
    ".backtest_cache",
    "backtest_index.db",
]
REQUIRED_ENV_HINTS = {
    "Cloudflare Workers AI": ["CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"],
    "Cloudflare Access": ["CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD"],
    "Email/SMS": ["SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "SENDBLUE_API_KEY"],
}


def load_local_env() -> None:
    """Load .env for diagnostics/startup without printing secret values."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
    except Exception:
        for line in env_path.read_text(errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def info(message: str) -> None:
    print(f"[agentic-restore] {message}")


def warn(message: str) -> None:
    print(f"[agentic-restore][WARN] {message}")


def fail(message: str, code: int = 1) -> None:
    print(f"[agentic-restore][ERROR] {message}", file=sys.stderr)
    raise SystemExit(code)


def run(
    cmd: list[str],
    *,
    check: bool = True,
    capture: bool = False,
    cwd: Path = ROOT,
) -> subprocess.CompletedProcess[str]:
    info("$ " + " ".join(cmd))
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        check=check,
        text=True,
        capture_output=capture,
    )


def command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def port_pid(port: int) -> str:
    if not command_exists("lsof"):
        return ""
    proc = subprocess.run(
        ["lsof", "-tiTCP:%s" % port, "-sTCP:LISTEN"],
        text=True,
        capture_output=True,
        check=False,
    )
    return proc.stdout.strip()


def health_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/health"


def web_health(port: int, timeout: float = 2.0) -> tuple[bool, str]:
    try:
        with urlopen(health_url(port), timeout=timeout) as response:
            return response.status == 200, response.read().decode("utf-8")[:500]
    except (URLError, TimeoutError, OSError) as exc:
        return False, str(exc)


def wait_for_health(port: int, seconds: int = 20) -> bool:
    for _ in range(seconds):
        ok, body = web_health(port)
        if ok:
            info(f"web health ok: {body}")
            return True
        time.sleep(1)
    ok, body = web_health(port)
    warn(f"web health did not turn green: {body}")
    return False


def copy_path(src: Path, dst: Path) -> None:
    if not src.exists():
        warn(f"artifact missing in backup: {src}")
        return
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    info(f"restored {dst.relative_to(ROOT)}")


def restore_data(args: argparse.Namespace) -> None:
    if args.artifact_tar:
        tar_path = Path(args.artifact_tar).expanduser().resolve()
        if not tar_path.exists():
            fail(f"artifact tar not found: {tar_path}")
        info(f"extracting artifact tar: {tar_path}")
        with tarfile.open(tar_path) as tar:
            safe_extract(tar, ROOT)

    if args.artifact_dir:
        artifact_dir = Path(args.artifact_dir).expanduser().resolve()
        if not artifact_dir.exists():
            fail(f"artifact directory not found: {artifact_dir}")
        for rel in DEFAULT_ARTIFACT_PATHS:
            copy_path(artifact_dir / rel, ROOT / rel)

    for rel in ["ml_models", "rl_models", "tmp"]:
        (ROOT / rel).mkdir(exist_ok=True)

    check_artifacts()


def safe_extract(tar: tarfile.TarFile, destination: Path) -> None:
    dest = destination.resolve()
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        if not str(target).startswith(str(dest)):
            fail(f"refusing unsafe tar member path: {member.name}")
    tar.extractall(dest)


def make_artifact_bundle(args: argparse.Namespace) -> None:
    out = Path(args.output).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    info(f"writing artifact bundle: {out}")
    with tarfile.open(out, "w:gz") as tar:
        for rel in DEFAULT_ARTIFACT_PATHS:
            path = ROOT / rel
            if path.exists():
                tar.add(path, arcname=rel)
                info(f"packed {rel}")
            else:
                warn(f"not found, skipped: {rel}")


def check_artifacts() -> None:
    expected_models = [
        ROOT / "ml_models/stock_universe/model_bundle.joblib",
        ROOT / "ml_models/latest/model_bundle.joblib",
    ]
    found_any = False
    for model in expected_models:
        if model.exists():
            found_any = True
            info(f"ML model found: {model.relative_to(ROOT)}")
    if not found_any:
        warn(
            "No ML model bundle found. Paper trading can still run, but ML scoring "
            "will be limited until you restore ml_models or retrain."
        )


def doctor(args: argparse.Namespace) -> None:
    info(f"project root: {ROOT}")
    info(f"python: {sys.executable}")
    for command in ["git", "python3", "uv", "cloudflared", "screen", "lsof"]:
        if command_exists(command):
            info(f"{command}: {shutil.which(command)}")
        else:
            warn(f"{command} not found")

    if not (ROOT / ".env").exists():
        warn(".env is missing. Copy your private env file or configure secrets before live use.")
    else:
        info(".env exists")

    for group, envs in REQUIRED_ENV_HINTS.items():
        missing = [env for env in envs if not os.getenv(env)]
        if missing:
            warn(f"{group}: missing {', '.join(missing)}")
        else:
            info(f"{group}: configured")

    check_artifacts()
    pid = port_pid(args.port)
    if pid:
        info(f"port {args.port} is already listening (pid {pid})")
    else:
        info(f"port {args.port} is free")
    ok, body = web_health(args.port)
    info(f"web health: {'ok' if ok else 'not running'} {body}")


def install(args: argparse.Namespace) -> None:
    if command_exists("uv"):
        cmd = ["uv", "sync", "--extra", "web", "--extra", "dev"]
        if args.media:
            cmd.extend(["--extra", "media"])
        run(cmd)
    else:
        warn("uv is not installed; falling back to pip editable install")
        run([sys.executable, "-m", "pip", "install", "-e", ".[web,dev]"])


def stop(args: argparse.Namespace) -> None:
    pid = port_pid(args.port)
    if pid:
        for part in pid.splitlines():
            run(["kill", part], check=False)
        info(f"stopped web listener on port {args.port}")
    else:
        info(f"no web listener on port {args.port}")

    if command_exists("screen"):
        for session in [args.web_session, args.tunnel_session]:
            run(["screen", "-S", session, "-X", "quit"], check=False)


def start_web(args: argparse.Namespace) -> None:
    pid = port_pid(args.port)
    if pid:
        if args.restart:
            stop(args)
        else:
            info(f"web already running on port {args.port} (pid {pid})")
            return

    (ROOT / "tmp").mkdir(exist_ok=True)
    env = os.environ.copy()
    env["WEB_HOST"] = args.host
    env["PORT"] = str(args.port)

    cmd = (
        f"cd {shell_quote(ROOT)} && exec {shell_quote(sys.executable)} "
        f"web/start.py --host {shell_quote(args.host)} --port {args.port} "
        ">> tmp/web.screen.log 2>&1"
    )
    if command_exists("screen"):
        run(["screen", "-dmS", args.web_session, "bash", "-lc", cmd], check=True)
    else:
        warn("screen not found; using nohup fallback")
        subprocess.Popen(
            ["bash", "-lc", cmd],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    wait_for_health(args.port)


def start_tunnel(args: argparse.Namespace) -> None:
    if not command_exists("cloudflared"):
        warn("cloudflared is not installed. Install with: brew install cloudflared")
        return

    (ROOT / "tmp").mkdir(exist_ok=True)
    tunnel_token = os.getenv("CLOUDFLARED_TUNNEL_TOKEN", "").strip()
    tunnel_config = Path(args.tunnel_config).expanduser()

    if args.quick_tunnel:
        tunnel_cmd = f"cloudflared tunnel --url http://127.0.0.1:{args.port}"
    elif tunnel_token:
        tunnel_cmd = "cloudflared tunnel run --token \"$CLOUDFLARED_TUNNEL_TOKEN\""
    elif tunnel_config.exists():
        tunnel_cmd = (
            f"cloudflared tunnel --config {shell_quote(tunnel_config)} "
            f"run {shell_quote(args.tunnel_name)}"
        )
    else:
        warn(f"tunnel config not found: {tunnel_config}")
        warn("Use --quick-tunnel for a temporary URL, or run: cloudflared tunnel login")
        return

    cmd = f"{tunnel_cmd} >> {shell_quote(ROOT / 'tmp/cloudflared.screen.log')} 2>&1"
    if command_exists("screen"):
        run(["screen", "-S", args.tunnel_session, "-X", "quit"], check=False)
        run(["screen", "-dmS", args.tunnel_session, "bash", "-lc", cmd], check=True)
    else:
        subprocess.Popen(["bash", "-lc", cmd], cwd=str(ROOT))

    time.sleep(3)
    info("tunnel started; log: tmp/cloudflared.screen.log")
    show_tunnel_status(args)


def show_tunnel_status(args: argparse.Namespace) -> None:
    if not command_exists("cloudflared"):
        return
    if args.quick_tunnel:
        log = ROOT / "tmp/cloudflared.screen.log"
        if log.exists():
            text = log.read_text(errors="ignore")
            import re

            match = re.search(r"https://[a-zA-Z0-9-]+\\.trycloudflare\\.com", text)
            if match:
                info(f"quick tunnel URL: {match.group(0)}")
        return
    proc = subprocess.run(
        ["cloudflared", "tunnel", "info", args.tunnel_name],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode == 0:
        info(proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else "tunnel info ok")
    else:
        warn("could not read tunnel info. Check that you are logged in to cloudflared.")


def status(args: argparse.Namespace) -> None:
    pid = port_pid(args.port)
    info(f"web port {args.port}: {'running pid ' + pid if pid else 'not listening'}")
    ok, body = web_health(args.port)
    info(f"health: {'ok' if ok else 'not ok'} {body}")

    if command_exists("screen"):
        proc = run(["screen", "-ls"], check=False, capture=True)
        print(proc.stdout.strip())

    log_tail(ROOT / "tmp/web.screen.log", "web")
    log_tail(ROOT / "tmp/cloudflared.screen.log", "cloudflared")


def log_tail(path: Path, label: str, lines: int = 20) -> None:
    if not path.exists():
        warn(f"{label} log missing: {path.relative_to(ROOT)}")
        return
    info(f"last {lines} lines of {path.relative_to(ROOT)}")
    text = path.read_text(errors="ignore").splitlines()[-lines:]
    for line in text:
        print("  " + line)


def run_all(args: argparse.Namespace) -> None:
    doctor(args)
    if args.install:
        install(args)
    restore_data(args)
    start_web(args)
    if args.tunnel:
        start_tunnel(args)
    status(args)


def shell_quote(value: object) -> str:
    import shlex

    return shlex.quote(str(value))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentic-restore",
        description="Restore local Agentic Trader artifacts and start web/tunnel services.",
    )
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--port", type=int, default=DEFAULT_PORT)
    shared.add_argument("--host", default="127.0.0.1")
    shared.add_argument("--web-session", default="agentic-web")
    shared.add_argument("--tunnel-session", default="agentic-tunnel")
    shared.add_argument("--tunnel-name", default=os.getenv("CLOUDFLARED_TUNNEL", "dsadsa"))
    shared.add_argument("--tunnel-config", default=str(Path.home() / ".cloudflared/config.yml"))
    shared.add_argument("--quick-tunnel", action="store_true")
    shared.add_argument("--restart", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", parents=[shared]).set_defaults(func=doctor)

    restore = sub.add_parser("restore-data", parents=[shared])
    restore.add_argument("--artifact-dir")
    restore.add_argument("--artifact-tar")
    restore.set_defaults(func=restore_data)

    bundle = sub.add_parser("bundle-data")
    bundle.add_argument("--output", default="agentic-trader-artifacts.tar.gz")
    bundle.set_defaults(func=make_artifact_bundle)

    install_cmd = sub.add_parser("install")
    install_cmd.add_argument("--media", action="store_true")
    install_cmd.set_defaults(func=install)

    start = sub.add_parser("start", parents=[shared])
    start.add_argument("--no-tunnel", dest="tunnel", action="store_false")
    start.set_defaults(func=lambda args: (start_web(args), start_tunnel(args) if args.tunnel else None), tunnel=True)

    sub.add_parser("stop", parents=[shared]).set_defaults(func=stop)
    sub.add_parser("status", parents=[shared]).set_defaults(func=status)

    all_cmd = sub.add_parser("all", parents=[shared])
    all_cmd.add_argument("--artifact-dir")
    all_cmd.add_argument("--artifact-tar")
    all_cmd.add_argument("--install", action="store_true")
    all_cmd.add_argument("--media", action="store_true")
    all_cmd.add_argument("--no-tunnel", dest="tunnel", action="store_false")
    all_cmd.set_defaults(func=run_all, tunnel=True)
    return parser


def main() -> None:
    load_local_env()
    args = build_parser().parse_args()
    try:
        args.func(args)
    except subprocess.CalledProcessError as exc:
        fail(f"command failed with exit {exc.returncode}: {' '.join(exc.cmd)}")
    except KeyboardInterrupt:
        fail("interrupted", code=130)


if __name__ == "__main__":
    main()
