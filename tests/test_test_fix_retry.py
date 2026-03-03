"""Tests for the Universal Test-Fix-Retry Framework.

Covers:
1. response_indicates_test_pass() heuristic — all signal categories
2. _TestFixState dataclass initialization and transitions
3. State machine phase logic — BUILDING → TESTING → FIXING → ESCALATED → REAPPROACH → GAVE_UP / PASSED
4. config.py thresholds are present
5. Script validation (ast.parse) in scheduler
6. Dry-run returns ok=True for valid scripts, ok=False for broken ones
"""

from __future__ import annotations

import ast
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lucy.core.agent import _TFPhase, _TestFixState
from lucy.core.quality import response_indicates_test_pass
from lucy.config import settings


# ─────────────────────────────────────────────────────────────────────────────
# 1. response_indicates_test_pass — positive signals
# ─────────────────────────────────────────────────────────────────────────────

class TestResponseIndicatesTestPass:

    def test_explicit_test_passed_prefix(self):
        ok, reason = response_indicates_test_pass("TEST PASSED: Cron ran successfully.")
        assert ok is True
        assert reason == ""

    def test_explicit_tests_passed_plural(self):
        ok, reason = response_indicates_test_pass("TESTS PASSED: all 3 checks completed.")
        assert ok is True

    def test_verification_passed(self):
        ok, _ = response_indicates_test_pass("VERIFICATION PASSED: endpoint returned 200.")
        assert ok is True

    def test_verified_keyword(self):
        ok, _ = response_indicates_test_pass("The deployment is verified and running correctly.")
        assert ok is True

    def test_working_correctly(self):
        ok, _ = response_indicates_test_pass("The cron is working correctly.")
        assert ok is True

    def test_confirmed_working(self):
        ok, _ = response_indicates_test_pass("Confirmed working — booth monitor is active.")
        assert ok is True

    def test_successfully_triggered(self):
        ok, _ = response_indicates_test_pass("Successfully triggered the cron, no errors.")
        assert ok is True

    def test_exited_code_zero(self):
        ok, _ = response_indicates_test_pass("Script exited with code 0.")
        assert ok is True

    def test_no_errors_found(self):
        ok, _ = response_indicates_test_pass("Ran the script — no errors found.")
        assert ok is True

    def test_case_insensitive_pass(self):
        ok, _ = response_indicates_test_pass("test passed: script ran cleanly")
        assert ok is True

    # ── Negative: explicit failures ──────────────────────────────────────────

    def test_explicit_test_failed_prefix(self):
        ok, reason = response_indicates_test_pass("TEST FAILED: ModuleNotFoundError: pymongo")
        assert ok is False
        assert "pymongo" in reason or "failure" in reason

    def test_traceback_detected(self):
        text = "Traceback (most recent call last):\n  File 'x.py', line 5\nImportError: no module"
        ok, reason = response_indicates_test_pass(text)
        assert ok is False
        assert "failure" in reason

    def test_module_not_found(self):
        ok, reason = response_indicates_test_pass("ModuleNotFoundError: No module named 'pymongo'")
        assert ok is False

    def test_syntax_error(self):
        ok, reason = response_indicates_test_pass("SyntaxError: invalid syntax on line 3")
        assert ok is False

    def test_exit_code_nonzero(self):
        ok, _ = response_indicates_test_pass("Script exited with code 1.")
        assert ok is False

    def test_exit_code_2(self):
        ok, _ = response_indicates_test_pass("Process exited with code 2 — permission denied.")
        assert ok is False

    def test_http_500(self):
        ok, _ = response_indicates_test_pass("Got HTTP 500 from the endpoint.")
        assert ok is False

    def test_http_404(self):
        ok, _ = response_indicates_test_pass("Deployment check returned HTTP 404.")
        assert ok is False

    def test_connection_refused(self):
        ok, _ = response_indicates_test_pass("Connection refused when connecting to MongoDB.")
        assert ok is False

    def test_failed_to_run(self):
        ok, _ = response_indicates_test_pass("Failed to run the script due to missing dependency.")
        assert ok is False

    def test_did_not_work(self):
        ok, _ = response_indicates_test_pass("The cron did not work as expected.")
        assert ok is False

    def test_not_working(self):
        ok, _ = response_indicates_test_pass("Something is not working here.")
        assert ok is False

    def test_fail_beats_pass_signal(self):
        """If a response has both pass and fail signals, fail wins."""
        text = "TEST PASSED: triggered — but got Traceback (most recent call last): ImportError"
        ok, _ = response_indicates_test_pass(text)
        assert ok is False

    # ── Ambiguous: neither explicit pass nor fail ─────────────────────────────

    def test_created_cron_ambiguous(self):
        ok, reason = response_indicates_test_pass("Created the cron successfully.")
        assert ok is False
        assert "ambiguous" in reason

    def test_set_up_ambiguous(self):
        ok, reason = response_indicates_test_pass("Set up the booth monitor.")
        assert ok is False
        assert "ambiguous" in reason

    def test_updated_schedule_ambiguous(self):
        ok, reason = response_indicates_test_pass("Updated the schedule to every 2 minutes.")
        assert ok is False
        assert "ambiguous" in reason

    def test_empty_string_ambiguous(self):
        ok, reason = response_indicates_test_pass("")
        assert ok is False
        assert "empty" in reason

    def test_done_ambiguous(self):
        ok, reason = response_indicates_test_pass("Done.")
        assert ok is False
        assert "ambiguous" in reason

    def test_setup_complete_ambiguous(self):
        ok, reason = response_indicates_test_pass(
            "Switched to lightweight heartbeat. Here's what it does..."
        )
        assert ok is False
        assert "ambiguous" in reason


