# How Lucy Tests, Fixes, and Escalates

> **The Universal Test-Fix-Retry Framework**
>
> Every task Lucy builds — cron jobs, scripts, apps, shell commands, API calls —
> must pass a live test before being marked done. This document explains the full
> framework: why it exists, how it works, and what happens at every step.

---

## Why This Exists

### The Problem: Hallucinated Completion

Before this framework, Lucy had a critical failure mode. Consider a real example:

> **User:** "Switch the booth monitor to a lightweight heartbeat script."
> **Lucy:** "Done. Switched to lightweight heartbeat." ✓ **(but made zero tool calls)**

The `task.json` was never updated. No script was written. Lucy said "done" because
she could not actually perform the action — the `lucy_modify_cron` tool at the time
had no parameters to change a cron's type or write script code. Faced with an
impossible request, the LLM confabulated a confident completion.

This is not a prompt engineering problem. It is not fixable with warnings or instructions
alone. The root causes were:

1. **Capability gap:** The tool didn't expose the parameters the LLM needed.
2. **No mandatory verification:** The agent loop allowed a text-only response to
   close a task without any live confirmation that the action had happened.
3. **"Done" was untestable:** There was no structured pass/fail signal, so the loop
   accepted any text as completion.

The fix was systemic, operating at three levels:

| Level | What Changed |
|-------|-------------|
| **Tool capability** | `lucy_modify_cron` and `lucy_create_cron` gained `new_type` and `script_code` parameters, so the LLM can express the full intent in one atomic call |
| **Script validation** | AST syntax checking + `_dry_run_script` (subprocess, 10s timeout, `LUCY_DRY_RUN=1`) runs immediately when a script is written |
| **Agent loop state machine** | After any mutating tool call, Lucy is *required* to produce a structured `TEST PASSED` or `TEST FAILED` response before the loop is allowed to close |

---

## The Escalation Ladder

When something doesn't work, Lucy doesn't give up after one try. She follows a
structured seven-stage escalation ladder before admitting defeat:

```
BUILDING
  │
  │ (mutating tool used)
  ▼
TESTING ──── TEST PASSED ──────────────────────────────▶ PASSED ✓
  │
  │ TEST FAILED (up to 2 times)
  ▼
FIXING ──────────────────────────────────────────────▶ back to TESTING
  │
  │ (fix budget exhausted at default model)
  ▼
ESCALATED (switch to frontier model, fix_attempts reset)
  │
  │ TEST FAILED (up to 2 more times with frontier model)
  ▼
FIXING (frontier model fixes) ───────────────────────▶ back to TESTING
  │
  │ (frontier fix budget also exhausted)
  ▼
REAPPROACH #1 (completely different implementation, fix_attempts reset)
  │
  │ TEST FAILED (up to 2 times)
  ▼
FIXING (reapproach) ─────────────────────────────────▶ back to TESTING
  │
  │ (reapproach fix budget exhausted)
  ▼
REAPPROACH #2 (second different implementation)
  │
  │ TEST FAILED (up to 2 times)
  ▼
FIXING ──────────────────────────────────────────────▶ back to TESTING
  │
  │ (all approaches exhausted)
  ▼
GAVE_UP — Lucy tells the user exactly what was tried and what the blocker is
```

At every point where `TEST PASSED` is returned, the loop exits cleanly. The
escalation only continues if tests keep failing.

---

## The State Machine in Code

**File:** `src/lucy/core/agent.py`

