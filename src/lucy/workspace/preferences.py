"""User preference tracking.

Extracts and persists user preferences from conversations,
making Lucy progressively smarter about individual team members.
Preferences are stored per-user in the workspace's data directory.

Preferences tracked:
- Communication style (brief vs. detailed)
- Preferred output formats (bullets, prose, tables)
- Timezone (inferred or stated)
- Notification preferences (DM vs. channel)
- Domains of interest (inferred from what they ask about)
- Expertise level (tech vs. non-tech)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from lucy.workspace.filesystem import WorkspaceFS

logger = structlog.get_logger()

_PREFS_DIR = "data/preferences"


def _prefs_path(ws: WorkspaceFS, user_id: str) -> Path:
    prefs_dir = ws.root / _PREFS_DIR
    prefs_dir.mkdir(parents=True, exist_ok=True)
    return prefs_dir / f"{user_id}.json"


def load_user_preferences(ws: WorkspaceFS, user_id: str) -> dict[str, Any]:
    """Load preferences for a user. Returns empty dict if none stored."""
    path = _prefs_path(ws, user_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_user_preferences(
    ws: WorkspaceFS,
    user_id: str,
    prefs: dict[str, Any],
) -> None:
    """Write preferences to disk."""
    path = _prefs_path(ws, user_id)
    try:
        path.write_text(
            json.dumps(prefs, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("prefs_write_failed", user_id=user_id, error=str(e))


def update_preference(
    ws: WorkspaceFS,
    user_id: str,
    key: str,
    value: Any,
    source: str = "inferred",
) -> None:
    """Set a single preference key. Explicit > inferred."""
    prefs = load_user_preferences(ws, user_id)

    existing_source = prefs.get(f"_src_{key}", "inferred")
    if existing_source == "explicit" and source == "inferred":
        return

    prefs[key] = value
    prefs[f"_src_{key}"] = source
    prefs[f"_ts_{key}"] = datetime.now(timezone.utc).isoformat()
    save_user_preferences(ws, user_id, prefs)
    logger.debug("preference_updated", user_id=user_id, key=key, value=value, source=source)


def extract_preferences_from_message(
    user_id: str,
    message: str,
    ws: WorkspaceFS,
) -> None:
    """Heuristic extraction of preferences from a user message.

    Runs after every interaction — cheap regex/keyword checks, no LLM cost.
    """
    msg_lower = message.lower().strip()

    # Communication style
    brief_signals = [
        "keep it short", "brief", "tldr", "quick", "one line",
        "don't explain", "no explanation", "just the answer",
    ]
    detailed_signals = [
        "detailed", "in depth", "thorough", "explain everything",
        "comprehensive", "step by step", "full breakdown",
    ]
    if any(s in msg_lower for s in brief_signals):
        update_preference(ws, user_id, "response_style", "brief", source="explicit")
    elif any(s in msg_lower for s in detailed_signals):
        update_preference(ws, user_id, "response_style", "detailed", source="explicit")

    # Format preferences
    if "use bullets" in msg_lower or "bullet points" in msg_lower:
        update_preference(ws, user_id, "format", "bullets", source="explicit")
    elif "use a table" in msg_lower or "in a table" in msg_lower:
        update_preference(ws, user_id, "format", "table", source="explicit")
    elif "in prose" in msg_lower or "as paragraphs" in msg_lower:
        update_preference(ws, user_id, "format", "prose", source="explicit")

    # Notification preference
    if "dm me" in msg_lower or "in a dm" in msg_lower or "send me a dm" in msg_lower:
        update_preference(ws, user_id, "notify_via", "dm", source="explicit")
    elif "in the channel" in msg_lower or "post here" in msg_lower:
        update_preference(ws, user_id, "notify_via", "channel", source="explicit")

    # Domain interest signals (inferred from topic)
    domains = {
        "seo": ["seo", "search console", "keywords", "rankings", "backlinks"],
        "sales": ["crm", "pipeline", "deals", "hubspot", "salesforce"],
        "engineering": ["github", "pull request", "deploy", "ci/cd", "kubernetes"],
        "marketing": ["campaign", "email open rate", "mailchimp", "conversion"],
        "finance": ["mrr", "arr", "revenue", "stripe", "invoices"],
        "hr": ["hiring", "headcount", "onboarding", "salary", "perk"],
    }
    prefs = load_user_preferences(ws, user_id)
    interests = prefs.get("domains", [])
    for domain, signals in domains.items():
        if any(s in msg_lower for s in signals) and domain not in interests:
            interests.append(domain)
            update_preference(ws, user_id, "domains", interests, source="inferred")
            break


def format_preferences_for_prompt(prefs: dict[str, Any]) -> str:
    """Format user preferences as a brief prompt injection."""
    if not prefs:
        return ""

    lines = []
    if style := prefs.get("response_style"):
        lines.append(f"- Prefers {style} responses")
    if fmt := prefs.get("format"):
        lines.append(f"- Prefers {fmt} format")
    if notify := prefs.get("notify_via"):
        lines.append(f"- Prefers notifications via {notify}")
    if domains := prefs.get("domains"):
        lines.append(f"- Works with: {', '.join(domains[:4])}")

    if not lines:
        return ""
    return "Known preferences for this user:\n" + "\n".join(lines)
