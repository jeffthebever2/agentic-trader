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
import tarfile
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from datetime import datetime

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
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
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
    return subprocess.run(cmd, cwd=str(cwd or install_dir), text=True, check=check, capture_output=True)

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
        self.root.geometry("850x650")
        
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

        self.auto_update_var = tk.BooleanVar(value=False)
        self.tunnel_var = tk.BooleanVar(value=args.tunnel)
        self.port_var = tk.IntVar(value=args.port)

        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        main_frame = ctk.CTkFrame(self.root, fg_color="#2A2A2A")
        main_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        main_frame.grid_rowconfigure(0, weight=1)
        main_frame.grid_columnconfigure(0, weight=1)

        self.notebook = ctk.CTkTabview(main_frame)
        self.notebook.grid(row=0, column=0, sticky="nsew", padx=10, pady=5)

        self.build_dashboard_tab()
        self.build_diagnostics_tab()
        self.build_settings_tab()

        self.root.after(100, self.process_log_queue)
        
        threading.Thread(target=self.diagnostics_loop, daemon=True).start()
        threading.Thread(target=self.auto_updater_loop, daemon=True).start()
        
        log(self.install_dir, "Admin Panel initialized. Ready to start.")

    def build_dashboard_tab(self):
        self.notebook.add("Dashboard")
        tab = self.notebook.tab("Dashboard")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(3, weight=1)
        
        title = ctk.CTkLabel(tab, text="Server Control", font=ctk.CTkFont(family="SF Pro Text", size=24, weight="normal"))
        title.grid(row=0, column=0, sticky="w", pady=(5, 15), padx=10)
        
        # Status Card
        card = ctk.CTkFrame(tab, corner_radius=10, fg_color=("gray85", "gray16"))
        card.grid(row=1, column=0, sticky="ew", pady=(0, 20), padx=10)
        card.grid_columnconfigure(0, weight=1)
        
        card_inner = ctk.CTkFrame(card, fg_color="transparent")
        card_inner.grid(row=0, column=0, sticky="w", padx=15, pady=15)

        # Status Pill
        self.status_pill_frame = ctk.CTkFrame(card_inner, corner_radius=10, fg_color="#D32F2F")
        self.status_pill_frame.grid(row=0, column=0, sticky="w", pady=(0, 15))
        self.status_var = tk.StringVar(value="Stopped")
        self.status_lbl = ctk.CTkLabel(self.status_pill_frame, textvariable=self.status_var, font=ctk.CTkFont(size=14, weight="bold"), text_color="white")
        self.status_lbl.pack(padx=10, pady=2)
        
        # Metrics Grid
        metrics_frame = ctk.CTkFrame(card_inner, fg_color="transparent")
        metrics_frame.grid(row=1, column=0, sticky="w")
        
        self.uptime_var = tk.StringVar(value="⏱ Uptime: 00:00:00")
        ctk.CTkLabel(metrics_frame, textvariable=self.uptime_var, font=ctk.CTkFont(size=13)).grid(row=0, column=0, sticky="w", padx=(0, 20))
        
        self.cpu_var = tk.StringVar(value="⚙ CPU: 0.0%")
        ctk.CTkLabel(metrics_frame, textvariable=self.cpu_var, font=ctk.CTkFont(size=13)).grid(row=0, column=1, sticky="w", padx=(0, 20))

        self.ram_var = tk.StringVar(value="🖬 RAM: 0.0 MB")
        ctk.CTkLabel(metrics_frame, textvariable=self.ram_var, font=ctk.CTkFont(size=13)).grid(row=0, column=2, sticky="w")

        # Action Buttons Area
        actions_frame = ctk.CTkFrame(tab, fg_color="transparent")
        actions_frame.grid(row=2, column=0, sticky="ew", padx=10)
        
        # Primary Row
        primary_row = ctk.CTkFrame(actions_frame, fg_color="transparent")
        primary_row.pack(fill=tk.X, pady=(0, 10))
        
        self.start_btn = ctk.CTkButton(primary_row, text="▶ Start Server", height=32, command=self.on_start_clicked, fg_color="#F25C05", hover_color="#C04803")
        self.start_btn.pack(side=tk.LEFT, padx=(0, 16))

        self.stop_btn = ctk.CTkButton(primary_row, text="■ Stop Server", height=32, command=self.on_stop_clicked, state="disabled", fg_color="gray30")
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 16))
        
        self.restart_btn = ctk.CTkButton(primary_row, text="↻ Restart", height=32, command=self.on_restart_clicked, state="disabled", fg_color="gray30")
        self.restart_btn.pack(side=tk.LEFT, padx=(0, 16))
        
        # Spacer before emergency stop
        ctk.CTkFrame(primary_row, width=1, height=20, fg_color="gray40").pack(side=tk.LEFT, padx=(5, 20))
        
        self.kill_btn = ctk.CTkButton(primary_row, text="⚠ Emergency Stop", height=32, command=self.on_emergency_stop_clicked, fg_color="#D32F2F", hover_color="#9A0007")
        self.kill_btn.pack(side=tk.LEFT)

        # Secondary Row
        secondary_row = ctk.CTkFrame(actions_frame, fg_color="transparent")
        secondary_row.pack(fill=tk.X)
        
        self.dash_btn = ctk.CTkButton(secondary_row, text="🌐 Web Dashboard", height=30, command=self.on_dashboard_clicked, fg_color="transparent", border_width=1)
        self.dash_btn.pack(side=tk.LEFT, padx=(0, 16))
        
        self.test_conn_btn = ctk.CTkButton(secondary_row, text="📡 Test Connection", height=30, command=self.on_test_connection_clicked, fg_color="transparent", border_width=1)
        self.test_conn_btn.pack(side=tk.LEFT, padx=(0, 16))
        
        self.edit_cfg_btn = ctk.CTkButton(secondary_row, text="📝 Edit Config", height=30, command=self.on_edit_config_clicked, fg_color="transparent", border_width=1)
        self.edit_cfg_btn.pack(side=tk.LEFT, padx=(0, 16))
        
        self.export_logs_btn = ctk.CTkButton(secondary_row, text="📤 Export Logs", height=30, command=self.on_export_logs_clicked, fg_color="transparent", border_width=1)
        self.export_logs_btn.pack(side=tk.LEFT)

        # Mini Log Pane
        self.mini_log = ctk.CTkTextbox(tab, height=70, font=ctk.CTkFont(family="Courier", size=11), wrap="word", corner_radius=8, fg_color="gray12")
        self.mini_log.grid(row=3, column=0, sticky="sew", padx=10, pady=(20, 0))
        self.mini_log.configure(state="disabled")

    def build_diagnostics_tab(self):
        self.notebook.add("Diagnostics")
        tab = self.notebook.tab("Diagnostics")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        lbl = ctk.CTkLabel(tab, text="Live Output Log", font=ctk.CTkFont(size=16, weight="bold"))
        lbl.grid(row=0, column=0, sticky="w", pady=(5, 10))

        self.log_area = ctk.CTkTextbox(tab, font=ctk.CTkFont(family="Courier", size=12), wrap="word", corner_radius=10, fg_color="gray12")
        self.log_area.grid(row=1, column=0, sticky="nsew")
        self.log_area.configure(state="disabled")

    def build_settings_tab(self):
        self.notebook.add("Settings")
        tab = self.notebook.tab("Settings")
        
        # Grid layout for settings
        col1 = ctk.CTkFrame(tab, fg_color="transparent")
        col1.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10)
        
        col2 = ctk.CTkFrame(tab, fg_color="transparent")
        col2.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10)

        # Network
        ctk.CTkLabel(col1, text="Network & System", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", pady=(10, 10))
        
        port_frame = ctk.CTkFrame(col1, fg_color="transparent")
        port_frame.pack(fill=tk.X, pady=(0, 15))
        ctk.CTkLabel(port_frame, text="Local Port: ").pack(side=tk.LEFT, padx=(0, 10))
        ctk.CTkEntry(port_frame, textvariable=self.port_var, width=100).pack(side=tk.LEFT)
        
        ctk.CTkSwitch(col1, text="Cloudflare Tunnel (Public Access)", variable=self.tunnel_var, progress_color="#F25C05").pack(anchor="w", pady=(0, 15))
        ctk.CTkSwitch(col1, text="Automatic Background Updates", variable=self.auto_update_var, progress_color="#F25C05").pack(anchor="w", pady=(0, 15))

        # Maintenance
        ctk.CTkLabel(col2, text="Maintenance", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", pady=(10, 10))
        
        ctk.CTkButton(col2, text="🔄 Check for Updates", command=self.on_check_updates_clicked, fg_color="gray30", anchor="w").pack(fill=tk.X, pady=(0, 10))
        ctk.CTkButton(col2, text="📦 Backup State", command=self.on_backup_clicked, fg_color="gray30", anchor="w").pack(fill=tk.X, pady=(0, 10))
        ctk.CTkButton(col2, text="♻ Clear Cache", command=self.on_clear_cache_clicked, fg_color="gray30", anchor="w").pack(fill=tk.X, pady=(0, 10))
        ctk.CTkButton(col2, text="⬇ Restore Backup", command=self.on_restore_clicked, fg_color="gray30", anchor="w").pack(fill=tk.X, pady=(0, 10))

    def process_log_queue(self):
        try:
            while True:
                line = log_queue.get_nowait()
                # Main log area
                self.log_area.configure(state="normal")
                self.log_area.insert("end", line + "\\n")
                self.log_area.see("end")
                self.log_area.configure(state="disabled")
                # Mini log
                self.mini_log.configure(state="normal")
                self.mini_log.insert("end", line + "\\n")
                self.mini_log.see("end")
                self.mini_log.configure(state="disabled")
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

    def update_status_pill(self, status: str):
        self.status_var.set(status)
        if "Running" in status:
            self.status_pill_frame.configure(fg_color="#388E3C") # Green
        elif "Stopped" in status:
            self.status_pill_frame.configure(fg_color="#D32F2F") # Red
        else:
            self.status_pill_frame.configure(fg_color="#F25C05") # Orange

    def diagnostics_loop(self):
        while True:
            time.sleep(1)
            pid = self.get_server_pid()
            
            if not pid:
                if self.status_var.get() == "Running":
                    self.update_status_pill("Stopped")
                    self.stop_btn.configure(state="disabled", fg_color="gray30")
                    self.restart_btn.configure(state="disabled", fg_color="gray30")
                    self.start_btn.configure(state="normal")
                self.uptime_var.set("⏱ Uptime: 00:00:00")
                self.cpu_var.set("⚙ CPU: 0.0%")
                self.ram_var.set("🖬 RAM: 0.0 MB")
                continue

            if self.status_var.get() == "Stopped":
                self.update_status_pill("Running")
                self.stop_btn.configure(state="normal", fg_color="gray30", hover_color="gray40")
                self.restart_btn.configure(state="normal", fg_color="gray30", hover_color="gray40")
                self.start_btn.configure(state="disabled")
                if not self.start_time:
                    self.start_time = time.time()

            if self.start_time:
                uptime_sec = int(time.time() - self.start_time)
                m, s = divmod(uptime_sec, 60)
                h, m = divmod(m, 60)
                self.uptime_var.set(f"⏱ Uptime: {h:02d}:{m:02d}:{s:02d}")

            system = platform.system().lower()
            try:
                if system in ("darwin", "linux"):
                    output = subprocess.check_output(["ps", "-p", str(pid), "-o", "%cpu,rss"], text=True).strip().split('\\n')
                    if len(output) > 1:
                        cpu, rss = output[1].strip().split()
                        mem_mb = float(rss) / 1024
                        self.cpu_var.set(f"⚙ CPU: {cpu}%")
                        self.ram_var.set(f"🖬 RAM: {mem_mb:.1f} MB")
                elif system == "windows":
                    output = subprocess.check_output(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], text=True)
                    if output:
                        parts = output.strip().split('","')
                        if len(parts) >= 5:
                            mem_str = parts[4].replace(' K"', '').replace(',', '')
                            mem_mb = float(mem_str) / 1024
                            self.ram_var.set(f"🖬 RAM: {mem_mb:.1f} MB")
            except Exception:
                pass

    def auto_updater_loop(self):
        while True:
            time.sleep(300)
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
                        
                        if self.get_server_pid() and not self.is_restarting:
                            log(self.install_dir, "Auto-updater: restarting server to apply updates...")
                            self.on_restart_clicked()
            except Exception as e:
                log(self.install_dir, f"Auto-updater error: {e}")

    def on_start_clicked(self):
        self.start_btn.configure(state="disabled")
        self.update_status_pill("Starting...")
        
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
                        self.update_status_pill("Running")
                        log(self.install_dir, "Server is healthy and ready.")
                        self.stop_btn.configure(state="normal")
                        self.restart_btn.configure(state="normal")
                    else:
                        self.update_status_pill("Error")
                        log(self.install_dir, "Server health did not become ready within 30 seconds.")
                        self.stop_btn.configure(state="normal")
                self.root.after(0, update_ui_success)

            except Exception as exc:
                log(self.install_dir, f"ERROR: {exc}")
                def update_ui_error():
                    self.update_status_pill("Error")
                    self.start_btn.configure(state="normal")
                self.root.after(0, update_ui_error)

        threading.Thread(target=task, daemon=True).start()

    def on_stop_clicked(self):
        self.stop_btn.configure(state="disabled")
        self.restart_btn.configure(state="disabled")
        
        pid = self.get_server_pid()
        if pid:
            self.update_status_pill("Stopping...")
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
        self.update_status_pill("Stopped")
        self.start_btn.configure(state="normal")

    def on_restart_clicked(self):
        self.is_restarting = True
        self.on_stop_clicked()
        def restart_task():
            time.sleep(2)
            self.root.after(0, self.on_start_clicked)
            self.is_restarting = False
        threading.Thread(target=restart_task, daemon=True).start()

    def on_emergency_stop_clicked(self):
        if messagebox.askyesno("Emergency Stop", "Are you sure you want to HARD KILL all server processes? This will abruptly terminate connections.", icon="warning"):
            log(self.install_dir, "⚠ INITIATING EMERGENCY STOP")
            pid = self.get_server_pid()
            if pid:
                subprocess.run(["kill", "-9", str(pid)], check=False)
            subprocess.run(["pkill", "-9", "-f", "start.py"], check=False)
            subprocess.run(["pkill", "-9", "-f", "cloudflared"], check=False)
            self.server_process = None
            self.start_time = 0
            self.update_status_pill("Stopped")
            self.start_btn.configure(state="normal")
            log(self.install_dir, "Emergency stop complete.")

    def on_dashboard_clicked(self):
        url = f"http://127.0.0.1:{self.port_var.get()}/?v=desktop-launcher"
        webbrowser.open(url)
        log(self.install_dir, f"Opened web dashboard: {url}")

    def on_test_connection_clicked(self):
        def task():
            log(self.install_dir, "Testing connection to server...")
            if health_ok(self.port_var.get()):
                log(self.install_dir, "✅ Server is running and healthy!")
                self.test_conn_btn.configure(text="📡 Status: Healthy")
            else:
                log(self.install_dir, "❌ Server is not responding on port " + str(self.port_var.get()))
                self.test_conn_btn.configure(text="📡 Status: Offline")
            self.root.after(3000, lambda: self.test_conn_btn.configure(text="📡 Test Connection"))
        threading.Thread(target=task, daemon=True).start()

    def on_export_logs_clicked(self):
        downloads = Path.home() / "Downloads"
        zip_path = downloads / f"AgenticTrader_Logs_{int(time.time())}.tar.gz"
        log(self.install_dir, f"Exporting logs to {zip_path}...")
        try:
            with tarfile.open(zip_path, "w:gz") as tar:
                if (self.install_dir / "launcher.log").exists():
                    tar.add(self.install_dir / "launcher.log", arcname="launcher.log")
                if (self.repo_dir / "tmp" / "web.screen.log").exists():
                    tar.add(self.repo_dir / "tmp" / "web.screen.log", arcname="web.screen.log")
                if (self.repo_dir / "tmp" / "cloudflared.screen.log").exists():
                    tar.add(self.repo_dir / "tmp" / "cloudflared.screen.log", arcname="cloudflared.screen.log")
            log(self.install_dir, "✅ Logs exported successfully.")
        except Exception as e:
            log(self.install_dir, f"❌ Failed to export logs: {e}")

    def on_edit_config_clicked(self):
        env_path = self.repo_dir / ".env"
        if not env_path.exists():
            env_path.touch()
        
        modal = ctk.CTkToplevel(self.root)
        modal.title("Edit Config (.env)")
        modal.geometry("600x500")
        modal.transient(self.root)
        
        textbox = ctk.CTkTextbox(modal, font=ctk.CTkFont(family="Courier", size=13))
        textbox.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        textbox.insert("0.0", env_path.read_text())
        
        def save():
            env_path.write_text(textbox.get("0.0", "end").strip())
            log(self.install_dir, "Config saved.")
            modal.destroy()
            
        ctk.CTkButton(modal, text="Save Settings", command=save, fg_color="#F25C05").pack(pady=10)

    def on_clear_cache_clicked(self):
        if messagebox.askyesno("Clear Cache", "Wipe all temporary data (.backtest_cache, tmp)?"):
            log(self.install_dir, "Clearing cache...")
            for folder in [".backtest_cache", "tmp"]:
                p = self.repo_dir / folder
                if p.exists():
                    shutil.rmtree(p, ignore_errors=True)
            log(self.install_dir, "✅ Cache cleared.")

    def on_backup_clicked(self):
        downloads = Path.home() / "Downloads"
        zip_path = downloads / f"AgenticTrader_Backup_{int(time.time())}.tar.gz"
        log(self.install_dir, "Backing up state...")
        try:
            with tarfile.open(zip_path, "w:gz") as tar:
                for f in ["ml_models", "rl_models", ".env", "backtest_index.db"]:
                    p = self.repo_dir / f
                    if p.exists():
                        tar.add(p, arcname=f)
            log(self.install_dir, f"✅ Backup saved to {zip_path}")
        except Exception as e:
            log(self.install_dir, f"❌ Backup failed: {e}")

    def on_restore_clicked(self):
        log(self.install_dir, "Restore requires a command line invocation of cli/restore_runtime.py currently.")
        messagebox.showinfo("Restore", "To restore, please run `cli/restore_runtime.py restore-data --artifact-tar <path>` in the terminal.")

    def on_check_updates_clicked(self):
        log(self.install_dir, "Checking for updates manually...")
        try:
            subprocess.run(["git", "fetch", "origin"], cwd=str(self.repo_dir), check=True)
            local = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(self.repo_dir), text=True).strip()
            remote = subprocess.check_output(["git", "rev-parse", "@{u}"], cwd=str(self.repo_dir), text=True).strip()
            if local != remote:
                log(self.install_dir, "✨ Updates are available! Restart the server with auto-updates enabled to apply them.")
            else:
                log(self.install_dir, "✅ You are on the latest version.")
        except Exception as e:
            log(self.install_dir, f"❌ Failed to check updates: {e}")

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