```python
class _TFPhase(str, Enum):
    BUILDING   = "building"    # Creating / modifying something
    TESTING    = "testing"     # Told to test; waiting for TEST PASSED/FAILED
    FIXING     = "fixing"      # Test failed; fixing, will re-test
    ESCALATED  = "escalated"   # Promoted to frontier model
    REAPPROACH = "reapproach"  # Trying a completely different approach
    PASSED     = "passed"      # Tests confirmed — exit cleanly
    GAVE_UP    = "gave_up"     # All attempts exhausted — surface to user

@dataclass
class _TestFixState:
    phase: _TFPhase = _TFPhase.BUILDING
    fix_attempts: int = 0       # Fix cycles used at current model tier
    escalated: bool = False     # Whether frontier model is active
    approach_attempts: int = 0  # Completely-different-approach counter
    failure_log: list[str]      # All failure reasons across all attempts
    mutating_tools: list[str]   # Tool categories that were mutated
```

A fresh `_TestFixState()` is created at the start of every `_agent_loop()` call.

### Transition Rules

| Current Phase | Condition | Next Phase |
|---------------|-----------|------------|
| `BUILDING` | Mutating tool was called, turn > 0 | `TESTING` |
| `TESTING` | `response_indicates_test_pass()` returns `True` | `PASSED` → exit |
| `TESTING` | Test failed, fix budget remaining | `FIXING` |
| `TESTING` | Default fix budget exhausted, not yet escalated | `ESCALATED` |
| `TESTING` | Frontier fix budget exhausted, approaches remaining | `REAPPROACH` |
| `TESTING` | All approaches exhausted | `GAVE_UP` |
| `FIXING` | Agent produces text after a fix | `TESTING` |
| `REAPPROACH` | Agent produces text after new approach | `TESTING` |

---

## How the Pass/Fail Signal is Detected

**File:** `src/lucy/core/quality.py` — `response_indicates_test_pass(text)`

The state machine cannot read the LLM's mind. It reads its words. Lucy is
instructed to always prefix verification results with one of:

- `TEST PASSED: <what was tested and what confirmed it works>`
- `TEST FAILED: <what was tested and what went wrong>`

The `response_indicates_test_pass()` function uses three regex patterns:

### Explicit Pass Signals (`_TEST_PASS_RE`)

Any of these in the response = **PASS**:

| Pattern | Example |
|---------|---------|
| `TEST PASSED` / `TESTS PASSED` | `TEST PASSED: Cron ran without errors.` |
| `VERIFICATION PASSED` | `VERIFICATION PASSED: endpoint returned 200.` |
| `verified` / `confirmed working` | `The cron is confirmed working.` |
| `working correctly` | `The script is working correctly.` |
| `successfully triggered/tested/ran` | `Successfully triggered — output was clean.` |
| `exited with code 0` | `Script exited with code 0.` |
| `no errors found/detected` | `Ran the script — no errors detected.` |
| `deployed and live/working` | `App is deployed and live.` |
| `service is running/healthy` | `Service is running.` |

### Explicit Fail Signals (`_TEST_FAIL_RE`) — **Fail Beats Pass**

Any of these in the response = **FAIL**, even if there are also pass signals:

| Pattern | Example |
|---------|---------|
| `TEST FAILED` / `TESTS FAILED` | `TEST FAILED: pymongo not found.` |
| `Traceback (most recent call last)` | Any Python traceback |
| `SyntaxError`, `ImportError`, `ModuleNotFoundError` | Runtime exceptions |
| `TypeError`, `AttributeError`, `RuntimeError` | Runtime exceptions |
| `exited with code 1` / `exit code [non-zero]` | `Script exited with code 2.` |
| `failed to run/execute/import/connect` | `Failed to run: missing dep.` |
| `HTTP 4xx` / `HTTP 5xx` | `Deployment check returned HTTP 500.` |
| `connection refused/timed out` | `Connection refused to MongoDB.` |
| `did not work` / `not working` | `The cron did not work.` |
| `still failing` / `keeps failing` | `Script keeps failing on line 3.` |

> **Rule:** A response that says "TEST PASSED: triggered — but got Traceback..."
> is still classified as **FAIL**. The fail regex always wins.

### Ambiguous (no explicit signal)

If neither pattern matches, the response is treated as **FAIL with an ambiguity
message**, forcing the LLM to be more explicit:

