"""Desktop bootstrapper for Agentic Trader.

This launcher is an Admin Panel to control the backend server, 
diagnostics, and auto-updates independently of the web dashboard.
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
import webbrowser
import threading
import queue
import tkinter as tk
import customtkinter as ctk
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

REPO_URL = "git@github.com:jeffthebever2/agentic-trader.git"
APP_DIR_NAME = "AgenticTrader"
DEFAULT_PORT = 8001

log_queue = queue.Queue()

def setup_environment() -> None:
    paths = os.environ.get("PATH", "").split(os.pathsep)
    extra_paths = [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        str(Path.home() / ".cargo/bin"),
        str(Path.home() / ".local/bin"),
    ]
    for p in extra_paths:
        if p not in paths:
            paths.append(p)
    os.environ["PATH"] = os.pathsep.join(paths)

def show_error(message: str) -> None:
    system = platform.system().lower()
    try:
        if system == "darwin":
            subprocess.run(["osascript", "-e", f'display dialog "{message}" with title "Agentic Trader Admin Panel" buttons {{"OK"}} default button "OK" icon stop'], check=False)
        elif system == "windows":
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, "Agentic Trader Admin Panel", 0x10)
    except Exception:
        pass

def default_install_dir() -> Path:
    system = platform.system().lower()
    if system == "windows":
        base = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData/Local")
        return base / APP_DIR_NAME
    if system == "darwin":
        return Path.home() / "Library/Application Support" / APP_DIR_NAME
    return Path.home() / ".agentic-trader"

def log_path(install_dir: Path) -> Path:
    return install_dir / "launcher.log"

def log(install_dir: Path, message: str) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    line = f"[admin-panel] {message}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with log_path(install_dir).open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass
    log_queue.put(line)

def run_cmd(install_dir: Path, cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    log(install_dir, "$ " + " ".join(cmd))
    return subprocess.run(cmd, cwd=str(cwd or install_dir), text=True, check=check)

def command_exists(command: str) -> bool:
    return shutil.which(command) is not None

def python_cmd() -> list[str]:
    if command_exists("python3"):
        return ["python3"]
    if command_exists("python"):
        return ["python"]
    raise RuntimeError("Python was not found. Install Python 3.10+ and run this launcher again.")

def ensure_repo(install_dir: Path, repo_url: str) -> Path:
    repo_dir = install_dir / "app"
    if not repo_dir.exists():
        if not command_exists("git"):
            raise RuntimeError("Git was not found. Install Git and run this launcher again.")
        install_dir.mkdir(parents=True, exist_ok=True)
        run_cmd(install_dir, ["git", "clone", repo_url, str(repo_dir)], cwd=install_dir)
        return repo_dir

    if (repo_dir / ".git").exists():
        run_cmd(install_dir, ["git", "fetch", "--prune", "origin"], cwd=repo_dir)
        run_cmd(install_dir, ["git", "pull", "--ff-only"], cwd=repo_dir)
    return repo_dir

def sync_dependencies(install_dir: Path, repo_dir: Path) -> None:
    if command_exists("uv"):
        run_cmd(install_dir, ["uv", "sync", "--extra", "web", "--extra", "dev"], cwd=repo_dir)
        return
    py = python_cmd()
    run_cmd(install_dir, py + ["-m", "pip", "install", "-e", ".[web,dev]"], cwd=repo_dir)

def health_ok(port: int) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
            return response.status == 200
    except (URLError, TimeoutError, OSError):
        return False

class LauncherApp:
    def __init__(self, root: ctk.CTk, args: argparse.Namespace):
        self.root = root
        self.args = args
        self.install_dir = Path(args.install_dir).expanduser().resolve()
        self.repo_dir = self.install_dir / "app"
        
        self.server_process = None
        self.log_file_handle = None
        self.start_time = 0
        self.is_restarting = False

        self.root.title("Agentic Trader Admin Panel")
        self.root.geometry("800x600")
        
        if hasattr(sys, '_MEIPASS'):
            base_path = Path(sys._MEIPASS)
        else:
            base_path = Path(__file__).parent.parent
            
        icon_path = base_path / "assets" / "TauricResearch.png"
        if icon_path.exists():
            try:
                tk_img = tk.PhotoImage(file=str(icon_path))
                self.root.iconphoto(True, tk_img)
            except Exception:
                pass

        # Variables for settings
        self.auto_update_var = tk.BooleanVar(value=False)
        self.tunnel_var = tk.BooleanVar(value=args.tunnel)
        self.port_var = tk.IntVar(value=args.port)

        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        main_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        main_frame.grid(row=0, column=0, sticky="nsew", padx=20, pady=20)
        main_frame.grid_rowconfigure(0, weight=1)
        main_frame.grid_columnconfigure(0, weight=1)

        self.notebook = ctk.CTkTabview(main_frame)
        self.notebook.grid(row=0, column=0, sticky="nsew")

        self.build_dashboard_tab()
        self.build_diagnostics_tab()
        self.build_settings_tab()

        self.root.after(100, self.process_log_queue)
        
        # Background threads
        threading.Thread(target=self.diagnostics_loop, daemon=True).start()
        threading.Thread(target=self.auto_updater_loop, daemon=True).start()
        
        log(self.install_dir, "Admin Panel initialized. Ready to start.")

    def build_dashboard_tab(self):
        self.notebook.add("Dashboard")
        tab = self.notebook.tab("Dashboard")
        tab.grid_columnconfigure(0, weight=1)
        
        title = ctk.CTkLabel(tab, text="Server Control", font=ctk.CTkFont(size=24, weight="bold"))
        title.grid(row=0, column=0, sticky="w", pady=(10, 20))
        
        # Status Card
        card = ctk.CTkFrame(tab, corner_radius=15, fg_color=("gray85", "gray16"))
        card.grid(row=1, column=0, sticky="ew", pady=(0, 20))
        card.grid_columnconfigure(0, weight=1)
        
        card_inner = ctk.CTkFrame(card, fg_color="transparent")
        card_inner.grid(row=0, column=0, sticky="w", padx=20, pady=20)

        self.status_var = tk.StringVar(value="Stopped")
        status_lbl = ctk.CTkLabel(card_inner, textvariable=self.status_var, font=ctk.CTkFont(size=18, weight="bold"), text_color="#F25C05")
        status_lbl.grid(row=0, column=0, sticky="w", pady=(0, 10))
        
        self.uptime_var = tk.StringVar(value="Uptime: 00:00:00")
        uptime_lbl = ctk.CTkLabel(card_inner, textvariable=self.uptime_var, font=ctk.CTkFont(size=14))
        uptime_lbl.grid(row=1, column=0, sticky="w", pady=(0, 5))
        
        self.resource_var = tk.StringVar(value="CPU: 0.0% | RAM: 0.0 MB")
        resource_lbl = ctk.CTkLabel(card_inner, textvariable=self.resource_var, font=ctk.CTkFont(size=14))
        resource_lbl.grid(row=2, column=0, sticky="w")

        # Control Buttons
        btn_frame = ctk.CTkFrame(tab, fg_color="transparent")
        btn_frame.grid(row=2, column=0, sticky="ew")

        self.start_btn = ctk.CTkButton(btn_frame, text="Start Server", command=self.on_start_clicked, fg_color="#F25C05", hover_color="#C04803")
        self.start_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.stop_btn = ctk.CTkButton(btn_frame, text="Stop Server", command=self.on_stop_clicked, state="disabled", fg_color="gray30")
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.restart_btn = ctk.CTkButton(btn_frame, text="Restart Server", command=self.on_restart_clicked, state="disabled", fg_color="gray30")
        self.restart_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.dash_btn = ctk.CTkButton(btn_frame, text="Open Web Dashboard", command=self.on_dashboard_clicked)
        self.dash_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.update_btn = ctk.CTkButton(btn_frame, text="Manual Update", command=self.on_update_clicked, fg_color="transparent", border_width=2, text_color=("gray10", "gray90"))
        self.update_btn.pack(side=tk.RIGHT)

    def build_diagnostics_tab(self):
        self.notebook.add("Diagnostics")
        tab = self.notebook.tab("Diagnostics")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        lbl = ctk.CTkLabel(tab, text="Live Output Log", font=ctk.CTkFont(size=16, weight="bold"))
        lbl.grid(row=0, column=0, sticky="w", pady=(10, 10))

        self.log_area = ctk.CTkTextbox(tab, font=ctk.CTkFont(family="Courier", size=12), wrap="word", corner_radius=10)
        self.log_area.grid(row=1, column=0, sticky="nsew")
        self.log_area.configure(state="disabled")

    def build_settings_tab(self):
        self.notebook.add("Settings")
        tab = self.notebook.tab("Settings")
        
        ctk.CTkLabel(tab, text="Network", font=ctk.CTkFont(size=18, weight="bold")).grid(row=0, column=0, sticky="w", pady=(10, 10))
        
        port_frame = ctk.CTkFrame(tab, fg_color="transparent")
        port_frame.grid(row=1, column=0, sticky="w", pady=(0, 10))
        ctk.CTkLabel(port_frame, text="Local Port: ").pack(side=tk.LEFT, padx=(0, 10))
        ctk.CTkEntry(port_frame, textvariable=self.port_var, width=100).pack(side=tk.LEFT)
        
        ctk.CTkSwitch(tab, text="Enable Cloudflare Tunnel (Public Access)", variable=self.tunnel_var, progress_color="#F25C05").grid(row=2, column=0, sticky="w", pady=(0, 20))

        ctk.CTkLabel(tab, text="System", font=ctk.CTkFont(size=18, weight="bold")).grid(row=3, column=0, sticky="w", pady=(10, 10))
        ctk.CTkSwitch(tab, text="Enable Automatic Background Updates (checks every 5 mins)", variable=self.auto_update_var, progress_color="#F25C05").grid(row=4, column=0, sticky="w")

    def process_log_queue(self):
        try:
            while True:
                line = log_queue.get_nowait()
                self.log_area.configure(state="normal")
                self.log_area.insert("end", line + "\\n")
                self.log_area.see("end")
                self.log_area.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self.process_log_queue)

    def get_server_pid(self):
        if self.server_process and self.server_process.poll() is None:
            return self.server_process.pid
        try:
            output = subprocess.check_output(["lsof", "-tiTCP:" + str(self.port_var.get()), "-sTCP:LISTEN"], text=True)
            pids = output.strip().split()
            if pids: return int(pids[0])
        except Exception:
            pass
        return None

    def diagnostics_loop(self):
        """Thread that updates Uptime, CPU, and Memory usage every second."""
        while True:
            time.sleep(1)
            pid = self.get_server_pid()
            
            if not pid:
                if self.status_var.get() == "Running":
                    self.status_var.set("Stopped")
                    self.stop_btn.configure(state="disabled", fg_color="gray30")
                    self.restart_btn.configure(state="disabled", fg_color="gray30")
                    self.start_btn.configure(state="normal")
                self.uptime_var.set("Uptime: 00:00:00")
                self.resource_var.set("CPU: 0.0% | RAM: 0.0 MB")
                continue

            if self.status_var.get() == "Stopped":
                self.status_var.set("Running")
                self.stop_btn.configure(state="normal", fg_color="#E74C3C", hover_color="#C0392B")
                self.restart_btn.configure(state="normal", fg_color="#3498DB", hover_color="#2980B9")
                self.start_btn.configure(state="disabled")
                if not self.start_time:
                    self.start_time = time.time()

            # Calculate Uptime
            if self.start_time:
                uptime_sec = int(time.time() - self.start_time)
                m, s = divmod(uptime_sec, 60)
                h, m = divmod(m, 60)
                self.uptime_var.set(f"Uptime: {h:02d}:{m:02d}:{s:02d}")

            # Calculate Resource Usage
            system = platform.system().lower()
            try:
                if system in ("darwin", "linux"):
                    output = subprocess.check_output(["ps", "-p", str(pid), "-o", "%cpu,rss"], text=True).strip().split('\\n')
                    if len(output) > 1:
                        cpu, rss = output[1].strip().split()
                        mem_mb = float(rss) / 1024
                        self.resource_var.set(f"CPU: {cpu}% | RAM: {mem_mb:.1f} MB")
                elif system == "windows":
                    output = subprocess.check_output(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], text=True)
                    if output:
                        parts = output.strip().split('","')
                        if len(parts) >= 5:
                            mem_str = parts[4].replace(' K"', '').replace(',', '')
                            mem_mb = float(mem_str) / 1024
                            self.resource_var.set(f"RAM: {mem_mb:.1f} MB")
            except Exception:
                pass

    def auto_updater_loop(self):
        """Thread that routinely checks git for updates."""
        while True:
            time.sleep(300) # Check every 5 minutes
            if not self.auto_update_var.get() or not self.repo_dir.exists():
                continue
                
            log(self.install_dir, "Auto-updater: checking for updates...")
            try:
                subprocess.run(["git", "fetch", "origin"], cwd=str(self.repo_dir), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                local = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(self.repo_dir), text=True).strip()
                remote = subprocess.check_output(["git", "rev-parse", "@{u}"], cwd=str(self.repo_dir), text=True).strip()
                
                if local != remote:
                    base = subprocess.check_output(["git", "merge-base", "@", "@{u}"], cwd=str(self.repo_dir), text=True).strip()
                    if local == base:
                        log(self.install_dir, "Auto-updater: new updates found! Applying...")
                        subprocess.run(["git", "pull", "--ff-only"], cwd=str(self.repo_dir), check=True)
                        sync_dependencies(self.install_dir, self.repo_dir)
                        log(self.install_dir, "Auto-updater: updates successfully applied.")
                        
                        if self.server_process and not self.is_restarting:
                            log(self.install_dir, "Auto-updater: restarting server to apply updates...")
                            self.on_restart_clicked()
            except Exception as e:
                log(self.install_dir, f"Auto-updater error: {e}")

    def on_start_clicked(self):
        self.start_btn.configure(state="disabled")
        self.update_btn.configure(state="disabled")
        self.status_var.set("Starting...")
        
        def task():
            try:
                if not self.repo_dir.exists():
                    log(self.install_dir, "Repository not found. Updating first...")
                    ensure_repo(self.install_dir, self.args.repo)
                sync_dependencies(self.install_dir, self.repo_dir)
                
                py = python_cmd()
                launcher_log = self.install_dir / "web.log"
                cmd = py + ["cli/restore_runtime.py", "start", "--restart", "--port", str(self.port_var.get())]
                if not self.tunnel_var.get():
                    cmd.append("--no-tunnel")
                
                self.log_file_handle = launcher_log.open("a", encoding="utf-8")
                self.server_process = subprocess.Popen(cmd, cwd=str(self.repo_dir), stdout=self.log_file_handle, stderr=self.log_file_handle)
                self.start_time = time.time()
                log(self.install_dir, f"started background runtime (PID: {self.server_process.pid})")

                ready = False
                for _ in range(30):
                    if health_ok(self.port_var.get()):
                        ready = True
                        break
                    time.sleep(1)
                
                def update_ui_success():
                    if ready:
                        self.status_var.set("Running")
                        log(self.install_dir, "Server is healthy and ready.")
                        self.stop_btn.configure(state="normal", fg_color="#E74C3C", hover_color="#C0392B")
                        self.restart_btn.configure(state="normal", fg_color="#3498DB", hover_color="#2980B9")
                    else:
                        self.status_var.set("Error: Server Not Ready")
                        log(self.install_dir, "Server health did not become ready within 30 seconds.")
                        self.stop_btn.configure(state="normal", fg_color="#E74C3C", hover_color="#C0392B") # allow killing
                self.root.after(0, update_ui_success)

            except Exception as exc:
                log(self.install_dir, f"ERROR: {exc}")
                def update_ui_error():
                    self.status_var.set("Error starting server")
                    self.start_btn.configure(state="normal")
                    self.update_btn.configure(state="normal")
                self.root.after(0, update_ui_error)

        threading.Thread(target=task, daemon=True).start()

    def on_stop_clicked(self):
        self.stop_btn.configure(state="disabled", fg_color="gray30")
        self.restart_btn.configure(state="disabled", fg_color="gray30")
        
        pid = self.get_server_pid()
        if pid:
            self.status_var.set("Stopping...")
            log(self.install_dir, "Stopping server...")
            if self.server_process:
                self.server_process.terminate()
                try:
                    self.server_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.server_process.kill()
                self.server_process = None
            else:
                try:
                    subprocess.run(["kill", str(pid)], check=False)
                except Exception:
                    pass
            
        if self.log_file_handle:
            self.log_file_handle.close()
            self.log_file_handle = None
            
        self.start_time = 0
        self.status_var.set("Stopped")
        self.start_btn.configure(state="normal")
        self.update_btn.configure(state="normal")

    def on_restart_clicked(self):
        self.is_restarting = True
        self.on_stop_clicked()
        def restart_task():
            time.sleep(1) # Ensure port is freed
            self.root.after(0, self.on_start_clicked)
            self.is_restarting = False
        threading.Thread(target=restart_task, daemon=True).start()

    def on_update_clicked(self):
        self.start_btn.configure(state="disabled")
        self.update_btn.configure(state="disabled")
        self.status_var.set("Updating...")
        
        def task():
            try:
                repo_dir = ensure_repo(self.install_dir, self.args.repo)
                sync_dependencies(self.install_dir, repo_dir)
                log(self.install_dir, "Manual update completed successfully.")
                self.root.after(0, lambda: self.status_var.set("Stopped" if not self.server_process else "Running"))
            except Exception as exc:
                log(self.install_dir, f"Update ERROR: {exc}")
                self.root.after(0, lambda: self.status_var.set("Update Failed"))
            finally:
                self.root.after(0, lambda: self.start_btn.configure(state="normal") if not self.server_process else None)
                self.root.after(0, lambda: self.update_btn.configure(state="normal"))

        threading.Thread(target=task, daemon=True).start()

    def on_dashboard_clicked(self):
        url = f"http://127.0.0.1:{self.port_var.get()}/?v=desktop-launcher"
        webbrowser.open(url)
        log(self.install_dir, f"Opened web dashboard: {url}")

def main() -> int:
    parser = argparse.ArgumentParser(description="Install, update, and launch Agentic Trader.")
    parser.add_argument("--repo", default=REPO_URL)
    parser.add_argument("--install-dir", default=str(default_install_dir()))
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--tunnel", action="store_true", help="Also start the configured Cloudflare tunnel.")
    args = parser.parse_args()

    setup_environment()
    
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    root = ctk.CTk()
    app = LauncherApp(root, args)
    root.protocol("WM_DELETE_WINDOW", lambda: (app.on_stop_clicked(), root.destroy()))
    root.mainloop()
    return 0

if __name__ == "__main__":
    sys.exit(main())
