# AI-Orchestrator — 50 Improvements That Actually Matter

Cross-referenced against the `openclaude` reference. Ordered by blast radius: the stuff at the top will get you owned, lose your work, or refuse to build. The stuff at the bottom makes it a real tool instead of a demo. Nothing here is a cosmetic nitpick.

Legend: **[CRIT]** = security/data-loss/won't-run · **[BUG]** = it's broken and you haven't noticed · **[ARCH]** = structural · **[REL]** = reliability · **[OBS]** = observability/metrics · **[TEST]** = testing/CI · **[DX]** = developer/user experience.

---

## TIER 1 — Security & "it literally doesn't run" (do these first)

### 1. [CRIT] Command injection in EVERY CLI executor
`orchestrator.ts` builds shell strings with `execAsync(\`${cli} "${prompt.replace(/"/g,'\\"')}"\`)`. A `"` escape is **not** a security boundary — `$()`, backticks, `;`, `&&`, `|`, `>` all execute. Your prompts come from an LLM and from user input. This is remote code execution by design.
**Fix:** switch every executor to `execFile`/`spawn` with an args array and `shell:false`. Pass the prompt via **stdin**, not argv. openclaude uses `spawn(command, args, {...})` everywhere for exactly this reason.

### 2. [CRIT] `proof-of-work.ts` runs LLM-generated shell commands verbatim
`runTests()` takes `TEST: "name" | <command>` lines straight from Gemini and does `execAsync(command)`. The model decides what shell commands run on your machine. Combined with #1, the whole "validation" step is an arbitrary-code-execution engine.
**Fix:** never `exec` model output. Run tests in a sandbox (container/`vm`/`firejail`/nsjail), allowlist binaries, drop network, set CPU/mem limits, and run as an unprivileged user.

### 3. [CRIT] `new Function(code)` to "validate syntax"
In `validateSyntax()`. `new Function` compiles AND lets top-level expressions run on invocation paths; at minimum it's a parser you don't control. It also doesn't catch `import`/ESM/TS syntax.
**Fix:** parse with a real parser that never executes — `@babel/parser`, `acorn`, or `esbuild --bundle=false` transform. For TS, use the TS compiler API.

### 4. [CRIT] `validateSyntax` for Python compiles a file that's never written
It runs `python3 -m py_compile /tmp/test.py` but nothing ever writes `/tmp/test.py`. It validates stale/again-injectable state and the result is meaningless.
**Fix:** write the code to a temp file you create (`mkdtemp`), compile that exact path, clean up in a `finally`.