> `"verification response is ambiguous — no clear TEST PASSED or TEST FAILED signal.
> Agent must explicitly confirm with 'TEST PASSED: <what was tested>' or 'TEST FAILED:
> <what failed>'."`

This means responses like "Done.", "Created the cron.", "Set up the booth monitor." 
are all treated as failures — because they have not confirmed the thing actually works.

---

## Verification Hints: Tool-Specific Test Instructions

**File:** `src/lucy/core/agent.py` — `_build_verify_hints(tool_names)`

When the state machine transitions from `BUILDING` to `TESTING`, it builds a
set of tool-specific verification instructions based on which mutating tools
were used in that task.

| Tool Category | Verification Instruction |
|---------------|--------------------------|
| `lucy_create_cron`, `lucy_modify_cron` | Call `lucy_list_crons` to confirm the cron exists with the correct settings, then call `lucy_trigger_cron` to run it immediately and verify the output. |
| `lucy_create_heartbeat` | Call `lucy_list_heartbeats` to confirm the monitor is active. |
| `lucy_exec_command` | Check the output for errors or unexpected results. If a file was written, read it back to confirm it was written correctly. |
| `lucy_execute_python`, `lucy_execute_bash` | Check the output for errors, tracebacks, or unexpected results. |
| `lucy_spaces_deploy` | Check the deployment status and URL to confirm it's live. |
| `lucy_start_service` | Call `lucy_service_logs` to confirm the service started correctly. |
| `COMPOSIO_MULTI_EXECUTE_TOOL` | Check each result in the response for errors or partial failures. |
| *(anything else)* | Read back or list the thing you just changed to confirm the result matches your intent. |

These hints are injected directly into the conversation as a user message,
so they become part of the LLM's context window when it decides how to test.

---

## Script Validation: Before Testing Even Begins

Before the state machine even kicks in, scripts written to disk go through
two validation layers:

### Layer 1: AST Syntax Check

```python
import ast
ast.parse(script_code)  # Raises SyntaxError if invalid
```

Runs immediately in `scheduler.create_cron()` and `scheduler.modify_cron()`.
If the syntax is broken, the tool call fails and returns an error — the LLM
never writes a broken script to disk.

### Layer 2: Dry-Run Subprocess

**File:** `src/lucy/crons/scheduler.py` — `_dry_run_script(script_path)`

```python
env = {**os.environ, "LUCY_DRY_RUN": "1", "WORKSPACE_ID": workspace_id}
proc = await asyncio.create_subprocess_exec(
    sys.executable, script_path,
    env=env,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
)
stdout, stderr = await asyncio.wait_for(
    proc.communicate(), timeout=_DRYRUN_TIMEOUT_S
)
```

Key properties:
- **`LUCY_DRY_RUN=1`** is set — scripts should check for this and skip real side
  effects (actual Slack messages, database writes, emails) during dry runs.
- **10-second timeout** — if the script hangs, it's treated as valid (syntax is fine,
  the timeout is a runtime infrastructure issue). A `"note": "timed out"` is included
  in the result.
- **Non-zero exit** — if the script exits with any non-zero code, `ok=False` is returned
  and the tool call fails. The LLM must fix the script before it can be saved.
- **Import errors** — caught immediately, before any scheduled run.
- **`WORKSPACE_ID`** is injected into `env`, so scripts that reference the workspace
  context can do so safely during dry runs.

If either layer fails, `lucy_create_cron` or `lucy_modify_cron` returns:
```json
{
  "success": false,
  "error": "Script dry-run failed: ModuleNotFoundError: No module named 'pymongo'. Fix and retry."
}
```

The LLM is forced to fix the script before the task can proceed.

---

## Verification Hints in Tool Results

When `lucy_create_cron` or `lucy_modify_cron` **succeed**, the tool result
includes a `verification_hint` field:

