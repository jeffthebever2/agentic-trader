# PyTextNow API Integration

This guide explains how to set up SMS notifications using the PyTextNow API with TradingAgents.

## What is PyTextNow?

PyTextNow is a Python library that enables free SMS sending through TextNow's API. It replaces the browser automation approach with direct API calls, making it faster, more reliable, and easier to maintain.

**Key Benefits:**
- No browser automation needed (faster, more reliable)
- Direct API calls to TextNow servers
- Free SMS through TextNow accounts
- Supports sending and receiving SMS
- Event-based message handling

**Repository:** [leogomezz4t/PyTextNow_API](https://github.com/leogomezz4t/PyTextNow_API)

## Installation

PyTextNow is already listed in `pyproject.toml`. Install it with:

```bash
pip install PyTextNow
# or if you have the full project dependencies:
pip install -e .
```

## Setup Instructions

### Step 1: Get Your TextNow Credentials

1. Go to [https://www.textnow.com](https://www.textnow.com) and log in to your account
2. Note your **username** (visible in your profile)
3. Extract authentication cookies:
   - Open DevTools: **F12** (or Cmd+Option+I on Mac)
   - Go to **Application** tab
   - Navigate to **Cookies** → **textnow.com**
   - Copy the value of the **connect.sid** cookie
   - Copy the value of the **_csrf** cookie (or similar CSRF cookie)

### Step 2: Update Your `.env` File

Add the following configuration to your `.env`:

```env
# TextNow SMS alerts (PyTextNow API)
TEXTNOW_USERNAME=your_textnow_username
TEXTNOW_SID=your_connect_sid_value
TEXTNOW_CSRF_COOKIE=your_csrf_cookie_value
PAPER_SMS_NUMBER=+1XXXXXXXXXX  # Your phone number for alerts
SMS_PROVIDER=textnow
```

**Example:**
```env
TEXTNOW_USERNAME=myusername
TEXTNOW_SID=s%3Aylf0TwNftCYehi8etWWVW12VLM0oZPhI.rx%2B4aAXG7QuIH9AxipLyaUMDH0OD5eFnMwKX0qxnZzs
TEXTNOW_CSRF_COOKIE=abc123def456ghi789
PAPER_SMS_NUMBER=+16145078688
SMS_PROVIDER=textnow
```

## Usage

### Basic SMS Sending

```python
from scripts.textnow_sms import send_sms

# Send a simple SMS
success = send_sms("+15551234567", "Hello from TradingAgents!")
print("✓ Sent OK" if success else "✗ Failed")
```

### With TextNowSMS Class

```python
from scripts.textnow_sms import TextNowSMS

# Create a client
client = TextNowSMS(
    username="myusername",
    sid_cookie="my_sid_value",
    csrf_token="my_csrf_value"
)

# Send SMS
try:
    result = client.send("+15551234567", "AAPL bought @ $182.50")
    print("Message sent:", result)
except Exception as e:
    print("Failed:", e)
```

### Using Environment Variables

The module automatically loads credentials from `.env`:

```python
from scripts.textnow_sms import send_sms

# Automatically uses TEXTNOW_USERNAME, TEXTNOW_SID, TEXTNOW_CSRF_TOKEN from .env
send_sms("+15551234567", "Trade alert!")
```

### CLI Testing

Test the SMS functionality from the command line:

```bash
# Test sending an SMS
python scripts/textnow_sms.py +15551234567 "Test message"

# Expected output on success:
# Sending to +15551234567: 'Test message'
# ✓ Sent OK
```

## Paper Trading Integration

Paper trading automatically sends SMS alerts when enabled:

```python
from web.api.paper import paper_router

# Alerts are sent to PAPER_SMS_NUMBER via SMS_PROVIDER
# (configured in .env)
```

To enable SMS alerts in paper trading:

1. Set `SMS_PROVIDER=textnow` in `.env`
2. Set `PAPER_SMS_NUMBER` to your phone number
3. Provide complete TextNow credentials (username, SID, CSRF token)
4. When trades occur, alerts will be sent automatically

## Credential Refresh

If your TextNow credentials expire or stop working:

1. Log out and back in at [https://www.textnow.com](https://www.textnow.com)
2. Extract new cookies:
   - **F12** → **Application** → **Cookies** → **textnow.com**
   - Get fresh values for `connect.sid` and CSRF cookie
3. Update `.env` with new values:
   ```env
   TEXTNOW_SID=new_sid_value
   TEXTNOW_CSRF_COOKIE=new_csrf_cookie_value
   ```

## Troubleshooting

### "PyTextNow not installed"
```bash
pip install PyTextNow
```

### "TextNow username not set"
Make sure `TEXTNOW_USERNAME` is defined in `.env` or passed to `TextNowSMS()`

### "TextNow SID cookie not set"
Make sure `TEXTNOW_SID` is defined in `.env` or passed to `TextNowSMS()`

### "TextNow CSRF cookie not set"
Make sure `TEXTNOW_CSRF_COOKIE` is defined in `.env` or passed to `TextNowSMS()`

### SMS Send Fails with Request Error
- Verify credentials are correct and not expired
- Re-extract cookies from a fresh TextNow login
- Check that your TextNow account is active and has SMS credits
- Ensure the destination phone number is in correct format (E.164 or 10-digit US)

### Rate Limiting
TextNow may rate-limit SMS sending if you send too many messages quickly. Consider adding delays between sends:

```python
import time
from scripts.textnow_sms import send_sms

phone = "+15551234567"
for message in messages:
    send_sms(phone, message)
    time.sleep(1)  # 1 second between sends
```

## Advanced Usage

### Receiving Messages (PyTextNow Feature)

The PyTextNow library also supports receiving messages:

```python
import pytextnow as pytn

client = pytn.Client(
    username="myusername",
    sid_cookie="my_sid",
    csrf_cookie="my_csrf"
)

# Get unread messages
unread = client.get_unread_messages()
for msg in unread:
    print(f"From {msg.number}: {msg.content}")
    msg.mark_as_read()
```

### Event-Based Message Handler

```python
import pytextnow as pytn

client = pytn.Client(username="...", sid_cookie="...", csrf_token="...")

@client.on("message")
def handle_message(msg):
    print(f"New message from {msg.number}: {msg.content}")
    if msg.content == "ping":
        msg.send_sms("pong")

# Start listening (this is blocking)
# client.run()  # Enable this to listen for messages
```

## Integration with TradingAgents

The SMS system is integrated at two levels:

1. **Paper Trading Alerts** (`web/api/paper.py`):
   - Automatically sends SMS when paper trades occur
   - Uses `PAPER_SMS_NUMBER` and configured `SMS_PROVIDER`

2. **Manual Alerts** (`scripts/sms_alerts.py`):
   - Use `send_sms()` function to send alerts from any code
   - Supports multiple SMS providers (textnow, textbelt)

## Alternatives

If you prefer not to use TextNow, you can use other SMS providers:

- **Textbelt** (default): Free SMS service (limited free tier)
  ```env
  SMS_PROVIDER=textbelt
  TEXTBELT_KEY=textbelt  # Free tier key
  ```

- **Twilio**: Premium SMS service (requires paid account)
- **AWS SNS**: Enterprise SMS platform
- **Custom SMS Gateway**: Add your own implementation in `scripts/sms_alerts.py`

## References

- [PyTextNow GitHub Repository](https://github.com/leogomezz4t/PyTextNow_API)
- [TextNow Website](https://www.textnow.com)
- [SMS Provider Comparison](https://www.textnow.com/help)

## License

PyTextNow is provided under the MIT License. TradingAgents SMS integration follows the same license.
