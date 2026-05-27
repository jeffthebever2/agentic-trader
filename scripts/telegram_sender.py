import os
import urllib.request
import urllib.parse
import ssl
from pathlib import Path

def load_env_defaults() -> None:
    ROOT = Path(__file__).resolve().parents[1]
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

def send_telegram_notification(message: str) -> bool:
    """Sends a notification to the user via Telegram bot API."""
    load_env_defaults()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id or "YOUR_" in token:
        print("[Telegram] Error: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured in .env")
        return False
        
    # Verified TLS — the bot token travels in the URL, so never disable cert
    # checks (that would let a MITM steal it). Use certifi's CA bundle on macOS
    # where the system store is often missing.
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    params = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    
    data = urllib.parse.urlencode(params).encode("utf-8")
    
    try:
        req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=ctx) as response:
            result = response.read().decode("utf-8")
            return '"ok":true' in result
    except Exception as e:
        print(f"[Telegram] Failed to send message: {e}")
        return False

if __name__ == "__main__":
    # Test send
    import sys
    msg = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Test message from TradingAgents!"
    if send_telegram_notification(msg):
        print("Success!")
    else:
        print("Failed.")