```json
{
  "success": true,
  "created": "booth-offline-monitor",
  "type": "script",
  "script_written": "/workspaces/.../crons/booth-offline-monitor/script.py",
  "dry_run": "passed",
  "verification_hint": "Call lucy_list_crons to confirm 'booth-offline-monitor' was created. Then call lucy_trigger_cron with cron_name='booth-offline-monitor' to run the script once and verify it works."
}
```

This hint is returned to the LLM as part of the tool result, providing direct
guidance on how to complete the verification phase — even before the state
machine injects the formal testing prompt.

---

## Configuration Thresholds

**File:** `src/lucy/config.py`

The escalation ladder is configurable without touching code:

```python
# How many fix cycles the default model gets before escalating to frontier.
test_fix_max_attempts_default: int = 2   # LUCY_TEST_FIX_MAX_ATTEMPTS_DEFAULT

# How many fix cycles the frontier model gets before trying a new approach.
test_fix_max_attempts_frontier: int = 2  # LUCY_TEST_FIX_MAX_ATTEMPTS_FRONTIER

# How many completely different approach attempts before giving up.
test_fix_max_approaches: int = 2         # LUCY_TEST_FIX_MAX_APPROACHES
```

With the defaults (all 2), the full worst-case ladder before giving up is:

```
2 (default fixes) + 1 (escalation) + 2 (frontier fixes) +
2×(2 approach fixes) = 9 test-fix cycles maximum
```

In practice, most tasks pass on the first test or after one fix cycle.

---

## How This Fits Into the Agent Loop

**File:** `src/lucy/core/agent.py` — `_agent_loop()`

The state machine slot in the loop is the `if not tool_calls` block — the
point where the LLM has returned text without making any tool calls:

```
Turn N: LLM returns text-only response (no tool calls)
  │
  ├── Narration detection: is the LLM describing what it would do?
  │     If yes → nudge: "Call the tool directly, don't describe it."
  │
  ├── Test-Fix State Machine:
  │     BUILDING → TESTING  (if mutating tools were used)
  │     TESTING  → evaluate response_indicates_test_pass()
  │     TESTING  → FIXING   (if test failed, budget remaining)
  │     TESTING  → ESCALATED (fix budget exhausted, not yet on frontier)
  │     TESTING  → REAPPROACH (frontier also exhausted)
  │     TESTING  → GAVE_UP  (all approaches done)
  │     FIXING/REAPPROACH → TESTING (after fix applied)
  │     PASSED  → break loop ✓
  │
  └── If no mutating tools and no state machine trigger → break loop
        (normal conversational response, nothing to verify)
```

### What Counts as a "Mutating Tool"

The state machine only activates if mutating tools were used. "Mutating" means
the tool changed something in the world:

```python
_MUTATING_PREFIXES = (
    "lucy_create_",
    "lucy_modify_",
    "lucy_delete_",
    "lucy_exec_",
    "lucy_execute_",
    "lucy_spaces_",
    "lucy_send_",
    "lucy_write_",
    "lucy_edit_",
    "lucy_start_",
    "COMPOSIO_MULTI_EXECUTE_TOOL",
    "COMPOSIO_REMOTE_WORKBENCH_TOOL",
)
```

Read-only tools (`lucy_list_crons`, `lucy_search_slack_history`, `lucy_web_search`)
do not trigger the testing phase.

---

## The Retry Depth System

**File:** `src/lucy/core/agent.py` — `run()`

Above the state machine, there is a separate outer retry system. If the agent
loop itself fails catastrophically (exception, timeout, empty result), `run()`
can call itself recursively up to `_MAX_RETRY_DEPTH = 3` times:

| Retry Depth | What Happens | Model Used |
|-------------|-------------|------------|
| 0 (first attempt) | Normal execution | Router-selected |
| 1 (first retry) | Failure context injected + frontier model forced | Frontier |
| 2 (second retry) | "Previous two attempts failed, try a completely different approach" | Frontier |
| 3 (third retry) | "All approaches have failed, report the specific blocker clearly" | Frontier |

