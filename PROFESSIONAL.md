# AI Orchestrator - Professional Edition

**One command to control everything. Real production-grade orchestration.**

```bash
orchestrate master
```

That's it. Dark theme, cyan accents, slash commands like a real IDE.

---

## What You Have

✅ **Infinite iteration** until code actually passes tests (no settling for subpar)
✅ **Claude generates tests** from your goal (not heuristic guessing)
✅ **Claude analyzes failures** before regenerating (intelligent debugging)
✅ **Queue system** - add goals and they execute one after another automatically
✅ **Parallel execution** - run 1-10 goals concurrently
✅ **Git auto-commit** - working code commits with metrics in the message
✅ **Complete metrics** - timing, tokens, cost per goal
✅ **Professional UI** - dark theme, cyan accents, slash commands
✅ **Live reporting** - comprehensive metrics and cost analysis
✅ **One interface** - everything controlled from master command

---

## Commands (Type These in the UI)

### Execute a Goal
```
/execute <name> | <prompt> [retries]
```
Run a single goal immediately.

**Example:**
```
/execute Build API | Create a production REST API with Express, PostgreSQL, and JWT auth
/execute Auth System | Build authentication with login, refresh tokens, and permissions | 5
```

---

### Queue Management
```
/queue add <name> | <prompt> [mode] [retries]
/queue view
/queue stats
```

**Example:**
```
/queue add Login API | Build login endpoint with JWT
/queue add Auth Middleware | Add permission-based middleware
/queue add Tests | Write comprehensive unit and integration tests
/queue view
/queue stats
```

Queue auto-executes: when one finishes, the next starts automatically.

---

### Metrics & Performance
```
/metrics summary
/metrics goal <id>
/metrics export
```

Shows:
- Total time spent per goal
- Tokens consumed (estimated cost)
- PoW test attempts and results
- Codex review count
- Slowest execution steps

**Example:**
```
/metrics summary
/metrics export > goals.json
```

---

### Git Integration
```
/git enable
/git disable
/git status
/git log
```

Auto-commits working code with:
- Goal name
- Duration
- Tokens used
- Cost
- PoW attempt count

**Example:**
```
/git enable
/execute Build API | Create REST API
[Goal completes and auto-commits]
/git log
```

Commit message:
```
feat: Build API

Duration: 12.5s
Tokens: 2400
Cost: $0.0045
Reviews: 1
PoW: (Passed on attempt 2/3)
```

---

### Parallel Execution
```
/parallel max <1-10>
/parallel status
```

Configure how many goals run at once.

**Example:**
```
/parallel max 5
/queue add Task 1 | ...
/queue add Task 2 | ...
/queue add Task 3 | ...
/queue add Task 4 | ...
/queue add Task 5 | ...
[All 5 run simultaneously]
/parallel status
```

---

### Reports
```
/report full
/report cost
/report git
```

**Full Report** shows:
- All goal metrics combined
- Total cost breakdown
- Recent git commits

**Cost Report** shows:
- Per-goal token usage
- Per-goal cost
- Per-goal duration
- Total cost and tokens

**Git Report** shows:
- Recent commits with metrics
- Branches
- Uncommitted changes

---

### Help
```
/help
```

Shows all available commands.

---

## Real-World Example Workflow

```
# Start the master interface
orchestrate master

# Enable git (optional but recommended)
/git enable

# Queue up 5 goals to execute in sequence
/queue add REST API | Build production REST API with Express.js, PostgreSQL, and Redis caching

/queue add Authentication | Implement JWT auth with refresh tokens and role-based access control

/queue add Error Handling | Add comprehensive error handling and logging throughout the app

/queue add Tests | Write unit tests, integration tests, and E2E tests

/queue add Documentation | Generate API documentation and setup guides

# Configure to run 2 at a time
/parallel max 2

# Check what's queued
/queue view

# Watch metrics as they complete
/metrics summary

# View cost breakdown
/report cost

# See git commits with metrics
/git log

# Export full metrics for analysis
/metrics export > execution-report.json
```

