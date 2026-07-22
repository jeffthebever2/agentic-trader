# Server migration — systemd deployment

Units for moving off the decommissioned Mac (launchd) onto a Linux server.
Paths assume `/opt/agentictrader` + a `trader` user + venv at
`/opt/agentictrader/.venv` — adjust all three in the unit files if different.

## Why exitguard is its own unit (P0)

On the Mac, the live-book stop/target watch (`_exit_guard_loop`) ran INSIDE the
webserver process. Webserver disabled/crashed/deploying = nobody watching real
holdings. `agentictrader-exitguard.service` runs `scripts/run_exit_guard.py`
independently. When it's up, it holds `tmp/exit_guard.lock` and the webserver's
internal loop automatically stands down; if it dies, systemd restarts it, and
until then the webserver loop resumes as fallback. Never make this unit depend
on the web unit.

## Install checklist

```bash
# 1. Code + venv
sudo mkdir -p /opt/agentictrader /opt/agentictrader/logs
sudo chown -R trader:trader /opt/agentictrader
git clone <repo> /opt/agentictrader
cd /opt/agentictrader
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Playwright (Fidelity browser automation)
.venv/bin/playwright install chromium
sudo .venv/bin/playwright install-deps chromium

# 3. Fresh .env — do NOT copy the Mac's .fidelity_session_*.json files.
#    Write a new .env (see gates below), then log in to Fidelity once through
#    the dashboard to seed a fresh session + encrypted creds (trust device).

# 4. Units
sudo cp deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agentictrader-web agentictrader-exitguard \
                            agentictrader-paperportfolios agentictrader-tunnel
```

## .env gates that matter on the server

| Var | Server value | Why |
|---|---|---|
| `HOLDINGS_BRAIN_ENABLED` | `true` | gates brain cycle AND both exit guards |
| `THEMATIC_AUTO_SCAN` | `true` | 4h scan loop AND the outcome-learning loop live behind this |
| `THEMATIC_EXIT_LOOP` | `true` | fast paper-book stop enforcement |
| `LIVE_TRADING_ENABLED` | as intended | master live-order switch, read fresh per call |
| `FIDELITY_PROTECTED_ACCOUNTS` | `262502469` | Roth kill-switch — carry over |
| `FIDELITY_REQUIRE_EXPLICIT_ACCOUNT` | unset (defaults `true`) | orders must name an allowed account |
| `WEB_SINGLE_INSTANCE_LOCK` | unset (defaults `true`) | web refuses to run twice |
| `PAPER_SMS_NUMBER` etc. | carry over | Sendblue trade-request texts |

## Verify after cutover

```bash
systemctl status agentictrader-\*
curl -s localhost:8001/api/health/deep | python3 -m json.tool
cat /opt/agentictrader/tmp/exit_guard_heartbeat.json   # ts fresh, status ok/market_closed
tail -f /opt/agentictrader/logs/exitguard.log
```

Also confirm in `logs/webserver.log`: `standalone exit-guard runner active —
in-server loop standing down`.