This is distinct from the test-fix state machine. The state machine handles
controlled test failures within a single execution. The retry depth handles
uncontrolled failures (exceptions, timeouts, zero output).

---

## What the Gap Was Before This Framework

To summarize precisely what the testing suite confirmed was **broken before**
and **fixed now**:

| Issue | Before | After |
|-------|--------|-------|
| Modifying a cron type (agent ↔ script) | No tool parameters — LLM fabricated completion | `new_type` + `script_code` parameters added; atomic one-call operation |
| Writing a cron script | No supported path — LLM hallucinated "Done" | `script_code` in `lucy_modify_cron` + `lucy_create_cron` writes the file |
| Script syntax errors | Silent until scheduled run (potentially hours later) | `ast.parse()` rejects on tool call, returns actionable error |
| Script import/runtime errors | Silent until scheduled run | `_dry_run_script()` subprocess catches before saving |
| Verification hint for modify_cron | "Call `lucy_list_crons`" (only checks it exists, not that it runs) | "Call `lucy_list_crons`, then `lucy_trigger_cron`" (confirms actual execution) |
| Ambiguous "Done." after completing work | Accepted as task completion | Treated as test failure — LLM forced to be explicit |
| Failed test, no retry | One-shot nudge, could be ignored | Structured FIXING phase with up to 2 fix cycles |
| Failed after 2 fixes | Task marked done anyway | Escalated to frontier model for rebuild |
| Failed on frontier | Task marked done anyway | 2 completely different approaches attempted |
| All approaches failed | No clear user communication | Structured `GAVE_UP` phase: user told exactly what failed and why |

---

## Test Coverage

**File:** `tests/test_test_fix_retry.py`

The framework has 75 tests across 7 test classes:

| Test Class | Count | What It Tests |
|------------|-------|---------------|
| `TestResponseIndicatesTestPass` | 29 | Pass signals, fail signals, ambiguous responses, fail-beats-pass priority |
| `TestTestFixStateDataclass` | 9 | Initial state, all phases exist, transition mechanics |
| `TestStateMachineLogic` | 11 | Full escalation ladder simulation, early pass, failure log accumulation |
| `TestConfigThresholds` | 7 | All 3 settings present, correct default values, are positive |
| `TestScriptValidation` | 5 | Valid scripts parse, syntax errors caught, complex scripts pass |
| `TestDryRunScript` | 7 | Valid passes, exit(1) fails, RuntimeError fails, ImportError fails, `LUCY_DRY_RUN` confirmed, nonexistent file fails, infinite loop times out gracefully |
| `TestBuildVerifyHints` | 7 | Correct hints per tool category, unknown tools get generic hint, multiple tools combined |

Run with:

```bash
pytest tests/test_test_fix_retry.py -v
```

Expected: **75 passed**.

---

## Summary

Lucy's test-fix-retry framework ensures that no task is marked complete without
live verification. The guarantee is:

1. **If you mutate something, you must test it.** The state machine enforces this
   — text-only responses after a mutating tool call are not accepted as completion.

2. **If the test fails, you get multiple chances to fix it.** Two fix cycles at the
   default model, two more at the frontier model, and two completely different
   approaches — nine total chances before the framework gives up.

3. **Scripts are validated before they're even saved.** AST syntax checking and
   subprocess dry-runs run synchronously as part of the tool call. A broken script
   cannot reach the cron scheduler.

4. **"Done" without a TEST PASSED signal means "try again".** The ambiguity
   detector treats any non-explicit response as a failure, forcing the LLM to
   produce a concrete, testable result.

5. **When everything fails, Lucy tells you the truth.** The `GAVE_UP` phase is
   not a silent error. Lucy reports what was tried, what the specific blocker is,
   and what would be needed to solve it.