# ─────────────────────────────────────────────────────────────────────────────
# 2. _TestFixState dataclass
# ─────────────────────────────────────────────────────────────────────────────

class TestTestFixStateDataclass:

    def test_initial_phase_is_building(self):
        s = _TestFixState()
        assert s.phase == _TFPhase.BUILDING

    def test_initial_fix_attempts_zero(self):
        s = _TestFixState()
        assert s.fix_attempts == 0

    def test_initial_not_escalated(self):
        s = _TestFixState()
        assert s.escalated is False

    def test_initial_approach_attempts_zero(self):
        s = _TestFixState()
        assert s.approach_attempts == 0

    def test_failure_log_starts_empty(self):
        s = _TestFixState()
        assert s.failure_log == []

    def test_mutating_tools_starts_empty(self):
        s = _TestFixState()
        assert s.mutating_tools == []

    def test_phase_transition(self):
        s = _TestFixState()
        s.phase = _TFPhase.TESTING
        assert s.phase == _TFPhase.TESTING

    def test_failure_log_append(self):
        s = _TestFixState()
        s.failure_log.append("some error")
        assert len(s.failure_log) == 1
        assert s.failure_log[0] == "some error"

    def test_all_phases_exist(self):
        phases = {p.value for p in _TFPhase}
        assert "building" in phases
        assert "testing" in phases
        assert "fixing" in phases
        assert "escalated" in phases
        assert "reapproach" in phases
        assert "passed" in phases
        assert "gave_up" in phases


# ─────────────────────────────────────────────────────────────────────────────
# 3. State machine logic simulation
# ─────────────────────────────────────────────────────────────────────────────

