import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from scripts.telegram_sender import send_telegram_notification
import json
import uuid

import secrets
import re
import subprocess
hil_token = secrets.token_urlsafe(50)

print("\n[HIL] Activating secure Cloudflare Tunnel...")
subprocess.run("killall cloudflared 2>/dev/null || true", shell=True)
subprocess.run("bash scripts/start_public_tunnel.sh", shell=True)

tunnel_url = "http://localhost:8001"
try:
    log_content = Path("tmp/tunnel.log").read_text()
    match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", log_content)
    if match:
        tunnel_url = match.group(0)
except Exception:
    pass

approval_link = f"{tunnel_url}/api/approve?t={hil_token}"

ticker = "AMD"
shares = 10
price = 145.20

msg = (
    f"🚨 <b>Test Trade Proposal</b>\n"
    f"Action: BUY\n"
    f"Ticker: {ticker}\n"
    f"Shares: {shares}\n\n"
    f"🔐 <a href=\"{approval_link}\">Click to Securely Approve/Reject</a>"
)

print("Sending Telegram message...")
res = send_telegram_notification(msg)
print("Telegram result:", res)

hil_file = Path("tmp/hil_state.json")
hil_file.parent.mkdir(exist_ok=True)
hil_file.write_text(json.dumps({
    "id": str(uuid.uuid4()),
    "ticker": ticker,
    "shares": shares,
    "price": price,
    "status": "pending",
    "token": hil_token
}))
print(f"HIL State created with token {hil_token}!")
print("\nWaiting for you to click approve or reject...")
import time

while True:
    try:
        state = json.loads(hil_file.read_text())
        if state.get("status") != "pending":
            print(f"User selected: {state.get('status')}!")
            break
    except Exception:
        pass
    time.sleep(2)

subprocess.run("killall cloudflared 2>/dev/null || true", shell=True)
print("Tunnel shut down! System is fully secure again.")
