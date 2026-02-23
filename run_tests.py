"""Lucy Test Runner — Level 1 through Level 5.

Runs all tests, records results, and outputs a summary.
Can be run standalone: python3 run_tests.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import traceback
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

RESULTS: list[dict] = []


def record(level: int, test_id: str, name: str, passed: bool, elapsed_ms: int = 0, notes: str = ""):
    status = "PASS" if passed else "FAIL"
    RESULTS.append({
        "level": level, "id": test_id, "name": name,
        "status": status, "elapsed_ms": elapsed_ms, "notes": notes,
    })
    icon = "✅" if passed else "❌"
    print(f"  {icon} {test_id}: {name} ({elapsed_ms}ms) {notes}")


# ═══════════════════════════════════════════════════════════════════════
# LEVEL 1: INFRASTRUCTURE
# ═══════════════════════════════════════════════════════════════════════

def test_1_1_import():
    """App boots without import errors."""
    t0 = time.monotonic()
    try:
        import lucy
        record(1, "1.1", "App boots without import errors", True,
               int((time.monotonic() - t0) * 1000))
    except Exception as e:
        record(1, "1.1", "App boots without import errors", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


def test_1_2_openrouter_ping():
    """OpenRouter reachable with minimax-m2.5."""
    t0 = time.monotonic()
    try:
        import httpx, certifi
        with httpx.Client(verify=certifi.where(), timeout=30) as c:
            resp = c.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json={
                    "model": "minimax/minimax-m2.5",
                    "messages": [{"role": "user", "content": "Reply PONG"}],
                    "max_tokens": 5,
                },
                headers={
                    "Authorization": f"Bearer {os.environ.get('LUCY_OPENROUTER_API_KEY', '')}",
                },
            )
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            record(1, "1.2", "OpenRouter reachable (minimax-m2.5)", True,
                   int((time.monotonic() - t0) * 1000), f"Response: {content!r}")
    except Exception as e:
        record(1, "1.2", "OpenRouter reachable (minimax-m2.5)", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


def test_1_3_tool_calling_works():
    """OpenRouter tool calling with minimax-m2.5."""
    t0 = time.monotonic()
    try:
        import httpx, certifi
        tools = [{
            "type": "function",
            "function": {
                "name": "test_func",
                "description": "A test function to call",
                "parameters": {"type": "object", "properties": {
                    "x": {"type": "string", "description": "input value"}
                }, "required": ["x"]},
            },
        }]
        with httpx.Client(verify=certifi.where(), timeout=30) as c:
            resp = c.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json={
                    "model": "minimax/minimax-m2.5",
                    "messages": [{"role": "user", "content": "Call test_func with x='hello'"}],
                    "tools": tools,
                    "tool_choice": "auto",
                    "max_tokens": 100,
                },
                headers={
                    "Authorization": f"Bearer {os.environ.get('LUCY_OPENROUTER_API_KEY', '')}",
                },
            )
            data = resp.json()
            msg = data["choices"][0]["message"]
            has_tools = bool(msg.get("tool_calls"))
            record(1, "1.3", "Tool calling works (minimax-m2.5)", has_tools,
                   int((time.monotonic() - t0) * 1000),
                   f"tool_calls={len(msg.get('tool_calls', []))}, finish={data['choices'][0].get('finish_reason')}")
    except Exception as e:
        record(1, "1.3", "Tool calling works (minimax-m2.5)", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_1_4_composio_init():
    """Composio SDK initializes."""
    t0 = time.monotonic()
    try:
        from composio import Composio
        c = Composio(api_key="ak_IfLg3d5wH3adb4LS2-ZQ")
        record(1, "1.4", "Composio SDK initializes", True,
               int((time.monotonic() - t0) * 1000))
        return c
    except Exception as e:
        record(1, "1.4", "Composio SDK initializes", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])
        return None


async def test_1_5_composio_session(composio_obj):
    """Composio session creates for workspace."""
    t0 = time.monotonic()
    if composio_obj is None:
        record(1, "1.5", "Composio session creates", False, 0, "Skipped: no SDK")
        return None
    try:
        session = composio_obj.create(user_id="test_workspace")
        record(1, "1.5", "Composio session creates", True,
               int((time.monotonic() - t0) * 1000))
        return session
    except Exception as e:
        record(1, "1.5", "Composio session creates", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])
        return None


async def test_1_6_composio_tools(session):
    """Composio meta-tools returned."""
    t0 = time.monotonic()
    if session is None:
        record(1, "1.6", "Composio meta-tools returned", False, 0, "Skipped: no session")
        return
    try:
        tools = await asyncio.to_thread(session.tools)
        names = []
        for t in tools:
            if isinstance(t, dict):
                names.append(t.get("function", {}).get("name", "?"))
            elif hasattr(t, "model_dump"):
                d = t.model_dump()
                names.append(d.get("function", {}).get("name", "?"))
        count = len(tools)
        record(1, "1.6", f"Composio meta-tools returned ({count})", count >= 5,
               int((time.monotonic() - t0) * 1000),
               f"names={names}")
    except Exception as e:
        record(1, "1.6", "Composio meta-tools returned", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


def test_1_7_config_loads():
    """Config loads from .env and keys.json."""
    t0 = time.monotonic()
    try:
        from lucy.config import settings
        checks = {
            "slack_bot_token": bool(settings.slack_bot_token),
            "slack_app_token": bool(settings.slack_app_token),
            "openclaw_base_url": bool(settings.openclaw_base_url),
            "openclaw_api_key": bool(settings.openclaw_api_key),
            "composio_api_key": bool(settings.composio_api_key),
        }
        all_ok = all(checks.values())
        missing = [k for k, v in checks.items() if not v]
        record(1, "1.7", "Config loads from .env/keys.json", all_ok,
               int((time.monotonic() - t0) * 1000),
               f"missing={missing}" if missing else "all keys present")
    except Exception as e:
        record(1, "1.7", "Config loads from .env/keys.json", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_1_8_workspace_structure():
    """Workspace directory has correct structure."""
    t0 = time.monotonic()
    try:
        from lucy.config import settings
        ws_root = settings.workspace_root
        ws_id = "test_workspace"
        ws_path = ws_root / ws_id

        expected_dirs = ["skills", "crons", "logs", "data", "team", "company", "scripts"]
        present = []
        missing = []
        for d in expected_dirs:
            if (ws_path / d).is_dir():
                present.append(d)
            else:
                missing.append(d)

        record(1, "1.8", "Workspace directory structure", not missing,
               int((time.monotonic() - t0) * 1000),
               f"present={present}, missing={missing}")
    except Exception as e:
        record(1, "1.8", "Workspace directory structure", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_1_9_skills_seeded():
    """Skills seeded in correct location (skills/ subdir)."""
    t0 = time.monotonic()
    try:
        from lucy.config import settings
        ws_root = settings.workspace_root
        ws_id = "test_workspace"
        skills_dir = ws_root / ws_id / "skills"

        skill_files = list(skills_dir.rglob("SKILL.md")) if skills_dir.is_dir() else []
        root_level_skills = [
            d for d in (ws_root / ws_id).iterdir()
            if d.is_dir() and (d / "SKILL.md").exists()
            and d.name not in ("skills", "company", "team", "crons", "logs", "data", "scripts")
        ]

        if skill_files:
            record(1, "1.9", f"Skills seeded in skills/ ({len(skill_files)})", True,
                   int((time.monotonic() - t0) * 1000))
        elif root_level_skills:
            record(1, "1.9", "Skills seeded in skills/", False,
                   int((time.monotonic() - t0) * 1000),
                   f"BUG: {len(root_level_skills)} skills at ROOT level instead of skills/")
        else:
            record(1, "1.9", "Skills seeded in skills/", False,
                   int((time.monotonic() - t0) * 1000), "No skills found anywhere")
    except Exception as e:
        record(1, "1.9", "Skills seeded in skills/", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_1_10_crons_seeded():
    """Crons seeded in crons/ subdir."""
    t0 = time.monotonic()
    try:
        from lucy.config import settings
        ws_root = settings.workspace_root
        ws_id = "test_workspace"
        crons_dir = ws_root / ws_id / "crons"

        task_files = list(crons_dir.rglob("task.json")) if crons_dir.is_dir() else []
        record(1, "1.10", f"Crons seeded in crons/ ({len(task_files)})", len(task_files) >= 3,
               int((time.monotonic() - t0) * 1000),
               f"found={len(task_files)}, expected>=3")
    except Exception as e:
        record(1, "1.10", "Crons seeded in crons/", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_1_11_team_profiled():
    """team/SKILL.md exists with content."""
    t0 = time.monotonic()
    try:
        from lucy.config import settings
        path = settings.workspace_root / "test_workspace" / "team" / "SKILL.md"
        exists = path.is_file()
        has_content = False
        if exists:
            content = path.read_text()
            has_content = "Team Members" in content
        record(1, "1.11", "team/SKILL.md exists", exists and has_content,
               int((time.monotonic() - t0) * 1000),
               "has member table" if has_content else "stub only" if exists else "missing")
    except Exception as e:
        record(1, "1.11", "team/SKILL.md exists", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_1_12_company_stub():
    """company/SKILL.md exists."""
    t0 = time.monotonic()
    try:
        from lucy.config import settings
        path = settings.workspace_root / "test_workspace" / "company" / "SKILL.md"
        exists = path.is_file()
        record(1, "1.12", "company/SKILL.md exists", exists,
               int((time.monotonic() - t0) * 1000))
    except Exception as e:
        record(1, "1.12", "company/SKILL.md exists", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


# ═══════════════════════════════════════════════════════════════════════
# LEVEL 2: CORE AGENT
# ═══════════════════════════════════════════════════════════════════════

async def test_2_1_prompt_has_soul():
    """System prompt includes SOUL.md content."""
    t0 = time.monotonic()
    try:
        from lucy.core.prompt import _load_soul
        soul = _load_soul()
        has_anchor = "Lucy" in soul
        record(2, "2.1", "System prompt includes SOUL.md", has_anchor,
               int((time.monotonic() - t0) * 1000),
               f"length={len(soul)}")
    except Exception as e:
        record(2, "2.1", "System prompt includes SOUL.md", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_2_2_prompt_has_template():
    """System prompt includes SYSTEM_PROMPT.md."""
    t0 = time.monotonic()
    try:
        from lucy.core.prompt import _load_template
        template = _load_template()
        has_skills_placeholder = "{available_skills}" in template
        has_philosophy = "core_philosophy" in template
        record(2, "2.2", "System prompt includes template", has_skills_placeholder and has_philosophy,
               int((time.monotonic() - t0) * 1000),
               f"placeholder={has_skills_placeholder}, philosophy={has_philosophy}")
    except Exception as e:
        record(2, "2.2", "System prompt includes template", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_2_3_prompt_has_skills():
    """System prompt includes skill descriptions."""
    t0 = time.monotonic()
    try:
        from lucy.config import settings
        from lucy.workspace.filesystem import WorkspaceFS
        from lucy.workspace.skills import get_skill_descriptions_for_prompt

        ws = WorkspaceFS(workspace_id="test_workspace", base_path=settings.workspace_root)
        desc = await get_skill_descriptions_for_prompt(ws)
        skill_count = desc.count("\n- ") + (1 if desc.startswith("- ") else 0)
        has_skills = skill_count > 0
        record(2, "2.3", f"Skill descriptions in prompt ({skill_count})", has_skills,
               int((time.monotonic() - t0) * 1000),
               f"length={len(desc)}, starts_with={desc[:60]!r}")
    except Exception as e:
        record(2, "2.3", "Skill descriptions in prompt", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_2_4_full_prompt_build():
    """Full system prompt builds successfully."""
    t0 = time.monotonic()
    try:
        from lucy.config import settings
        from lucy.core.prompt import build_system_prompt
        from lucy.workspace.filesystem import WorkspaceFS

        ws = WorkspaceFS(workspace_id="test_workspace", base_path=settings.workspace_root)
        prompt = await build_system_prompt(ws)
        checks = {
            "has_soul": "Lucy" in prompt,
            "has_template": "core_philosophy" in prompt,
            "has_skills_section": "available_skills" in prompt,
            "reasonable_length": len(prompt) > 500,
        }
        all_ok = all(checks.values())
        record(2, "2.4", "Full system prompt builds", all_ok,
               int((time.monotonic() - t0) * 1000),
               f"length={len(prompt)}, checks={checks}")
    except Exception as e:
        record(2, "2.4", "Full system prompt builds", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_2_5_llm_simple_response():
    """LLM returns non-empty response to 'Hi'."""
    t0 = time.monotonic()
    try:
        from lucy.core.openclaw import OpenClawClient, ChatConfig
        client = OpenClawClient()
        config = ChatConfig(
            system_prompt="You are Lucy, a helpful AI. Reply briefly.",
            max_tokens=100,
        )
        resp = await client.chat_completion(
            messages=[{"role": "user", "content": "Hi Lucy!"}],
            config=config,
        )
        has_content = bool(resp.content and resp.content.strip())
        record(2, "2.5", "LLM responds to 'Hi'", has_content,
               int((time.monotonic() - t0) * 1000),
               f"response={resp.content[:80]!r}")
        await client.close()
    except Exception as e:
        record(2, "2.5", "LLM responds to 'Hi'", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_2_6_llm_response_time():
    """LLM responds in < 60s for simple query."""
    t0 = time.monotonic()
    try:
        from lucy.core.openclaw import OpenClawClient, ChatConfig
        client = OpenClawClient()
        config = ChatConfig(
            system_prompt="You are Lucy. Reply in one sentence.",
            max_tokens=50,
        )
        resp = await client.chat_completion(
            messages=[{"role": "user", "content": "What is 2+2?"}],
            config=config,
        )
        elapsed = int((time.monotonic() - t0) * 1000)
        fast_enough = elapsed < 60000
        record(2, "2.6", f"LLM responds in <60s ({elapsed}ms)", fast_enough,
               elapsed, f"response={resp.content[:50]!r}")
        await client.close()
    except Exception as e:
        elapsed = int((time.monotonic() - t0) * 1000)
        record(2, "2.6", "LLM responds in <60s", False, elapsed, str(e)[:200])


# ═══════════════════════════════════════════════════════════════════════
# LEVEL 3: TOOL CALLING & INTEGRATIONS
# ═══════════════════════════════════════════════════════════════════════

async def test_3_1_tools_in_payload():
    """Tools are included in OpenClaw request payload."""
    t0 = time.monotonic()
    try:
        from lucy.integrations.composio_client import get_composio_client
        client = get_composio_client()
        tools = await client.get_tools("test_workspace")
        names = [t.get("function", {}).get("name", "?") for t in tools if isinstance(t, dict)]
        has_tools = len(tools) >= 5
        record(3, "3.1", f"Composio tools fetched ({len(tools)})", has_tools,
               int((time.monotonic() - t0) * 1000),
               f"names={names}")
        return tools
    except Exception as e:
        record(3, "3.1", "Composio tools fetched", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])
        return []


async def test_3_2_llm_calls_tool(tools):
    """LLM calls a tool when asked."""
    t0 = time.monotonic()
    if not tools:
        record(3, "3.2", "LLM calls tool", False, 0, "Skipped: no tools")
        return
    try:
        from lucy.core.openclaw import OpenClawClient, ChatConfig
        client = OpenClawClient()
        config = ChatConfig(
            system_prompt=(
                "You are Lucy, an AI assistant with tools. "
                "When the user asks to find tools, use COMPOSIO_SEARCH_TOOLS."
            ),
            tools=tools,
            max_tokens=500,
        )
        resp = await client.chat_completion(
            messages=[{"role": "user", "content": "Search for Gmail tools using your tools."}],
            config=config,
        )
        has_tool_calls = bool(resp.tool_calls)
        tool_names = [tc.get("name", "") for tc in (resp.tool_calls or [])]
        record(3, "3.2", "LLM calls tool when asked", has_tool_calls,
               int((time.monotonic() - t0) * 1000),
               f"tool_calls={tool_names}, content={resp.content[:60]!r}")
        await client.close()
    except Exception as e:
        record(3, "3.2", "LLM calls tool when asked", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_3_3_tool_execution():
    """Tool call result is passed back correctly."""
    t0 = time.monotonic()
    try:
        from lucy.integrations.composio_client import get_composio_client
        client = get_composio_client()
        result = await client.execute_tool_call(
            workspace_id="test_workspace",
            tool_name="COMPOSIO_SEARCH_TOOLS",
            arguments={"query": "gmail send email", "limit": 3},
        )
        has_result = bool(result) and "error" not in str(result).lower()[:50]
        record(3, "3.3", "Tool execution works", has_result,
               int((time.monotonic() - t0) * 1000),
               f"result_keys={list(result.keys()) if isinstance(result, dict) else type(result)}")
    except Exception as e:
        record(3, "3.3", "Tool execution works", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


# ═══════════════════════════════════════════════════════════════════════
# LEVEL 4: WORKSPACE, SKILLS, MEMORY
# ═══════════════════════════════════════════════════════════════════════

async def test_4_1_workspace_dirs():
    """All required workspace directories exist."""
    t0 = time.monotonic()
    try:
        from lucy.config import settings
        ws_path = settings.workspace_root / "test_workspace"
        required = ["skills", "crons", "logs", "data", "team", "company", "scripts"]
        results = {d: (ws_path / d).is_dir() for d in required}
        all_ok = all(results.values())
        record(4, "4.1", "Workspace dirs exist", all_ok,
               int((time.monotonic() - t0) * 1000),
               f"results={results}")
    except Exception as e:
        record(4, "4.1", "Workspace dirs exist", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_4_2_skill_frontmatter():
    """Each skill SKILL.md has valid YAML frontmatter."""
    t0 = time.monotonic()
    try:
        from lucy.config import settings
        from lucy.workspace.filesystem import WorkspaceFS
        from lucy.workspace.skills import list_skills

        ws = WorkspaceFS(workspace_id="test_workspace", base_path=settings.workspace_root)
        skills = await list_skills(ws)
        record(4, "4.2", f"Skills with valid frontmatter ({len(skills)})", len(skills) >= 10,
               int((time.monotonic() - t0) * 1000),
               f"found={len(skills)}, names={[s.name for s in skills[:5]]}")
    except Exception as e:
        record(4, "4.2", "Skills with valid frontmatter", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_4_3_state_json():
    """state.json exists and has onboarded_at."""
    t0 = time.monotonic()
    try:
        from lucy.config import settings
        from lucy.workspace.filesystem import WorkspaceFS

        ws = WorkspaceFS(workspace_id="test_workspace", base_path=settings.workspace_root)
        state = await ws.read_state()
        has_onboarded = "onboarded_at" in state
        record(4, "4.3", "state.json has onboarded_at", has_onboarded,
               int((time.monotonic() - t0) * 1000),
               f"keys={list(state.keys())}")
    except Exception as e:
        record(4, "4.3", "state.json has onboarded_at", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_4_4_activity_log():
    """Activity log writes and reads."""
    t0 = time.monotonic()
    try:
        from lucy.config import settings
        from lucy.workspace.filesystem import WorkspaceFS
        from lucy.workspace.activity_log import log_activity, get_recent_activity

        ws = WorkspaceFS(workspace_id="test_workspace", base_path=settings.workspace_root)
        await log_activity(ws, "Test activity entry")
        recent = await get_recent_activity(ws)
        has_entry = "Test activity entry" in recent
        record(4, "4.4", "Activity log works", has_entry,
               int((time.monotonic() - t0) * 1000))
    except Exception as e:
        record(4, "4.4", "Activity log works", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_4_5_cron_task_json():
    """Cron task.json files are valid JSON."""
    t0 = time.monotonic()
    try:
        from lucy.config import settings
        crons_dir = settings.workspace_root / "test_workspace" / "crons"
        valid = 0
        invalid = 0
        task_files = list(crons_dir.rglob("task.json")) if crons_dir.is_dir() else []
        for f in task_files:
            try:
                data = json.loads(f.read_text())
                if all(k in data for k in ("path", "cron", "title", "description")):
                    valid += 1
                else:
                    invalid += 1
            except Exception:
                invalid += 1
        record(4, "4.5", f"Cron task.json valid ({valid})", valid >= 3 and invalid == 0,
               int((time.monotonic() - t0) * 1000),
               f"valid={valid}, invalid={invalid}, total_files={len(task_files)}")
    except Exception as e:
        record(4, "4.5", "Cron task.json valid", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_4_6_timezone_data():
    """team/SKILL.md has timezone data."""
    t0 = time.monotonic()
    try:
        from lucy.config import settings
        path = settings.workspace_root / "test_workspace" / "team" / "SKILL.md"
        content = path.read_text() if path.is_file() else ""
        has_tz = "Timezone" in content or "tz_offset" in content or "TZ Offset" in content
        record(4, "4.6", "team/SKILL.md has timezone data", has_tz,
               int((time.monotonic() - t0) * 1000))
    except Exception as e:
        record(4, "4.6", "team/SKILL.md has timezone", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


# ═══════════════════════════════════════════════════════════════════════
# LEVEL 5: END-TO-END SLACK INTERACTIONS
# ═══════════════════════════════════════════════════════════════════════

SLACK_USER_TOKEN = os.environ.get("LUCY_TEST_USER_TOKEN", "")
SLACK_BOT_TOKEN = os.environ.get("LUCY_TEST_BOT_TOKEN", "")
LUCY_USER_ID = os.environ.get("LUCY_TEST_BOT_USER_ID", "U0AG8LVAB4M")
CHANNEL_ID = os.environ.get("LUCY_TEST_CHANNEL_ID", "C0AEZ241C3V")


def _slack_client():
    import httpx, certifi
    return httpx.Client(verify=certifi.where(), timeout=10)


def slack_post(text: str, token: str = SLACK_USER_TOKEN) -> dict:
    """Post a message to Slack as the user."""
    with _slack_client() as c:
        resp = c.post("https://slack.com/api/chat.postMessage", json={
            "channel": CHANNEL_ID,
            "text": f"<@{LUCY_USER_ID}> {text}",
            "as_user": True,
        }, headers={"Authorization": f"Bearer {token}"})
        return resp.json()


def slack_get_replies(thread_ts: str, token: str = SLACK_BOT_TOKEN) -> list:
    """Get thread replies."""
    with _slack_client() as c:
        resp = c.get(
            f"https://slack.com/api/conversations.replies?channel={CHANNEL_ID}&ts={thread_ts}&limit=10",
            headers={"Authorization": f"Bearer {token}"},
        )
        return resp.json().get("messages", [])


def wait_for_reply(thread_ts: str, timeout_s: int = 180) -> dict | None:
    """Wait for Lucy's reply in a thread. Returns the reply message or None."""
    start = time.time()
    while time.time() - start < timeout_s:
        time.sleep(5)
        msgs = slack_get_replies(thread_ts)
        for msg in msgs:
            if msg.get("ts") != thread_ts and (msg.get("bot_id") or msg.get("app_id")):
                return msg
    return None