class TestStateMachineLogic:
    """Test the state machine decision logic without running the full agent."""

    def _simulate_test_evaluation(
        self,
        state: _TestFixState,
        response: str,
        max_default: int = 2,
        max_frontier: int = 2,
        max_approaches: int = 2,
    ) -> str:
        """Simulate one TESTING evaluation step. Returns the next action name."""
        passed, reason = response_indicates_test_pass(response)

        if passed:
            state.phase = _TFPhase.PASSED
            return "PASSED"

        state.failure_log.append(reason)
        _max_fixes = max_frontier if state.escalated else max_default

        if state.fix_attempts < _max_fixes:
            state.phase = _TFPhase.FIXING
            state.fix_attempts += 1
            return "FIX"

        if not state.escalated:
            state.phase = _TFPhase.ESCALATED
            state.escalated = True
            state.fix_attempts = 0
            return "ESCALATE"

        if state.approach_attempts < max_approaches:
            state.phase = _TFPhase.REAPPROACH
            state.fix_attempts = 0
            state.approach_attempts += 1
            return "REAPPROACH"

        state.phase = _TFPhase.GAVE_UP
        return "GAVE_UP"

    def test_immediate_pass(self):
        state = _TestFixState()
        state.phase = _TFPhase.TESTING
        action = self._simulate_test_evaluation(state, "TEST PASSED: all good.")
        assert action == "PASSED"
        assert state.phase == _TFPhase.PASSED

    def test_fail_triggers_fix(self):
        state = _TestFixState()
        state.phase = _TFPhase.TESTING
        action = self._simulate_test_evaluation(state, "TEST FAILED: pymongo not found.")
        assert action == "FIX"
        assert state.phase == _TFPhase.FIXING
        assert state.fix_attempts == 1

    def test_second_fail_triggers_second_fix(self):
        state = _TestFixState()
        state.phase = _TFPhase.TESTING
        state.fix_attempts = 1  # already used one fix
        action = self._simulate_test_evaluation(state, "TEST FAILED: still broken.")
        assert action == "FIX"
        assert state.fix_attempts == 2

    def test_exhausted_default_fixes_triggers_escalation(self):
        state = _TestFixState()
        state.phase = _TFPhase.TESTING
        state.fix_attempts = 2  # budget exhausted for default model
        action = self._simulate_test_evaluation(state, "TEST FAILED: still broken.")
        assert action == "ESCALATE"
        assert state.escalated is True
        assert state.fix_attempts == 0  # reset after escalation

    def test_escalated_fix_budget(self):
        state = _TestFixState()
        state.phase = _TFPhase.TESTING
        state.escalated = True
        state.fix_attempts = 1  # one frontier fix used
        action = self._simulate_test_evaluation(state, "TEST FAILED: still broken.")
        assert action == "FIX"  # still has 1 more frontier fix

    def test_exhausted_frontier_fixes_triggers_reapproach(self):
        state = _TestFixState()
        state.phase = _TFPhase.TESTING
        state.escalated = True
        state.fix_attempts = 2  # frontier budget exhausted
        action = self._simulate_test_evaluation(state, "TEST FAILED: still broken.")
        assert action == "REAPPROACH"
        assert state.approach_attempts == 1
        assert state.fix_attempts == 0  # reset

    def test_second_approach_attempt(self):
        state = _TestFixState()
        state.phase = _TFPhase.TESTING
        state.escalated = True
        state.fix_attempts = 2
        state.approach_attempts = 1  # one approach already tried
        action = self._simulate_test_evaluation(state, "TEST FAILED: still broken.")
        assert action == "REAPPROACH"
        assert state.approach_attempts == 2

    def test_all_approaches_exhausted_gives_up(self):
        state = _TestFixState()
        state.phase = _TFPhase.TESTING
        state.escalated = True
        state.fix_attempts = 2
        state.approach_attempts = 2  # all approaches tried
        action = self._simulate_test_evaluation(state, "TEST FAILED: still broken.")
        assert action == "GAVE_UP"
        assert state.phase == _TFPhase.GAVE_UP

    def test_full_escalation_path(self):
        """Walk through the complete happy-sad path to give up.

        After each REAPPROACH, fix_attempts resets to 0, so the agent gets
        another full default-tier fix budget (2 more cycles) before the next
        REAPPROACH trigger. The total ladder is:
          2 default fixes → ESCALATE → 2 frontier fixes → REAPPROACH 1
          → 2 more fixes → REAPPROACH 2 → 2 more fixes → GAVE_UP
        """
        state = _TestFixState()
        state.phase = _TFPhase.TESTING

        fail_response = "TEST FAILED: connection refused."

        # Default-model fix budget: 2 rounds
        assert self._simulate_test_evaluation(state, fail_response) == "FIX"
        state.phase = _TFPhase.TESTING
        assert self._simulate_test_evaluation(state, fail_response) == "FIX"
        state.phase = _TFPhase.TESTING

        # Budget exhausted → ESCALATE to frontier, fix_attempts resets to 0
        assert self._simulate_test_evaluation(state, fail_response) == "ESCALATE"
        state.phase = _TFPhase.TESTING

        # Frontier-model fix budget: 2 rounds
        assert self._simulate_test_evaluation(state, fail_response) == "FIX"
        state.phase = _TFPhase.TESTING
        assert self._simulate_test_evaluation(state, fail_response) == "FIX"
        state.phase = _TFPhase.TESTING

        # Frontier budget exhausted → REAPPROACH 1, fix_attempts resets to 0
        assert self._simulate_test_evaluation(state, fail_response) == "REAPPROACH"
        state.phase = _TFPhase.TESTING

        # After REAPPROACH, a fresh fix budget (frontier tier) applies
        assert self._simulate_test_evaluation(state, fail_response) == "FIX"
        state.phase = _TFPhase.TESTING
        assert self._simulate_test_evaluation(state, fail_response) == "FIX"
        state.phase = _TFPhase.TESTING

        # Budget exhausted again → REAPPROACH 2 (approach_attempts=2), fix_attempts resets
        assert self._simulate_test_evaluation(state, fail_response) == "REAPPROACH"
        state.phase = _TFPhase.TESTING

        # After REAPPROACH 2, another fresh fix budget
        assert self._simulate_test_evaluation(state, fail_response) == "FIX"
        state.phase = _TFPhase.TESTING
        assert self._simulate_test_evaluation(state, fail_response) == "FIX"
        state.phase = _TFPhase.TESTING

        # All approaches exhausted → GAVE_UP
        assert self._simulate_test_evaluation(state, fail_response) == "GAVE_UP"
        assert state.phase == _TFPhase.GAVE_UP

    def test_pass_after_one_fix(self):
        """Pass on first fix attempt."""
        state = _TestFixState()
        state.phase = _TFPhase.TESTING

        # First test: fail
        self._simulate_test_evaluation(state, "TEST FAILED: error.")
        state.phase = _TFPhase.TESTING

        # After fix, pass
        action = self._simulate_test_evaluation(state, "TEST PASSED: all good.")
        assert action == "PASSED"

    def test_failure_log_accumulated(self):
        state = _TestFixState()
        state.phase = _TFPhase.TESTING

        self._simulate_test_evaluation(state, "TEST FAILED: error 1.")
        state.phase = _TFPhase.TESTING
        self._simulate_test_evaluation(state, "TEST FAILED: error 2.")

        assert len(state.failure_log) == 2


