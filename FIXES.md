# What got fixed (mapped to IMPROVEMENTS.md)

This pass fixed every Tier 1 + Tier 2 item and several below. Build is green,
12 tests pass, CLI runs.

## Tier 1 — Security & won't-run (ALL FIXED)
1. **Command injection** → new `src/utils/exec.ts` runs everything via `spawn`
   + arg arrays + `shell:false`, prompts over stdin. All executors use it.
2. **PoW arbitrary shell** → `proof-of-work.ts` tokenizes commands, enforces a
   binary **allowlist**, runs in a temp dir. Non-allowlisted binaries rejected.
3. **`new Function(code)`** → replaced with `node --check` / `py_compile`
   (parse-only, never executes).
4. **py_compile on unwritten file** → code is now materialized to a temp file
   first; that exact path is compiled and cleaned up.
5. **`dashboard` crash** → `startTUI` is now imported in `cli.ts`.
6. **Broken build (no jsx + invalid Ink props)** → tsconfig `jsx`/`bundler`
   resolution added; all invalid `<Text cyan>` props converted to `color="..."`.
   `tsc` compiles clean now.
7. **Git injection** → `git-manager.ts` uses `spawn('git', ['-C', path, ...])`.
8. **Auto-init / `git add -A`** → git is opt-in; commits stage only generated
   files; no auto-init.
9. **Secrets logged/committed** → `redact()` runs before disk writes; `.env`,
   `logs/`, `goals.json` gitignored.
10. **Non-atomic goals.json** → `store.ts` writes tmp+rename; corrupt files are
    backed up, not silently wiped.

## Tier 2 — Core logic bugs (ALL FIXED)
11. **Fake parallel cap** → real in-flight `Set` + `Promise.race`; test asserts
    peak concurrency ≤ max.
12. **Queue never executed** → `TaskQueue` now drives the orchestrator and drains
    pending tasks; smoke-tested.
13. **Codex substring grep** → `parseCodexReview()` is negation-aware and prefers
    structured JSON; unit-tested.
14. **Fabricated tokens/cost** → real usage parsed from CLI output; fallbacks are
    flagged `estimated` and surfaced in reports/commits.
15. **Double test run** → tests run once; failing results are reused for analysis.
16. **Code never written** → generated code is materialized before tests/commit.
17. **Split mode dead end** → split outputs persisted to `goals.json`;
    `integrate` loads them.
18. **In-memory config lies** → persisted to `~/.config/ai-orchestrator/config.json`.
19. **dist/ in repo** → gitignored; build from source.
20. **Dead master-control.tsx** → deleted.

## Also done
- #21 shared `Provider` abstraction; #22 `AbortController` halt + SIGINT handler;
  #23 configurable timeout w/ process-group kill; #35 CLI preflight checks;
  #36 deterministic commit hash; #37 push failures surfaced; #40 output cap;
  #41 Vitest suite; #42 GitHub Actions CI; #43 ESLint+Prettier;
  #44 package.json metadata/engines/files; #46 `tsx` dev script;
  #49 SECURITY.md; #50 `--dry-run` plan mode.

## Still worth doing (didn't get to)
- #26 unify state into SQLite (currently atomic JSON — fine for now).
- #28 fully typed event contract (started; `ExecutionEvent` is a union now).
- #34 execute real generated unit tests instead of grep heuristics.
- #47 swap the hand-rolled Ink input for `ink-text-input`.
- #48 README still needs a feature-by-feature honesty pass.

## Usage failsafe + OpenCode fallback (new)

Added so a quota/usage wall no longer kills a goal:

- **`src/providers/errors.ts`** — classifies every provider failure as `quota`
  (out of usage), `auth`, `transient`, or `fatal` from output + exit codes.
- **`src/providers/fallback.ts`** — `FallbackRunner`:
  - transient errors (5xx / network / timeout) → exponential backoff + jitter
    retry of the same provider (`maxRetriesPerProvider`, default 2);
  - quota/auth exhaustion → mark that provider dead for the run and **fall back
    to OpenCode**;
  - exhaustion is **sticky** — once Gemini/Claude is out, later steps skip
    straight to OpenCode instead of re-hitting the wall;
  - if OpenCode is also out, fails with a clear "both out of usage" error.
- Wired through every generation step (gemini prompt, claude-code write, codex
  review, test-gen, failure analysis, split mode). `step()` records the provider
  actually used; the log shows `🪂 Falling back gemini → opencode (quota)`.
