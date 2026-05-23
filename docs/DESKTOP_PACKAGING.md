# Agentic Trader Desktop Packaging

This project uses a small desktop bootstrapper instead of bundling secrets,
models, and the full private repo into an installer.

The DMG/EXE installs a launcher that:

- Clones or updates `git@github.com:jeffthebever2/agentic-trader.git` through SSH.
- Installs web dependencies with `uv sync --extra web --extra dev` when `uv` is available.
- Falls back to `python -m pip install -e .[web,dev]`.
- Starts the local dashboard on `127.0.0.1:8001`.
- Opens the browser to the local dashboard.
- Does **not** start the Cloudflare tunnel unless launched with `--tunnel`.

## Build macOS DMG

Run on macOS:

```bash
bash scripts/build_macos_dmg.sh
```

Output:

```text
dist/Agentic-Trader-macOS.dmg
```

## Build Windows EXE

Run on Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows_exe.ps1
```

Output:

```text
dist/Agentic Trader.exe
```

## Launcher Install Location

- macOS: `~/Library/Application Support/AgenticTrader/app`
- Windows: `%LOCALAPPDATA%\AgenticTrader\app`
- Linux: `~/.agentic-trader/app`

Logs:

- Launcher log: `launcher.log` in the install directory.
- Web runtime log: `web.log` in the install directory.
- App runtime logs: `tmp/web.screen.log` and `tmp/cloudflared.screen.log` inside the cloned repo.

## Tunnel Behavior

The launcher defaults to local-only hosting.

To start the configured Cloudflare tunnel too:

```bash
"Agentic Trader" --tunnel
```

The admin Runtime tab can also start and stop the managed tunnel session.

## Requirements on Target Machines

- Git with SSH access to the private GitHub repo.
- Python 3.10+.
- `uv` recommended, but not required.
- `cloudflared` only required on machines that host the public tunnel.

The launcher intentionally does not copy `.env` or model artifacts. Keep those
machine-local or restore them with:

```bash
python cli/restore_runtime.py restore-data --artifact-tar agentic-trader-artifacts.tar.gz
```