### 5. [CRIT] `orchestrate dashboard` crashes instantly
`cli.ts` calls `startTUI(orchestrator)` but only imports `startOrchestratorChat`. `startTUI` is undefined → `ReferenceError` the moment anyone runs `dashboard`/`watch`.
**Fix:** `import { startTUI } from './tui.js';` (it's exported from `tui.tsx`). Add a smoke test that loads every command's action.

### 6. [CRIT] The build is broken — `tsc` can't compile your `.tsx`
`tsconfig.json` has **no `jsx` setting** but you ship `app.tsx`, `progress.tsx`, `tui.tsx`, `master-control.tsx` using Ink/React. `npm run build` fails on a clean checkout. The committed `dist/` is the only reason it "works."
**Fix:** add `"jsx": "react-jsx"` (and `"jsxImportSource": "react"` if needed) to `compilerOptions`, plus `@types/react` to devDeps. Then delete `dist/` from git (#19).

### 7. [CRIT] `git-manager.ts` injects repo path and commit message into shell
`cd ${this.repoPath} && git commit -m "${msg}"`. `repoPath` and goal names flow into a shell string. A goal named `$(rm -rf ~)` is a bad afternoon.
**Fix:** use `spawn('git', ['-C', repoPath, 'commit', '-m', msg])`. `-C` removes the `cd`. Args array removes the injection.

### 8. [CRIT] Auto-`git init` + `git add -A` + auto-commit in the user's CWD
`GitManager` silently `git init`s the current directory and `git add -A`s **everything** — including any `.env`, secrets, and junk in CWD — then commits. Run it in `~` and enjoy.
**Fix:** require an explicit opt-in path, refuse if not already a repo unless `--init` is passed, respect `.gitignore`, and stage only files you actually generated.

### 9. [CRIT] Secrets and prompts get committed and logged in plaintext
`git add -A` (#8) plus `appendFileSync(logFile, message)` writes full prompts/outputs to `./logs/*.log` with no redaction. API keys pasted into prompts end up on disk and possibly in git history.
**Fix:** add a redaction pass (regex for common key formats) before logging/committing, gitignore `logs/` and `goals.json`, and document that prompts are persisted.

### 10. [CRIT] `goals.json` write is non-atomic → corruption on crash/parallel runs
`saveGoals()` does `writeFileSync('goals.json', ...)` directly. Crash mid-write or two processes writing = truncated/corrupt JSON, and `loadGoals()` silently swallows the parse error (`catch {}`), so you lose **all** goal history with zero warning.
**Fix:** write to `goals.json.tmp` then `renameSync` (atomic). On load failure, back up the corrupt file and warn loudly instead of nuking history.

---

## TIER 2 — Core logic bugs (it runs but lies to you)

### 11. [BUG] Parallel concurrency control is fake
`parallel.ts`: `executing = executing.filter((p) => p.pending)` — Promises have **no `.pending`** property, so the filter removes nothing. `executing` grows forever, `Promise.race` keeps racing settled promises, and you can blow way past `maxConcurrent`.
**Fix:** track in-flight promises in a `Set`, remove each in its own `.finally()`, and `await Promise.race(set)` only while `set.size >= max`. Add a test that asserts peak concurrency never exceeds the cap.

### 12. [BUG] The task queue never actually executes anything
`queue.ts processNext()` sets `isProcessing = true`, then `setImmediate(() => isProcessing = false)` — and **never calls the orchestrator**. `/queue add` and the CLI `queue add` enqueue tasks that sit there forever. `waitForEmpty()` can hang.
**Fix:** wire the queue to a runner: `processNext` should `await orchestrator.runLoopMode(...)`/`runSplitMode(...)` based on `task.mode`, then mark complete/failed and recurse. This is the single biggest "advertised feature that doesn't work."

### 13. [BUG] Codex "pass" detection is a substring grep
`executeCodex`: `passed = stdout.includes('passed') || stdout.includes('success')`. "This code has **not** passed review" → counts as passed. "0 successes" → passed. Your quality gate is decided by coincidental substrings.
**Fix:** make Codex emit structured output (JSON: `{passed: bool, issues: []}`) and parse it. If you can't control Codex output, use a strict sentinel line and exit codes.

### 14. [BUG] Token counts and cost are completely fabricated
`orchestrator.ts` hardcodes `completeStep(..., 500)`, `1000`, `300`. `metrics.ts`/`reporter.ts` then proudly report "Total Tokens" and "$Cost" to 4 decimals. It's theater — the numbers have no relationship to reality.
**Fix:** capture real usage from each CLI (most print token/usage info or support `--json`). If a CLI won't report it, estimate from `tiktoken`/char-count and **label it "estimated"** instead of presenting fiction as fact.

### 15. [BUG] Loop mode runs the test suite twice and inconsistently
On PoW failure it calls `powValidator.runTests(...)` **again** with `generateTestScript` (heuristic) even though the first run used `generateTestsWithClaude` (LLM). So the failures you "analyze" aren't the failures you saw. Wasteful and incoherent.
**Fix:** run tests once, keep the `TestResult[]`, pass the failing subset into the analyzer. Don't regenerate.

### 16. [BUG] Generated code is never written anywhere before "testing"
Tests reference `/tmp/test.js`, `app.js`, `config.json`, etc., but the orchestrator never writes Claude's output to any of those paths. Tests run against files that don't exist or are leftovers. "All tests passed" is often "all tests no-op'd."
**Fix:** materialize generated artifacts to a known working dir, template the test commands against those real paths, and verify the files exist before claiming success.

### 17. [BUG] Split mode is a dead end
`runSplitMode` sets `goal.status = 'pending'` and tells you to run `orchestrate integrate <id>` — but the goal only lives in memory of a process that's about to exit, and `manualIntegrate` looks it up in a fresh process's empty map. The handoff can't work across CLI invocations.
**Fix:** persist enough state (the OpenCode/Codex outputs) to `goals.json`/disk so a later `integrate` invocation can actually load and use them.

### 18. [BUG] `setEnabled(true)` on git is a lie inside a one-shot CLI
`git enable` flips an in-memory flag in a process that exits immediately. The next command starts fresh with auto-push/enabled defaults from the constructor. None of the toggle commands persist.
**Fix:** persist config (git enabled, autoPush, maxParallel, etc.) to a config file (`~/.config/ai-orchestrator/config.json`) and load it on boot.

### 19. [BUG] You're shipping `dist/` in the repo
Committed build output drifts from source, hides the broken build (#6), and bloats diffs. The reference repo builds from source.
**Fix:** gitignore `dist/`, build in CI / on `prepublishOnly`, and verify a clean `npm ci && npm run build` succeeds.

### 20. [BUG] `master-control.tsx` is dead/duplicate code
It re-implements a TUI and also imports `startTUI`, but `cli.ts master` routes to `professional/app.tsx` instead. Two parallel UIs, one unreferenced. Confusing and rots.
**Fix:** pick one. Delete the other. Right now `professional/app.tsx` is the live one.

---

## TIER 3 — Architecture (why everything above keeps happening)

### 21. [ARCH] Provider abstraction is copy-pasted four times
`executeGemini`, `executeClaudeCode`, `executeCodex`, `executeOpenCode` are the same function with a different binary. Every fix (injection, timeout, retry, usage capture) has to be made 4×, and you'll miss one.
**Fix:** define a `Provider` interface (`run(prompt, opts): Promise<ProviderResult>`) with one shared `spawn`-based implementation. openclaude has a provider layer (`scripts/provider-*.ts`, `dev:codex/openai/gemini/ollama`) — that's the pattern.

### 22. [ARCH] No cancellation primitive — `haltGoal` can't actually stop work
`halted` is only checked at the top of the loop. An in-flight 5-minute `execAsync` keeps running after halt; Ctrl-C orphans child processes.
**Fix:** thread an `AbortController` through every provider call (openclaude uses `createAbortController()` + `signal` throughout `QueryEngine`). On halt/SIGINT, abort the signal and kill child processes.

### 23. [ARCH] Hardcoded `300000ms` timeout, no per-call config, no kill-on-timeout
Every executor hardcodes a 5-min timeout. `.env` advertises `CLI_TIMEOUT` but nothing reads it. And `exec` timeout doesn't reliably kill child trees.
**Fix:** centralize timeout config (read `CLI_TIMEOUT`), make it per-provider, and use `spawn` + explicit `kill('SIGKILL')` with `detached`/process-group kill on timeout.

### 24. [ARCH] No retry/backoff on transient failures
Any CLI hiccup (rate limit, network blip) throws and either kills the goal or just gets logged and loops with no delay — hammering the API.
**Fix:** wrap provider calls in retry-with-exponential-backoff + jitter, distinguishing retryable (429/5xx/timeout) from fatal (bad args).

### 25. [ARCH] Loop-termination logic is incomprehensible and can run ~150 iterations
`while(true)` with `attempt > maxInitialAttempts*3 && powAttempts > 50`. With default retries=3 that's "fail only after 9 attempts AND 50 PoW loops" — i.e. up to dozens of paid LLM calls before it gives up, and the condition is an unreadable mess.
**Fix:** one clear budget: `maxIterations` and/or a wall-clock/$ budget. Exit with a clear reason. Make it readable.

### 26. [ARCH] State lives in three places that don't agree
`Orchestrator.goals` (Map), `goals.json`, `MetricsTracker.metrics`, and `TaskQueue.queue` are all separate in-memory stores with different lifetimes, none shared across CLI invocations. That's the root cause of #12/#17/#18.
**Fix:** single persisted store (SQLite via `better-sqlite3`, or one JSON store with a repository layer) that all subsystems read/write.

### 27. [ARCH] `any` everywhere defeats the point of TypeScript
`ExecutionEvent.data: any`, `result?: any`, `(this.tokenCosts as any)[cliType]`, event payloads untyped. You get TS's compile cost with none of its safety, and the event bug in #28 slips through.
**Fix:** type the event union, provider results, and metrics maps. Turn on `noUncheckedIndexedAccess`.

### 28. [BUG] Event names don't match between emitter and listeners
Orchestrator emits `this.emit('execution', {type:'log', step, message})` but `commands.ts` listens for `event.goalId === 'temp-goal'` (a goalId that's never set) and reads `event.step` that isn't always present. The progress UI receives nothing useful.
**Fix:** define a typed event contract and make emitter + all listeners conform. Test it.

### 29. [ARCH] No structured logging — it's all `console.log(chalk...)`
Logs are ANSI-colored prose, impossible to grep/parse, and color codes get written into the `.log` files too (you imported `strip-ansi` but don't use it on file writes).
**Fix:** structured logger (level + JSON to file, pretty to TTY). Strip ANSI for file sink. You already depend on `strip-ansi` — use it.

### 30. [ARCH] `.metrics/` directory is never created before write
`saveMetricsFile` does `writeFileSync('${repoPath}/.metrics/${id}.json')` with no `mkdirSync`. First call throws (silently swallowed), so metrics files never persist.
**Fix:** `mkdirSync(dir,{recursive:true})` first. (And it's never even called from the loop — wire it in.)

---

## TIER 4 — Reliability & correctness

### 31. [REL] `loadGoals` swallows corruption; no schema/versioning
`catch {}` means a malformed or schema-changed `goals.json` silently becomes "no goals." Users lose history and never know.
**Fix:** validate with a schema (zod), version the file, migrate or back-up-and-warn on mismatch.

### 32. [REL] No backpressure/serialization on `goals.json` writes
Parallel goals all call `saveGoals()` writing the whole file. Last-write-wins clobbers concurrent updates (lost metrics/status).
**Fix:** serialize writes through a queue/mutex, or move to SQLite (#26) which handles this.

### 33. [REL] Gemini failure in test-gen/analysis silently degrades quality
`generateTestsWithClaude` and `analyzeTestFailures` swallow errors and fall back to weak heuristics, but report success-ish. You think Claude wrote smart tests; it didn't.
**Fix:** surface the degradation in the log/UI ("LLM test-gen failed, using heuristics") so the operator knows the validation is weaker.

### 34. [REL] Heuristic tests are trivially gameable `grep`s
`grep -q "catch\|error" file` "proves" error handling. Code with the word "error" in a comment passes. These tests prove nothing.
**Fix:** at minimum run the code, lint it, and run any real test files. Prefer executing generated unit tests over keyword greps.

### 35. [REL] No validation that configured CLI paths exist
`validateCLIPaths()` just prints paths; it doesn't check they exist or are executable. First real failure is a cryptic exec error mid-goal.
**Fix:** `which`/`access(X_OK)` each path at startup, fail fast with a clear "install X or set Y_PATH" message. openclaude ships a `system-check.ts` script for this.

### 36. [REL] `commitGoal` hash parsing is fragile
Regex `/\[.+?\s([a-f0-9]{7})/` on `git commit` stdout breaks on detached HEAD, root commits, localized git output, or `--quiet`.
**Fix:** after commit, run `git rev-parse --short HEAD` to get the hash deterministically.

### 37. [REL] Auto-push failures are silently swallowed
`autoPush` catch block has a comment "silently fail" — so a goal "completes + commits" but never pushed, and you find out days later.
**Fix:** capture and report push result; warn on failure with the actual error.

### 38. [REL] No global SIGINT/SIGTERM handler → orphaned child processes
Ctrl-C during a goal kills Node but leaves `gemini`/`claude` children running and `goals.json` half-written.
**Fix:** install signal handlers that abort controllers (#22), kill child process groups, flush state, then exit.

### 39. [REL] `executeGemini` used for both Gemini AND Claude tasks
`generateTestsWithClaude` and `analyzeTestFailures` call `this.executeGemini(...)` despite the names/comments saying Claude. Misleading and means "Claude analysis" is actually Gemini.
**Fix:** route to the intended provider (or rename honestly). This is why the provider abstraction (#21) matters.

### 40. [REL] No max output / buffer guard on exec
`exec` has a default `maxBuffer` (~1MB); a chatty CLI overflows it and throws `ENOBUFS`, killing the goal with a confusing error.
**Fix:** with `spawn` you stream stdout/stderr; cap and truncate with an explicit limit, and handle large outputs gracefully.

---

## TIER 5 — Testing, CI, packaging

### 41. [TEST] Zero tests. None.
`find -name '*.test.ts'` = 0. openclaude has dozens (`commands.test.ts`, `QueryEngine.*.test.ts`, guard tests). For a tool that auto-runs code and commits to git, no tests is reckless.
**Fix:** add Vitest. Start with the bug-prone units: queue runner (#12), parallel cap (#11), command parsing/routing, Codex result parsing (#13), goals persistence round-trip.

### 42. [TEST] No CI pipeline
Nothing enforces build/lint/test on a PR, which is how #6 (broken build) shipped.
**Fix:** GitHub Actions: `npm ci`, `tsc --noEmit`, lint, `vitest run` on push/PR. Block merge on red.

### 43. [TEST] No linter / formatter
No ESLint, no Prettier config. Inconsistent style and easy-to-lint bugs (unused imports, floating promises) go unnoticed.
**Fix:** ESLint with `@typescript-eslint`, enable `no-floating-promises` (you have several), Prettier, run in CI.

### 44. [DX] `package.json` is missing `engines`, `files`, and real metadata
No Node version constraint (you use ESM + top-level features), no `files` allowlist (you'll publish junk), empty author. `main` points to `dist/index.js` which **doesn't exist** (entry is `dist/cli.js`).
**Fix:** add `"engines": {"node": ">=18"}`, `"files": ["dist"]`, fix `main`/`bin`, add `prepublishOnly: tsc`.

### 45. [DX] No dependency lockfile committed
No `package-lock.json` shown. Without it, `npm install` drift causes "works on my machine."
**Fix:** commit the lockfile, use `npm ci` in CI and docs.

### 46. [DX] `dev` script uses `ts-node` but project is pure ESM + tsx
`"dev": "ts-node src/cli.ts"` is fragile with ESM/`.tsx`. It'll choke on the Ink JSX.
**Fix:** use `tsx` (`"dev": "tsx src/cli.ts"`) — handles ESM + TSX out of the box. Add `tsx` to devDeps.

---

## TIER 6 — UX, docs, and honesty

### 47. [DX] The Ink chat input is hand-rolled and broken for real use
`app.tsx` builds an input from raw `useInput` char codes: no cursor movement, no paste, no arrow keys, no history, mishandles multibyte. Backspace-only editing is painful.
**Fix:** use `ink-text-input` (or the maintained input components). You already pull in Ink ecosystem deps.

### 48. [DX] README claims features that don't work
"macOS CLI," parallel execution, queue, split-mode integration, cost tracking — several are broken (#11/#12/#17) or fake (#14). The README oversells a demo.
**Fix:** after fixing, make the README match reality. Until then, mark experimental features clearly. Trust matters.

### 49. [DX] No SECURITY.md / threat model despite executing AI-generated code
This tool's entire job is running model-generated code and shell commands and committing to git. That demands a documented security posture. openclaude ships `SECURITY.md` and `verify-no-phone-home.ts`.
**Fix:** add SECURITY.md: sandbox model, what's executed, how to report issues, and the "never run untrusted output unsandboxed" warning.

### 50. [DX] No dry-run / plan mode
There's no way to see what the orchestrator *would* do (which prompts, which commands, which commits) without actually doing it. For a tool that runs code and writes git, that's table stakes.
**Fix:** add `--dry-run` that logs the planned provider calls and test commands without executing, and require confirmation before the first destructive git action.

---

## Suggested order of attack

1. **Stop the bleeding:** #6 (build), #5 (dashboard crash) — get it running.
2. **Stop the danger:** #1, #2, #3, #7, #8, #9 — kill injection + sandbox execution.
3. **Make the features real:** #12 (queue), #11 (parallel), #16 (write code), #13 (Codex parse), #14 (real tokens).
4. **Make it durable:** #10/#26/#31 (state), #22 (cancellation), #41/#42 (tests + CI).
5. **Polish:** the rest.

The reference (`openclaude`) already demonstrates the right patterns for the big three: `spawn` with arg arrays (no injection), `AbortController` everywhere (real cancellation), and a real test/CI culture. Steal those patterns shamelessly.