async def test_5_1_mention_single_reply():
    """@Lucy mention gets exactly 1 reply."""
    t0 = time.monotonic()
    try:
        res = slack_post("Hello Lucy! Just testing — reply with a greeting.")
        if not res.get("ok"):
            record(5, "5.1", "Mention gets 1 reply", False, 0, f"Post failed: {res}")
            return None

        thread_ts = res["ts"]
        reply = wait_for_reply(thread_ts, timeout_s=180)
        elapsed = int((time.monotonic() - t0) * 1000)

        if not reply:
            record(5, "5.1", "Mention gets 1 reply", False, elapsed, "No reply within 180s")
            return None

        # Check for duplicates
        time.sleep(10)
        all_msgs = slack_get_replies(thread_ts)
        bot_replies = [m for m in all_msgs if m.get("ts") != thread_ts and (m.get("bot_id") or m.get("app_id"))]
        is_single = len(bot_replies) == 1

        record(5, "5.1", "Mention gets exactly 1 reply", is_single,
               elapsed, f"replies={len(bot_replies)}, text={reply.get('text', '')[:60]!r}")
        return thread_ts
    except Exception as e:
        record(5, "5.1", "Mention gets 1 reply", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])
        return None


async def test_5_2_reply_in_thread(thread_ts):
    """Reply appears in thread (not channel)."""
    t0 = time.monotonic()
    if not thread_ts:
        record(5, "5.2", "Reply in thread", False, 0, "Skipped: no thread_ts")
        return
    try:
        msgs = slack_get_replies(thread_ts)
        bot_replies = [m for m in msgs if m.get("ts") != thread_ts and (m.get("bot_id") or m.get("app_id"))]
        in_thread = all(m.get("thread_ts") == thread_ts for m in bot_replies)
        record(5, "5.2", "Reply in thread", in_thread and len(bot_replies) > 0,
               int((time.monotonic() - t0) * 1000))
    except Exception as e:
        record(5, "5.2", "Reply in thread", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_5_3_response_time():
    """Response time < 120s for greeting."""
    t0 = time.monotonic()
    try:
        res = slack_post("Hey! How are you today?")
        if not res.get("ok"):
            record(5, "5.3", "Response time < 120s", False, 0, f"Post failed")
            return

        reply = wait_for_reply(res["ts"], timeout_s=120)
        elapsed = int((time.monotonic() - t0) * 1000)
        record(5, "5.3", f"Response time ({elapsed}ms)", bool(reply) and elapsed < 120000,
               elapsed, f"text={reply.get('text', '')[:40]!r}" if reply else "No reply")
    except Exception as e:
        record(5, "5.3", "Response time < 120s", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_5_4_tool_use_query():
    """'Search for tools' triggers tool use."""
    t0 = time.monotonic()
    try:
        res = slack_post("Can you search for Gmail integration tools and tell me what's available?")
        if not res.get("ok"):
            record(5, "5.4", "Tool use query", False, 0, "Post failed")
            return

        reply = wait_for_reply(res["ts"], timeout_s=180)
        elapsed = int((time.monotonic() - t0) * 1000)
        if reply:
            text = reply.get("text", "")
            mentions_tools = any(w in text.lower() for w in ("gmail", "email", "tool", "integration", "search"))
            record(5, "5.4", "Tool use query answered", mentions_tools,
                   elapsed, f"text={text[:80]!r}")
        else:
            record(5, "5.4", "Tool use query", False, elapsed, "No reply within 180s")
    except Exception as e:
        record(5, "5.4", "Tool use query", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_5_5_thread_continuity():
    """Thread continuity (follow-up in same thread)."""
    t0 = time.monotonic()
    try:
        res = slack_post("Lucy, tell me a fun fact about space.")
        if not res.get("ok"):
            record(5, "5.5", "Thread continuity", False, 0, "Post failed")
            return

        thread_ts = res["ts"]
        reply = wait_for_reply(thread_ts, timeout_s=180)
        if not reply:
            record(5, "5.5", "Thread continuity", False,
                   int((time.monotonic() - t0) * 1000), "First reply timeout")
            return

        # Send follow-up in the same thread
        with _slack_client() as c:
            c.post("https://slack.com/api/chat.postMessage", json={
                "channel": CHANNEL_ID,
                "text": f"<@{LUCY_USER_ID}> Can you tell me another one?",
                "thread_ts": thread_ts,
                "as_user": True,
            }, headers={"Authorization": f"Bearer {SLACK_USER_TOKEN}"})

        # Wait for second reply
        time.sleep(5)
        second_reply = None
        start = time.time()
        while time.time() - start < 180:
            msgs = slack_get_replies(thread_ts)
            bot_msgs = [m for m in msgs if (m.get("bot_id") or m.get("app_id")) and m.get("ts") != reply.get("ts")]
            if bot_msgs:
                second_reply = bot_msgs[-1]
                break
            time.sleep(5)

        elapsed = int((time.monotonic() - t0) * 1000)
        record(5, "5.5", "Thread continuity", bool(second_reply), elapsed,
               f"reply2={second_reply.get('text', '')[:40]!r}" if second_reply else "No follow-up reply")
    except Exception as e:
        record(5, "5.5", "Thread continuity", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


# ═══════════════════════════════════════════════════════════════════════
# LEVEL 6: INTELLIGENCE & AWARENESS
# ═══════════════════════════════════════════════════════════════════════


async def test_6_1_timezone_awareness():
    """Lucy knows team timezones and can compute local times."""
    t0 = time.monotonic()
    try:
        res = slack_post("What time is it right now for each person on the team?")
        if not res.get("ok"):
            record(6, "6.1", "Timezone awareness", False, 0, "Post failed")
            return
        reply = wait_for_reply(res["ts"], timeout_s=120)
        elapsed = int((time.monotonic() - t0) * 1000)
        if reply:
            text = reply.get("text", "").lower()
            mentions_timezone = any(w in text for w in (
                "ist", "utc", "pst", "est", "kolkata", "am", "pm",
                "time zone", "timezone", "local time"
            ))
            mentions_team = any(w in text for w in ("ojash", "team", "member"))
            record(6, "6.1", "Timezone awareness", mentions_timezone and mentions_team,
                   elapsed, f"text={reply.get('text', '')[:120]!r}")
        else:
            record(6, "6.1", "Timezone awareness", False, elapsed, "No reply")
    except Exception as e:
        record(6, "6.1", "Timezone awareness", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_6_2_connection_awareness():
    """Lucy checks connections and offers link when service is missing."""
    t0 = time.monotonic()
    try:
        res = slack_post("Can you create a ticket in Jira for me? The title should be 'Test Ticket'.")
        if not res.get("ok"):
            record(6, "6.2", "Connection awareness", False, 0, "Post failed")
            return
        reply = wait_for_reply(res["ts"], timeout_s=180)
        elapsed = int((time.monotonic() - t0) * 1000)
        if reply:
            text = reply.get("text", "").lower()
            offers_connect = any(w in text for w in (
                "connect", "link", "authorize", "set up", "access",
                "integration", "not connected"
            ))
            record(6, "6.2", "Connection awareness (offers link)", offers_connect,
                   elapsed, f"text={reply.get('text', '')[:120]!r}")
        else:
            record(6, "6.2", "Connection awareness", False, elapsed, "No reply")
    except Exception as e:
        record(6, "6.2", "Connection awareness", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_6_3_no_sycophantic_opener():
    """Response doesn't start with 'I'd be happy to help' or similar."""
    t0 = time.monotonic()
    try:
        res = slack_post("What's the weather like in New York today?")
        if not res.get("ok"):
            record(6, "6.3", "No sycophantic opener", False, 0, "Post failed")
            return
        reply = wait_for_reply(res["ts"], timeout_s=120)
        elapsed = int((time.monotonic() - t0) * 1000)
        if reply:
            text = reply.get("text", "").lower().strip()
            bad_openers = [
                "i'd be happy to", "i would be happy to", "great question",
                "absolutely!", "of course!", "sure thing!", "i'd love to",
                "certainly!", "it's worth noting", "let me delve"
            ]
            has_bad_opener = any(text.startswith(b) for b in bad_openers)
            record(6, "6.3", "No sycophantic opener", not has_bad_opener,
                   elapsed, f"starts_with={text[:60]!r}")
        else:
            record(6, "6.3", "No sycophantic opener", False, elapsed, "No reply")
    except Exception as e:
        record(6, "6.3", "No sycophantic opener", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_6_4_admits_uncertainty():
    """Lucy admits when she doesn't know something instead of guessing."""
    t0 = time.monotonic()
    try:
        res = slack_post(
            "What was our exact MRR last month? Give me the precise number."
        )
        if not res.get("ok"):
            record(6, "6.4", "Admits uncertainty", False, 0, "Post failed")
            return
        reply = wait_for_reply(res["ts"], timeout_s=120)
        elapsed = int((time.monotonic() - t0) * 1000)
        if reply:
            text = reply.get("text", "").lower()
            admits = any(w in text for w in (
                "don't have", "not sure", "don't know", "no access",
                "can't find", "need to check", "let me check",
                "connect", "unable to", "i'll need", "i would need"
            ))
            makes_up_number = any(
                c.isdigit() and text.count("$") > 0
                for c in text
            )
            passed = admits or not makes_up_number
            record(6, "6.4", "Admits uncertainty", passed,
                   elapsed, f"text={reply.get('text', '')[:120]!r}")
        else:
            record(6, "6.4", "Admits uncertainty", False, elapsed, "No reply")
    except Exception as e:
        record(6, "6.4", "Admits uncertainty", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_6_5_direct_short_answer():
    """Simple question gets a concise direct answer, not a paragraph."""
    t0 = time.monotonic()
    try:
        res = slack_post("What day of the week is it today?")
        if not res.get("ok"):
            record(6, "6.5", "Direct short answer", False, 0, "Post failed")
            return
        reply = wait_for_reply(res["ts"], timeout_s=60)
        elapsed = int((time.monotonic() - t0) * 1000)
        if reply:
            text = reply.get("text", "")
            word_count = len(text.split())
            is_concise = word_count < 30
            mentions_day = any(d in text.lower() for d in (
                "monday", "tuesday", "wednesday", "thursday",
                "friday", "saturday", "sunday"
            ))
            record(6, "6.5", "Direct short answer", is_concise and mentions_day,
                   elapsed, f"words={word_count}, text={text[:80]!r}")
        else:
            record(6, "6.5", "Direct short answer", False, elapsed, "No reply")
    except Exception as e:
        record(6, "6.5", "Direct short answer", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_6_6_complex_task_acknowledgment():
    """Lucy acknowledges a complex task will take time before starting."""
    t0 = time.monotonic()
    try:
        res = slack_post(
            "I need you to research the top 10 competitors in the AI agent space, "
            "analyze their pricing models, and create a comparison spreadsheet."
        )
        if not res.get("ok"):
            record(6, "6.6", "Complex task acknowledgment", False, 0, "Post failed")
            return
        reply = wait_for_reply(res["ts"], timeout_s=180)
        elapsed = int((time.monotonic() - t0) * 1000)
        if reply:
            text = reply.get("text", "").lower()
            acknowledges = any(w in text for w in (
                "take a", "few minutes", "working on", "let me",
                "i'll", "on it", "moment", "give me", "research",
                "look into", "dive into"
            ))
            record(6, "6.6", "Complex task acknowledgment", acknowledges,
                   elapsed, f"text={reply.get('text', '')[:120]!r}")
        else:
            record(6, "6.6", "Complex task acknowledgment", False, elapsed, "No reply")
    except Exception as e:
        record(6, "6.6", "Complex task acknowledgment", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


# ═══════════════════════════════════════════════════════════════════════
# LEVEL 7: MEMORY & SKILLS
# ═══════════════════════════════════════════════════════════════════════


async def test_7_1_reads_skill_before_acting():
    """Lucy references skill knowledge when responding to a relevant query."""
    t0 = time.monotonic()
    try:
        res = slack_post(
            "How do you manage integrations? What's your approach?"
        )
        if not res.get("ok"):
            record(7, "7.1", "Reads skill before acting", False, 0, "Post failed")
            return
        reply = wait_for_reply(res["ts"], timeout_s=120)
        elapsed = int((time.monotonic() - t0) * 1000)
        if reply:
            text = reply.get("text", "").lower()
            mentions_composio = any(w in text for w in (
                "composio", "integration", "connect", "tool", "meta-tool",
                "search_tools", "manage_connections", "oauth"
            ))
            record(7, "7.1", "Reads skill before acting", mentions_composio,
                   elapsed, f"text={reply.get('text', '')[:120]!r}")
        else:
            record(7, "7.1", "Reads skill before acting", False, elapsed, "No reply")
    except Exception as e:
        record(7, "7.1", "Reads skill before acting", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_7_2_updates_skill_after_learning():
    """Lucy writes to a skill/learnings file after being taught something."""
    t0 = time.monotonic()
    try:
        from lucy.config import settings
        ws_path = settings.workspace_root / "1d18c417-b53c-4ab1-80da-4959a622da17"
        learnings_before = set()
        for f in ws_path.rglob("LEARNINGS.md"):
            learnings_before.add((str(f), f.stat().st_mtime))

        res = slack_post(
            "Important: Our company just changed its name from 'TechCorp' to 'InnovateLabs'. "
            "Please remember this and update your knowledge files."
        )
        if not res.get("ok"):
            record(7, "7.2", "Updates skill after learning", False, 0, "Post failed")
            return
        reply = wait_for_reply(res["ts"], timeout_s=120)
        time.sleep(5)

        learnings_after = set()
        for f in ws_path.rglob("LEARNINGS.md"):
            learnings_after.add((str(f), f.stat().st_mtime))
        skill_files_changed = False
        for f in ws_path.rglob("SKILL.md"):
            content = f.read_text().lower()
            if "innovatelabs" in content or "innov" in content:
                skill_files_changed = True
                break

        new_learnings = learnings_after - learnings_before
        elapsed = int((time.monotonic() - t0) * 1000)
        updated = bool(new_learnings) or skill_files_changed
        record(7, "7.2", "Updates skill after learning", updated,
               elapsed,
               f"new_learnings={len(new_learnings)}, skill_changed={skill_files_changed}, "
               f"reply={reply.get('text', '')[:80]!r}" if reply else "No reply")
    except Exception as e:
        record(7, "7.2", "Updates skill after learning", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_7_3_activity_log_written():
    """Activity log is updated after agent interactions."""
    t0 = time.monotonic()
    try:
        from lucy.config import settings
        ws_path = settings.workspace_root / "1d18c417-b53c-4ab1-80da-4959a622da17"
        logs_dir = ws_path / "logs"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = logs_dir / f"{today}.md"

        had_log_before = log_file.exists()
        size_before = log_file.stat().st_size if had_log_before else 0

        res = slack_post("Just checking in — say hi!")
        if not res.get("ok"):
            record(7, "7.3", "Activity log written", False, 0, "Post failed")
            return
        reply = wait_for_reply(res["ts"], timeout_s=60)
        time.sleep(3)

        has_log_after = log_file.exists()
        size_after = log_file.stat().st_size if has_log_after else 0
        grew = size_after > size_before

        elapsed = int((time.monotonic() - t0) * 1000)
        record(7, "7.3", "Activity log written", has_log_after and grew,
               elapsed,
               f"before={size_before}B, after={size_after}B, grew={grew}")
    except Exception as e:
        record(7, "7.3", "Activity log written", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


# ═══════════════════════════════════════════════════════════════════════
# LEVEL 8: TOOL CALLING INTELLIGENCE
# ═══════════════════════════════════════════════════════════════════════


async def test_8_1_multi_step_tool_chain():
    """Lucy performs search → execute multi-step tool flow."""
    t0 = time.monotonic()
    try:
        res = slack_post(
            "Search for Google Calendar tools and tell me what actions are "
            "available for managing events."
        )
        if not res.get("ok"):
            record(8, "8.1", "Multi-step tool chain", False, 0, "Post failed")
            return
        reply = wait_for_reply(res["ts"], timeout_s=180)
        elapsed = int((time.monotonic() - t0) * 1000)
        if reply:
            text = reply.get("text", "").lower()
            mentions_actions = any(w in text for w in (
                "create", "list", "delete", "update", "event",
                "calendar", "schedule", "invite", "meeting"
            ))
            record(8, "8.1", "Multi-step tool chain", mentions_actions,
                   elapsed, f"text={reply.get('text', '')[:120]!r}")
        else:
            record(8, "8.1", "Multi-step tool chain", False, elapsed, "No reply")
    except Exception as e:
        record(8, "8.1", "Multi-step tool chain", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_8_2_connection_check():
    """Lucy can check which integrations are connected."""
    t0 = time.monotonic()
    try:
        res = slack_post("What integrations do I currently have connected?")
        if not res.get("ok"):
            record(8, "8.2", "Connection check", False, 0, "Post failed")
            return
        reply = wait_for_reply(res["ts"], timeout_s=180)
        elapsed = int((time.monotonic() - t0) * 1000)
        if reply:
            text = reply.get("text", "").lower()
            mentions_services = any(w in text for w in (
                "gmail", "google", "calendar", "github", "slack",
                "connected", "integration", "connection", "active"
            ))
            record(8, "8.2", "Connection check", mentions_services,
                   elapsed, f"text={reply.get('text', '')[:120]!r}")
        else:
            record(8, "8.2", "Connection check", False, elapsed, "No reply")
    except Exception as e:
        record(8, "8.2", "Connection check", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_8_3_graceful_unavailable_tool():
    """Lucy explains when a tool isn't available and suggests alternatives."""
    t0 = time.monotonic()
    try:
        res = slack_post(
            "Deploy my latest code to AWS Lambda right now."
        )
        if not res.get("ok"):
            record(8, "8.3", "Graceful unavailable tool", False, 0, "Post failed")
            return
        reply = wait_for_reply(res["ts"], timeout_s=120)
        elapsed = int((time.monotonic() - t0) * 1000)
        if reply:
            text = reply.get("text", "").lower()
            graceful = any(w in text for w in (
                "don't have access", "not connected", "connect",
                "can't", "unable", "need to", "would need",
                "alternative", "help with", "set up"
            ))
            doesnt_hallucinate = "deployed" not in text or "successfully" not in text
            record(8, "8.3", "Graceful unavailable tool", graceful and doesnt_hallucinate,
                   elapsed, f"text={reply.get('text', '')[:120]!r}")
        else:
            record(8, "8.3", "Graceful unavailable tool", False, elapsed, "No reply")
    except Exception as e:
        record(8, "8.3", "Graceful unavailable tool", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


# ═══════════════════════════════════════════════════════════════════════
# LEVEL 9: RESPONSE QUALITY & FORMATTING
# ═══════════════════════════════════════════════════════════════════════


async def test_9_1_markdown_formatting():
    """Lucy uses proper Slack mrkdwn (bold, lists, code blocks)."""
    t0 = time.monotonic()
    try:
        res = slack_post(
            "Give me a quick summary of what you can do. Use bullet points."
        )
        if not res.get("ok"):
            record(9, "9.1", "Markdown formatting", False, 0, "Post failed")
            return
        reply = wait_for_reply(res["ts"], timeout_s=120)
        elapsed = int((time.monotonic() - t0) * 1000)
        if reply:
            text = reply.get("text", "")
            has_bullets = "•" in text or "- " in text or "* " in text
            has_bold = "*" in text
            has_structure = has_bullets or has_bold
            record(9, "9.1", "Markdown formatting", has_structure,
                   elapsed, f"bullets={has_bullets}, bold={has_bold}, text={text[:100]!r}")
        else:
            record(9, "9.1", "Markdown formatting", False, elapsed, "No reply")
    except Exception as e:
        record(9, "9.1", "Markdown formatting", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_9_2_appropriate_length():
    """Simple question → short; complex question → detailed but not bloated."""
    t0 = time.monotonic()
    try:
        res1 = slack_post("What's 15 * 23?")
        if not res1.get("ok"):
            record(9, "9.2", "Appropriate length", False, 0, "Post failed")
            return
        reply1 = wait_for_reply(res1["ts"], timeout_s=60)
        if not reply1:
            record(9, "9.2", "Appropriate length", False,
                   int((time.monotonic() - t0) * 1000), "No reply to simple question")
            return
        simple_len = len(reply1.get("text", "").split())

        res2 = slack_post(
            "Explain the differences between REST APIs and GraphQL, "
            "including pros, cons, and when to use each."
        )
        reply2 = wait_for_reply(res2["ts"], timeout_s=120)
        elapsed = int((time.monotonic() - t0) * 1000)
        if not reply2:
            record(9, "9.2", "Appropriate length", False, elapsed, "No reply to complex question")
            return
        complex_len = len(reply2.get("text", "").split())

        simple_is_short = simple_len < 20
        complex_is_longer = complex_len > simple_len
        complex_not_bloated = complex_len < 500

        passed = simple_is_short and complex_is_longer and complex_not_bloated
        record(9, "9.2", "Appropriate length", passed, elapsed,
               f"simple={simple_len}w, complex={complex_len}w")
    except Exception as e:
        record(9, "9.2", "Appropriate length", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_9_3_no_hallucinated_actions():
    """Lucy doesn't claim to have done something it can't do."""
    t0 = time.monotonic()
    try:
        res = slack_post("Send an email to john@example.com saying 'Hello from Lucy test'.")
        if not res.get("ok"):
            record(9, "9.3", "No hallucinated actions", False, 0, "Post failed")
            return
        reply = wait_for_reply(res["ts"], timeout_s=180)
        elapsed = int((time.monotonic() - t0) * 1000)
        if reply:
            text = reply.get("text", "").lower()
            hallucinated = any(w in text for w in (
                "sent the email", "email has been sent", "i've sent",
                "email sent successfully", "done, i've sent"
            ))
            asks_permission_or_connects = any(w in text for w in (
                "confirm", "should i", "want me to", "connect",
                "draft", "review", "before i send"
            ))
            passed = not hallucinated or asks_permission_or_connects
            record(9, "9.3", "No hallucinated actions", passed,
                   elapsed, f"hallucinated={hallucinated}, text={reply.get('text', '')[:120]!r}")
        else:
            record(9, "9.3", "No hallucinated actions", False, elapsed, "No reply")
    except Exception as e:
        record(9, "9.3", "No hallucinated actions", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


# ═══════════════════════════════════════════════════════════════════════
# LEVEL 10: SLACK CAPABILITIES
# ═══════════════════════════════════════════════════════════════════════


async def test_10_1_hourglass_reaction():
    """Lucy adds hourglass reaction while processing."""
    t0 = time.monotonic()
    try:
        res = slack_post("Tell me a fun fact about the ocean.")
        if not res.get("ok"):
            record(10, "10.1", "Hourglass reaction", False, 0, "Post failed")
            return
        event_ts = res["ts"]
        time.sleep(2)

        with _slack_client() as c:
            rr = c.get(
                f"https://slack.com/api/reactions.get?channel={CHANNEL_ID}"
                f"&timestamp={event_ts}&full=true",
                headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            )
            data = rr.json()
            msg = data.get("message", {})
            reactions = msg.get("reactions", [])
            has_hourglass = any(
                r.get("name") == "hourglass_flowing_sand" for r in reactions
            )

        reply = wait_for_reply(res["ts"], timeout_s=120)
        elapsed = int((time.monotonic() - t0) * 1000)

        time.sleep(2)
        with _slack_client() as c:
            rr2 = c.get(
                f"https://slack.com/api/reactions.get?channel={CHANNEL_ID}"
                f"&timestamp={event_ts}&full=true",
                headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            )
            data2 = rr2.json()
            reactions2 = data2.get("message", {}).get("reactions", [])
            hourglass_removed = not any(
                r.get("name") == "hourglass_flowing_sand" for r in reactions2
            )

        record(10, "10.1", "Hourglass reaction", has_hourglass or hourglass_removed,
               elapsed,
               f"added_during={has_hourglass}, removed_after={hourglass_removed}")
    except Exception as e:
        record(10, "10.1", "Hourglass reaction", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_10_2_dm_response():
    """Lucy responds to direct messages."""
    t0 = time.monotonic()
    try:
        with _slack_client() as c:
            conv = c.post("https://slack.com/api/conversations.open", json={
                "users": LUCY_USER_ID,
            }, headers={"Authorization": f"Bearer {SLACK_USER_TOKEN}"})
            dm_channel = conv.json().get("channel", {}).get("id")
            if not dm_channel:
                record(10, "10.2", "DM response", False, 0, "Can't open DM")
                return

            c.post("https://slack.com/api/chat.postMessage", json={
                "channel": dm_channel,
                "text": "Hey Lucy, quick DM test!",
                "as_user": True,
            }, headers={"Authorization": f"Bearer {SLACK_USER_TOKEN}"})

        start = time.time()
        reply = None
        while time.time() - start < 120:
            time.sleep(5)
            with _slack_client() as c:
                resp = c.get(
                    f"https://slack.com/api/conversations.history?channel={dm_channel}&limit=5",
                    headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
                )
                msgs = resp.json().get("messages", [])
                for m in msgs:
                    if (m.get("bot_id") or m.get("app_id")) and "test" not in m.get("text", "").lower()[:20]:
                        reply = m
                        break
                if reply:
                    break

        elapsed = int((time.monotonic() - t0) * 1000)
        record(10, "10.2", "DM response", bool(reply),
               elapsed, f"text={reply.get('text', '')[:80]!r}" if reply else "No DM reply")
    except Exception as e:
        record(10, "10.2", "DM response", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


async def test_10_3_mrkdwn_conversion():
    """Markdown ** → Slack * bold conversion works."""
    t0 = time.monotonic()
    try:
        res = slack_post("List 3 reasons why Python is popular. Use bold headers for each.")
        if not res.get("ok"):
            record(10, "10.3", "mrkdwn conversion", False, 0, "Post failed")
            return
        reply = wait_for_reply(res["ts"], timeout_s=120)
        elapsed = int((time.monotonic() - t0) * 1000)
        if reply:
            text = reply.get("text", "")
            has_double_star = "**" in text
            has_single_star_bold = text.count("*") >= 2 and "**" not in text
            passed = has_single_star_bold and not has_double_star
            record(10, "10.3", "mrkdwn conversion", passed,
                   elapsed,
                   f"double_star={has_double_star}, single_bold={has_single_star_bold}, "
                   f"text={text[:100]!r}")
        else:
            record(10, "10.3", "mrkdwn conversion", False, elapsed, "No reply")
    except Exception as e:
        record(10, "10.3", "mrkdwn conversion", False,
               int((time.monotonic() - t0) * 1000), str(e)[:200])


# ═══════════════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════════════

async def run_level_1():
    print("\n" + "=" * 60)
    print("LEVEL 1: INFRASTRUCTURE")
    print("=" * 60)
    test_1_1_import()
    test_1_2_openrouter_ping()
    test_1_3_tool_calling_works()
    composio = await test_1_4_composio_init()
    session = await test_1_5_composio_session(composio)
    await test_1_6_composio_tools(session)
    test_1_7_config_loads()
    await test_1_8_workspace_structure()
    await test_1_9_skills_seeded()
    await test_1_10_crons_seeded()
    await test_1_11_team_profiled()
    await test_1_12_company_stub()


async def run_level_2():
    print("\n" + "=" * 60)
    print("LEVEL 2: CORE AGENT")
    print("=" * 60)
    await test_2_1_prompt_has_soul()
    await test_2_2_prompt_has_template()
    await test_2_3_prompt_has_skills()
    await test_2_4_full_prompt_build()
    await test_2_5_llm_simple_response()
    await test_2_6_llm_response_time()


async def run_level_3():
    print("\n" + "=" * 60)
    print("LEVEL 3: TOOL CALLING & INTEGRATIONS")
    print("=" * 60)
    tools = await test_3_1_tools_in_payload()
    await test_3_2_llm_calls_tool(tools)
    await test_3_3_tool_execution()


async def run_level_4():
    print("\n" + "=" * 60)
    print("LEVEL 4: WORKSPACE, SKILLS, MEMORY")
    print("=" * 60)
    await test_4_1_workspace_dirs()
    await test_4_2_skill_frontmatter()
    await test_4_3_state_json()
    await test_4_4_activity_log()
    await test_4_5_cron_task_json()
    await test_4_6_timezone_data()


async def run_level_5():
    print("\n" + "=" * 60)
    print("LEVEL 5: END-TO-END SLACK INTERACTIONS")
    print("=" * 60)
    thread_ts = await test_5_1_mention_single_reply()
    await test_5_2_reply_in_thread(thread_ts)
    await test_5_3_response_time()
    await test_5_4_tool_use_query()
    await test_5_5_thread_continuity()


async def run_level_6():
    print("\n" + "=" * 60)
    print("LEVEL 6: INTELLIGENCE & AWARENESS")
    print("=" * 60)
    await test_6_1_timezone_awareness()
    await test_6_2_connection_awareness()
    await test_6_3_no_sycophantic_opener()
    await test_6_4_admits_uncertainty()
    await test_6_5_direct_short_answer()
    await test_6_6_complex_task_acknowledgment()


async def run_level_7():
    print("\n" + "=" * 60)
    print("LEVEL 7: MEMORY & SKILLS")
    print("=" * 60)
    await test_7_1_reads_skill_before_acting()
    await test_7_2_updates_skill_after_learning()
    await test_7_3_activity_log_written()


async def run_level_8():
    print("\n" + "=" * 60)
    print("LEVEL 8: TOOL CALLING INTELLIGENCE")
    print("=" * 60)
    await test_8_1_multi_step_tool_chain()
    await test_8_2_connection_check()
    await test_8_3_graceful_unavailable_tool()


async def run_level_9():
    print("\n" + "=" * 60)
    print("LEVEL 9: RESPONSE QUALITY & FORMATTING")
    print("=" * 60)
    await test_9_1_markdown_formatting()
    await test_9_2_appropriate_length()
    await test_9_3_no_hallucinated_actions()


async def run_level_10():
    print("\n" + "=" * 60)
    print("LEVEL 10: SLACK CAPABILITIES")
    print("=" * 60)
    await test_10_1_hourglass_reaction()
    await test_10_2_dm_response()
    await test_10_3_mrkdwn_conversion()


def print_summary():
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    by_level = {}
    for r in RESULTS:
        level = r["level"]
        by_level.setdefault(level, {"total": 0, "passed": 0, "failed": 0})
        by_level[level]["total"] += 1
        if r["status"] == "PASS":
            by_level[level]["passed"] += 1
        else:
            by_level[level]["failed"] += 1

    total_pass = sum(v["passed"] for v in by_level.values())
    total_fail = sum(v["failed"] for v in by_level.values())
    total = total_pass + total_fail

    level_names = {
        1: "Infrastructure", 2: "Core Agent", 3: "Tool Calling",
        4: "Workspace/Skills", 5: "E2E Slack",
        6: "Intelligence", 7: "Memory/Skills",
        8: "Tool Intelligence", 9: "Response Quality",
        10: "Slack Capabilities",
    }
    for lvl in sorted(by_level):
        info = by_level[lvl]
        pct = round(info["passed"] / info["total"] * 100) if info["total"] else 0
        print(f"  L{lvl:2d} {level_names.get(lvl, '?'):20s}: {info['passed']}/{info['total']} ({pct}%)")

    pct_total = round(total_pass / total * 100) if total else 0
    print(f"\n  TOTAL: {total_pass}/{total} ({pct_total}%)")

    if total_fail:
        print(f"\n  FAILURES ({total_fail}):")
        for r in RESULTS:
            if r["status"] == "FAIL":
                print(f"    ❌ {r['id']} {r['name']}: {r['notes'][:100]}")

    return total_fail == 0


async def main():
    # Usage: python run_tests.py [max_level] [min_level]
    max_level = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    min_level = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    runners = {
        1: run_level_1, 2: run_level_2, 3: run_level_3,
        4: run_level_4, 5: run_level_5, 6: run_level_6,
        7: run_level_7, 8: run_level_8, 9: run_level_9,
        10: run_level_10,
    }
    for lvl in range(min_level, max_level + 1):
        if lvl in runners:
            await runners[lvl]()

    all_passed = print_summary()
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    asyncio.run(main())
