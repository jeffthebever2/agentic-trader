# Security Policy & Audit Record

Last reviewed: 2026-05-23. Tools: **CodeQL** (2.25.5, `python-security-and-quality.qls` + `javascript-security-and-quality.qls`), **semgrep** (1.163, `auto` + `p/xss` + `p/javascript` + `p/owasp-top-ten`), **bandit** (1.9.4), plus a custom exhaustive HTML-sink dataflow analyzer over the inline SPA JS.

## CodeQL results (binary-precision dataflow)

Initial scan surfaced 5 Python security findings — all fixed and re-verified:

| Finding | Sev | Location | Fix |
|---------|-----|----------|-----|
| `py/command-line-injection` | **9.8** | `web/api/admin.py` web-restart endpoint | Constant shell string + `exec "$@"` argv passthrough; `_safe_port()` bounds-check. User input can no longer be interpreted as shell code. |
| `py/stack-trace-exposure` ×4 | 5.4 | `web/api/admin.py` diagnostic endpoints | Exceptions logged server-side; responses return generic messages (no internal detail leaked). |

**Re-scan after fixes: 0 Python security findings, 0 JavaScript/XSS security findings.** (Remaining CodeQL results are code-quality/style, not security.) Databases: `codeql database create … --language=python/javascript`; the JS DB extracts the inline SPA `<script>` blocks, so the XSS surface is covered by binary-precision taint tracking — it reports **0** XSS sinks.

## Threat model

- **Attack surface = the deployed web app** (`web/`, served by `run_web.py`). Authenticated via Cloudflare Access + per-route FastAPI deps. This is the only remotely reachable code and is held to a strict standard.
- **`tradingagents/`** runs in-process behind the web app; deserialization there loads only fixed, locally-produced artifacts.
- **`scripts/`** are **offline, single-operator developer/research tools** run manually on the operator's own machine against data they produced. They are **not deployed, not network-reachable, and accept no untrusted input.** They are out of the remote threat model.

## XSS — eliminated on the web surface

The SPA is a single inline HTML/JS bundle that builds DOM via `innerHTML`. Every dynamic HTML sink was enumerated with a custom analyzer and audited:

- **`renderMd()` now sanitizes** all markdown→HTML through DOMPurify before `innerHTML` (LLM/report output is prompt-injectable). Verified to strip `<script>`, `onerror=`, and `javascript:` URLs.
- Escaped (`escHtml`) every data-bearing interpolation: history table (ticker/date/snippet) + sanitized its element ids and onclick args (`safeSym`/`safeId`); portfolio positions; screener results; Fidelity positions; `decisionBadge` fallback; admin status fields (`_adminStat` escapes label/sub centrally, callers escape data values).
- **Residual `innerHTML` interpolations that are NOT escaped are provably non-data**: hardcoded report-tab titles, constant KPI labels (`'Value'/'P/L'/…`), numeric HTTP `Response.status`, and CSS-token ternaries. Confirmed by source inspection.
- **Defense-in-depth CSP** (`web/app.py`) restricts `script-src`/`connect-src`/`frame-src` to known origins, sets `object-src 'none'`, `base-uri 'self'`, `frame-ancestors 'none'`. Verified the page loads with **zero CSP violations**.

Result: **0 data-driven XSS sinks**; `p/xss` + `p/javascript` rulesets report 0 findings.

## Other web-surface fixes

| Issue | Fix |
|-------|-----|
| Global SSL verification disabled (`paper.py`) — process-wide MITM | certifi-verified context; verification re-enabled |
| Bot token sent over unverified TLS (`telegram_sender.py`) | certifi-verified context |
| Wildcard CORS (`allow_origins=["*"]`) | Explicit allow-list, `ALLOWED_ORIGINS`-overridable |
| No security headers | CSP, X-Frame-Options DENY, nosniff, Referrer-Policy, COOP, Permissions-Policy |
| `torch.load` unpickling (RL agent) | `weights_only=True` (blocks pickle RCE) |
| `requests.get` w/o timeout (Alpha Vantage) | `timeout=30` |
| `urllib.urlopen` scheme (training fetch) | http(s)-only allowlist (blocks `file://` SSRF) |
| Weak MD5 (cache keys) | `usedforsecurity=False` (intent: non-cryptographic) |

## Verified false positives

- **`web/d1_store.py` B608 (×3), `checkpointer.py` B608, `market_analyst.py` B608** — table/column names are module-level **literal constants**; all VALUES use `?` parameter binding. No user input is concatenated. SQL injection is not possible.
- **`agents/__init__.py` non-literal-import** — `import_module` argument is selected from a hardcoded whitelist dict; non-members raise `AttributeError`.
- **`web/api/ml.py` `joblib.load`** — fixed server-side path (`MODEL_DIR/model_bundle.joblib`); no endpoint accepts an uploaded or caller-specified path. Not a remote deserialization vector.

## Accepted residual risks (offline `scripts/` only)

These run **manually, locally, single-user, on operator-produced data**, with no network exposure:

- **`pickle`/`pd.read_pickle`/`joblib` loads (B301/pickles-in-pandas)** — load the operator's own cached research/model artifacts. Exploiting requires write access to the operator's local files (already-owned host).
- **Hardcoded `/tmp` cache paths (B108)** — intermediate backtest caches on a single-user dev machine. (Symlink-race risk only applies to shared multi-user hosts, which is not the deployment model.)
- **`subprocess(..., shell=True)` (`paper_trade_today.py`)** — the command is built from a resolved binary path + a hardcoded `--url http://localhost:8001`; no web/user input flows in. Shell is required for the `||` failover.

These are documented and accepted; rewriting offline research tooling has no effect on the deployed attack surface. If any `scripts/` file is ever promoted into a request path, its pickle/shell/tmp usage must be re-reviewed first.

## Re-running the audit

```bash
# CodeQL (binary-precision dataflow) — Python + JavaScript security suites
codeql database create DB-py --language=python  --source-root=. --codescanning-config=cqconfig.yml
codeql database analyze DB-py python-security-and-quality.qls --format=sarifv2.1.0 --output=py.sarif
codeql database create DB-js --language=javascript --source-root=. --codescanning-config=cqconfig-js.yml
codeql database analyze DB-js javascript-security-and-quality.qls --format=sarifv2.1.0 --output=js.sarif

# semgrep + bandit
semgrep --config=auto --config=p/xss --config=p/javascript --config=p/owasp-top-ten \
  --exclude=.venv --exclude='*.min.js' --exclude=vendor web/ tradingagents/ scripts/
bandit -r web tradingagents scripts -x .venv,node_modules -ll
```
