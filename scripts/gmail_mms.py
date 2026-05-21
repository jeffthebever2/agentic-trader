import os
import smtplib
import imaplib
import email
from email.message import EmailMessage
import time
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

def send_gmail_mms(to: str, subject: str, message: str) -> dict:
    load_env_defaults()
    gmail_user = os.getenv("GMAIL_ADDRESS")
    gmail_pass = os.getenv("GMAIL_APP_PASSWORD")
    
    if not gmail_user or not gmail_pass:
        return {"success": False, "error": "Missing GMAIL_ADDRESS or GMAIL_APP_PASSWORD in .env"}
        
    msg = EmailMessage()
    msg.set_content(message)
    msg['Subject'] = subject
    msg['From'] = gmail_user
    msg['To'] = to
    
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(gmail_user, gmail_pass)
            smtp.send_message(msg)
        return {"success": True}
    except Exception as e:
        import logging; logging.error(f"Gmail MMS error: {e}")
        return {"success": False, "error": "An internal error occurred."}

def wait_for_gmail_reply(from_email: str, subject_match: str, timeout_sec: int = 300) -> str:
    """Polls inbox for a reply from the specified email containing the subject_match."""
    load_env_defaults()
    gmail_user = os.getenv("GMAIL_ADDRESS")
    gmail_pass = os.getenv("GMAIL_APP_PASSWORD")
    
    if not gmail_user or not gmail_pass:
        print("[HIL] Missing GMAIL credentials, falling back to terminal input.")
        return ""
        
    start_time = time.time()
    print(f"[HIL] Waiting for SMS reply from {from_email} (timeout in {timeout_sec}s)...")
    
    while time.time() - start_time < timeout_sec:
        try:
            with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
                imap.login(gmail_user, gmail_pass)
                imap.select("INBOX")
                
                # Search for unread emails from the specific sender
                _, messages = imap.search(None, f'(UNSEEN FROM "{from_email}")')
                
                for num in messages[0].split():
                    _, data = imap.fetch(num, '(RFC822)')
                    msg = email.message_from_bytes(data[0][1])
                    
                    # Mark as read
                    imap.store(num, '+FLAGS', '\\Seen')
                    
                    subj = msg.get("Subject", "")
                    # Extract text body
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                body = part.get_payload(decode=True).decode(errors="ignore")
                                break
                    else:
                        body = msg.get_payload(decode=True).decode(errors="ignore")
                    
                    # If this looks like a reply to our prompt
                    body_clean = body.strip().lower()
                    if body_clean:
                        return body_clean
        except Exception:
            pass
            
        time.sleep(10)
        
    return ""