# ─────────────────────────────────────────────────────────────────────────────
# 4. config.py thresholds
# ─────────────────────────────────────────────────────────────────────────────

class TestConfigThresholds:

    def test_max_attempts_default_exists(self):
        assert hasattr(settings, "test_fix_max_attempts_default")

    def test_max_attempts_default_value(self):
        assert settings.test_fix_max_attempts_default == 2

    def test_max_attempts_frontier_exists(self):
        assert hasattr(settings, "test_fix_max_attempts_frontier")

    def test_max_attempts_frontier_value(self):
        assert settings.test_fix_max_attempts_frontier == 2

    def test_max_approaches_exists(self):
        assert hasattr(settings, "test_fix_max_approaches")

    def test_max_approaches_value(self):
        assert settings.test_fix_max_approaches == 2

    def test_thresholds_are_positive(self):
        assert settings.test_fix_max_attempts_default > 0
        assert settings.test_fix_max_attempts_frontier > 0
        assert settings.test_fix_max_approaches > 0


# ─────────────────────────────────────────────────────────────────────────────
# 5. Script validation (ast.parse) in scheduler
# ─────────────────────────────────────────────────────────────────────────────

class TestScriptValidation:

    def test_valid_script_parses(self):
        code = "import os\nprint(os.environ.get('WORKSPACE_ID', ''))\n"
        ast.parse(code)  # should not raise

    def test_syntax_error_detected(self):
        code = "def broken(:\n    pass\n"
        with pytest.raises(SyntaxError):
            ast.parse(code)

    def test_incomplete_function_detected(self):
        code = "def foo(\n"
        with pytest.raises(SyntaxError):
            ast.parse(code)

    def test_empty_script_is_valid(self):
        ast.parse("")

    def test_complex_script_parses(self):
        code = """
import json
import os
from datetime import datetime

WORKSPACE_ID = os.environ.get('WORKSPACE_ID', '')

def check_booths():
    results = []
    for i in range(5):
        results.append({'booth': i, 'online': True})
    return results

if __name__ == '__main__':
    data = check_booths()
    print(json.dumps(data))
"""
        ast.parse(code)  # should not raise


# ─────────────────────────────────────────────────────────────────────────────
# 6. Dry-run function
# ─────────────────────────────────────────────────────────────────────────────