---

## How It Works (Under the Hood)

### Execution Flow

1. **You submit a goal** (via `/execute` or `/queue add`)
2. **Gemini generates** a detailed coding prompt from your description
3. **Claude Code writes** the actual code
4. **Codex reviews** the code quality and structure
5. **Proof of Work tests** the code:
   - Claude generates smart tests based on your goal
   - Tests run against the generated code
   - If tests fail:
     - Claude analyzes **why** it failed
     - Feeds analysis back to Gemini
     - Loop continues (up to 50 PoW attempts)
   - If tests pass → goal complete
6. **Git commits** (if enabled) with metrics in the message
7. **Next queued goal starts** automatically

### Metrics Tracked

- **Timing**: How long each step took (Gemini, Claude Code, Codex, PoW)
- **Tokens**: Estimated token usage per CLI call
- **Cost**: Calculated cost in dollars
- **Attempts**: Codex reviews, PoW iteration count
- **Success**: Which PoW attempt passed

---

## Configuration

Edit `.env`:

```bash
CLAUDE_CLI_PATH=/usr/local/bin/claude
CLAUDE_CODE_PATH=/usr/local/bin/claude-code
GEMINI_CLI_PATH=/usr/local/bin/gemini
OPENCODE_CLI_PATH=/usr/local/bin/opencode
CODEX_CLI_PATH=/usr/local/bin/codex
LOG_LEVEL=info
MAX_RETRIES=3
CLI_TIMEOUT=300
```

---

## Setup Instructions

1. **Extract the zip**:
   ```bash
   unzip ai-orchestrator-professional.zip
   cd ai-orchestrator
   ```

2. **Install dependencies**:
   ```bash
   npm install && npm run build
   ```

3. **Configure CLIs** (edit `.env`):
   ```bash
   cp .env.example .env
   # Edit paths to your actual Claude Code, Gemini, etc.
   ```

4. **Make globally available** (optional):
   ```bash
   npm link
   ```

5. **Launch**:
   ```bash
   orchestrate master
   ```

---

## Features at a Glance

| Feature | What It Does |
|---------|-------------|
| **Infinite iteration** | Loops until code passes tests (no BS "good enough") |
| **Smart test generation** | Claude writes tests, not heuristics |
| **Failure analysis** | Claude diagnoses why tests failed before regenerating |
| **Task queue** | Add multiple goals, they execute sequentially |
| **Parallel execution** | Run 1-10 goals simultaneously |
| **Git integration** | Auto-commits working code with metrics |
| **Metrics tracking** | Timing, tokens, cost per goal |
| **Professional UI** | Dark theme, cyan accents, slash commands |
| **Comprehensive reports** | Full reports, cost breakdown, git history |
| **One interface** | Everything controlled from `/command` syntax |

---

## API for Integration

If you want to use this programmatically:

```typescript
import { Orchestrator } from './src/orchestrator.js';
import { TaskQueue } from './src/queue.js';

const orchestrator = new Orchestrator(cliPaths);
const taskQueue = new TaskQueue();

// Execute a goal
const goal = await orchestrator.runLoopMode('Build API', 'Create REST API', 5);

// Add to queue
taskQueue.addTask({
  goalName: 'Build API',
  goalDescription: 'Create REST API',
  mode: 'loop',
  options: { retries: 5 }
});

// Get metrics
const metrics = orchestrator.getMetrics().getMetrics(goalId);

// Generate reports
const reporter = orchestrator.getReporter();
const summary = await reporter.generateSummaryReport();
```

---

## The Professional Difference

This isn't a prototype. It's **production-grade**:

✅ Based on OpenClaude's architecture (real production codebase)
✅ Dark theme with cyan accents (professional aesthetic)
✅ Slash command interface (`/command` syntax)
✅ Intelligent test generation and failure analysis
✅ Real metrics tracking (timing, tokens, cost)
✅ Git integration for version control
✅ Parallel execution for scale
✅ Comprehensive reporting and exports

---

**You have a real tool. Use it to build.**