- Config: `fallbackToOpenCode` (default on) + `maxRetriesPerProvider` (default 2),
  persisted. Toggle with `orchestrate config --fallback off --retries 3`.
- Covered by 9 new tests (classification, fallback, sticky exhaustion, retry,
  both-exhausted, fatal-no-fallback). This also closes audit item #24.

## Gemini model: fast + low-credit default (new)

- Gemini now runs on **`gemini-3.1-flash-lite`** by default (Google's fastest
  production model, ~$0.25/1M in — released March 2026), passed to the CLI as
  `-m <model>`.
- Configurable: `orchestrate config --gemini-model gemini-2.5-flash-lite`
  (the absolute cheapest at $0.10/1M in) or any model string your CLI accepts.
- Shown in `orchestrate config` output and the `--dry-run` plan. Cost estimate
  in metrics updated to Flash-Lite pricing (still labeled "estimated").
- Note: 3.1 Flash-Lite is preview; if your installed Gemini CLI rejects the
  exact id, set a known-good one with `--gemini-model`.

## Claude Code (Sonnet + plan mode) & Codex (gpt-5.5, medium effort) (new)

- **Claude Code** now runs as `claude -p --model sonnet --permission-mode plan`.
  Plan mode and model are configurable:
  `orchestrate config --claude-model opus --plan off`.
- **Codex** now runs as `codex review -m gpt-5.5 -c model_reasoning_effort=medium`.
  Configurable: `orchestrate config --codex-model gpt-5.5 --codex-effort high`.
- Both shown in `orchestrate config` and the `--dry-run` plan.
- Fallback safety preserved: when Claude Code or Codex runs out of usage, the
  FallbackRunner switches to OpenCode using OpenCode's own args — the
  sonnet/plan/codex flags are never leaked to the wrong CLI.
- Caveat: these flag forms match the current Claude Code and Codex CLIs
  (`--permission-mode plan`, `-c model_reasoning_effort=...`). If your installed
  CLI version differs, change the model strings/flags via `orchestrate config`.

## Production-readiness pass (final)

- **Plan mode default → OFF.** Claude Code runs `claude -p --model sonnet` and
  writes actual code. (`--plan on` still available if ever wanted.)
- **Codex invocation corrected for production**: `codex review` reviews git
  diffs, not piped code — switched to `codex exec -m gpt-5.5
  -c model_reasoning_effort=medium --skip-git-repo-check -` (non-interactive,
  prompt over stdin, works outside git repos).
- **Review prompt now demands a JSON verdict** (`{"passed": bool, "feedback"}`),
  and the parser extracts JSON from markdown fences and CLI noise, with the
  negation-aware text fallback. 3 new tests.
- **Chat UI progress fixed**: listener used `.once()` (handled one event ever)
  and filtered on a `'temp-goal'` id that never existed — now `.on()` with
  cleanup, matching the real typed events.
- **Reports label estimated tokens** ("includes estimates") instead of passing
  estimates off as real usage.
- **Process-level `unhandledRejection` guard**: halts in-flight goals and exits
  nonzero instead of dying silently.
- **End-to-end smoke verified** with stub CLI binaries:
  - happy path: gemini → claude-code → codex JSON verdict → sandboxed
    LLM-generated tests → goal COMPLETED;
  - failsafe path: gemini exits `429 RESOURCE_EXHAUSTED` → quota classified →
    falls back to OpenCode → sticky exhaustion skips gemini on later steps →
    goal COMPLETED.
- 26 unit tests passing; clean `tsc` build from scratch.

## Input + folder targeting (new)

- **Backspace fixed.** The chat UI's hand-rolled input listened for
  `key.backspace`, but most terminals send DEL (reported as `key.delete`) —
  so backspace did nothing. Replaced the whole hand-rolled handler with
  `ink-text-input`: backspace, delete, arrow keys, cursor movement, and paste
  all work now. (Audit item #47.)
- **Target folder: `-C` / `--dir <path>`** global flag. Chdirs before anything
  initializes, so git, logs/, goals.json, generated code, and provider working
  dirs all land in the target project:
  `node dist/cli.js -C ~/projects/myapp master`
  Invalid paths are rejected with a clear error. Verified end-to-end: goal run
  with `-C` writes goals.json + logs into the target dir.