class TestDryRunScript:

    @pytest.mark.asyncio
    async def test_valid_hello_world_passes(self):
        from lucy.crons.scheduler import _dry_run_script

        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write('print("hello")\n')
            path = f.name

        result = await _dry_run_script(path)
        assert result.get("ok") is True

    @pytest.mark.asyncio
    async def test_exit_1_fails(self):
        from lucy.crons.scheduler import _dry_run_script
        import sys

        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write("import sys\nsys.exit(1)\n")
            path = f.name

        result = await _dry_run_script(path)
        assert result.get("ok") is False
        assert "1" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_runtime_error_fails(self):
        from lucy.crons.scheduler import _dry_run_script

        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write("raise RuntimeError('boom')\n")
            path = f.name

        result = await _dry_run_script(path)
        assert result.get("ok") is False

    @pytest.mark.asyncio
    async def test_import_error_fails(self):
        from lucy.crons.scheduler import _dry_run_script

        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write("import this_module_definitely_does_not_exist_xyz\n")
            path = f.name

        result = await _dry_run_script(path)
        assert result.get("ok") is False

    @pytest.mark.asyncio
    async def test_dry_run_env_var_set(self):
        """LUCY_DRY_RUN=1 must be set so scripts can skip side effects."""
        from lucy.crons.scheduler import _dry_run_script

        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            # Script checks that LUCY_DRY_RUN is set and exits 1 if not
            f.write(
                "import os, sys\n"
                "if not os.environ.get('LUCY_DRY_RUN'):\n"
                "    sys.exit(2)\n"
                "print('dry run confirmed')\n"
            )
            path = f.name

        result = await _dry_run_script(path)
        assert result.get("ok") is True, f"LUCY_DRY_RUN was not set: {result}"

    @pytest.mark.asyncio
    async def test_nonexistent_file_fails(self):
        from lucy.crons.scheduler import _dry_run_script

        result = await _dry_run_script("/tmp/this_file_does_not_exist_xyz.py")
        # Either FileNotFoundError or a non-zero exit — either way ok=False
        assert result.get("ok") is False

    @pytest.mark.asyncio
    async def test_long_running_script_times_out_gracefully(self):
        """Scripts that block (e.g. waiting for I/O) should time out gracefully."""
        from lucy.crons.scheduler import _dry_run_script

        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            # Infinite loop — should hit the dry-run timeout
            f.write("import time\nwhile True:\n    time.sleep(0.1)\n")
            path = f.name

        result = await _dry_run_script(path)
        # The dry-run timeout returns ok=True with a note (syntax is still valid)
        assert result.get("ok") is True
        assert "timed out" in result.get("note", "").lower()


# ─────────────────────────────────────────────────────────────────────────────
# 7. Tool result verification hints
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildVerifyHints:
    """Test that _build_verify_hints returns the right hints for each tool category."""

    def _get_hints(self, tools: list[str]) -> str:
        from lucy.core.agent import LucyAgent
        return LucyAgent._build_verify_hints(tools)

    def test_cron_hint_mentions_list_crons(self):
        hints = self._get_hints(["lucy_create_cron"])
        assert "lucy_list_crons" in hints

    def test_cron_hint_mentions_trigger_cron(self):
        hints = self._get_hints(["lucy_modify_cron"])
        assert "lucy_trigger_cron" in hints

    def test_heartbeat_hint_mentions_list_heartbeats(self):
        hints = self._get_hints(["lucy_create_heartbeat"])
        assert "lucy_list_heartbeats" in hints

    def test_exec_command_hint_mentions_errors(self):
        hints = self._get_hints(["lucy_exec_command"])
        assert "error" in hints.lower()

    def test_deploy_hint_mentions_url(self):
        hints = self._get_hints(["lucy_spaces_deploy"])
        assert "url" in hints.lower() or "deploy" in hints.lower()

    def test_unknown_tool_returns_generic_hint(self):
        hints = self._get_hints(["lucy_some_unknown_tool"])
        assert len(hints) > 0  # always returns something

    def test_multiple_tools_combined(self):
        hints = self._get_hints(["lucy_create_cron", "lucy_exec_command"])
        assert "lucy_list_crons" in hints
        assert "error" in hints.lower()
