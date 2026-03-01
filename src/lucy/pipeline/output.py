"""Six-layer output pipeline for Lucy's Slack messages.

Layer 0:   Internal content filter - strips planning, self-correction,
           quality-gate critique, leaked XML tags, and meta-commentary
           using structural classification (content_classifier.py)
Layer 0.5: Fact verifier - corrects hallucinated dates, validates URLs,
           flags stale version/pricing claims (fact_verifier.py) [W7-TRUTH]
Layer 1:   Sanitizer - strips paths, tool names, internal references
Layer 2:   Format converter - transforms Markdown to Slack mrkdwn
Layer 2.5: Proactive endings - auto-CTA appender & related topic suggestions
Layer 3:   Tone validator - catches robotic/error-dump patterns
Layer 4:   De-AI engine - detects and strips AI-generated tells via regex

The de-AI engine has two tiers, but only Tier 1 is active:
  Tier 1 (instant): Regex detection + mechanical fixes for obvious patterns
                     — catches em dashes, power words, chatbot closers, etc.
  Tier 2 (DISABLED): LLM-based contextual rewrite. Disabled because:
                      (a) the rewriter has no access to SOUL.md personality,
                      (b) its generic "smart colleague" prompt flattens Lucy's
                          voice into bland corporate tone,
                      (c) it can destroy formatting (numbered lists, structure),
                      (d) Tier 1 regex already catches ~90% of AI tells.
                      The code is kept as a safety net but the threshold is set
                      to 999 so it never triggers in practice.

Applied to every message before posting to Slack.
"""

from __future__ import annotations

import asyncio
import re

import structlog

from lucy.config import LLMPresets, settings
from lucy.pipeline.content_classifier import strip_internal_content
from lucy.pipeline.fact_verifier import verify_claims, verify_claims_sync

logger = structlog.get_logger()

# ═══════════════════════════════════════════════════════════════════════
# LAYER 1: OUTPUT SANITIZER
# ═══════════════════════════════════════════════════════════════════════

_REDACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"/home/user/[^\s)\"']+"), ""),
    (re.compile(r"/workspace[s]?/[^\s)\"']+"), ""),
    (re.compile(r"@?workspace_seeds[^\s]*"), ""),
    (re.compile(r"(?:using |called |via |through )?COMPOSIO_SEARCH_TOOLS"), "searching available tools"),
    (re.compile(r"(?:using |called |via |through )?COMPOSIO_MANAGE_CONNECTIONS"), "checking integrations"),
    (re.compile(r"(?:using |called |via |through )?COMPOSIO_MULTI_EXECUTE_TOOL"), "running actions"),
    (re.compile(r"(?:using |called |via |through )?COMPOSIO_REMOTE_WORKBENCH"), "running some code"),
    (re.compile(r"(?:using |called |via |through )?COMPOSIO_REMOTE_BASH_TOOL"), "running a script"),
    (re.compile(r"(?:using |called |via |through )?COMPOSIO_GET_TOOL_SCHEMAS"), "looking up tool details"),
    (re.compile(r"COMPOSIO_\w+"), ""),
    (re.compile(r"`?lucy_custom_\w+`?"), ""),
    (re.compile(r"\blucy_\w+\b"), ""),
    (re.compile(r"(?:through |via |using |on )composio(?!\.dev)(?!\.\w)", re.IGNORECASE), ""),
    (re.compile(r"(?<![\w/.])composio(?!\.dev)(?!\.\w)", re.IGNORECASE), ""),
    (re.compile(r"(?i)\bopenrouter\b"), ""),
    (re.compile(r"(?i)\bopenclaw\b"), ""),
    (re.compile(r"(?i)\bminimax\b"), ""),
    (re.compile(r"SKILL\.md|LEARNINGS\.md|state\.json"), "my notes"),
    (re.compile(r"\btool[_ ]?call[s]?\b", re.IGNORECASE), "request"),
    (re.compile(r"\bmeta[- ]?tool[s]?\b", re.IGNORECASE), ""),
    (re.compile(r"sk_live_[A-Za-z0-9_-]{20,}"), "[REDACTED]"),
    (re.compile(r"sk_test_[A-Za-z0-9_-]{20,}"), "[REDACTED]"),
    (re.compile(r"Bearer\s+[A-Za-z0-9_-]{20,}"), "Bearer [REDACTED]"),
    (re.compile(r"pol_[A-Za-z0-9_-]{20,}"), "[REDACTED]"),
    (re.compile(r"['\"]Authorization['\"]:\s*['\"]Bearer\s+[^'\"]+['\"]"), '"Authorization": "Bearer [REDACTED]"'),
    (re.compile(r"<(?:request|invoke)[^>]*>.*?</invoke>", re.DOTALL), ""),
    (re.compile(r"<invoke\s+name=.*?>.*?</invoke>", re.DOTALL), ""),
    (re.compile(r"(?i)the api key[^.]*(?:workbench|sandbox|available)[^.]*\.", re.DOTALL), ""),
    (re.compile(r"(?i)(?:let me|i(?:'ll| will)) try a different (?:approach|strategy)[^.]*\.", re.DOTALL), ""),
    (re.compile(r"(?i)(?:api key|credentials?) (?:isn't|aren't|is not|are not) available[^.]*\.", re.DOTALL), ""),
    (re.compile(r"\bfunction calling\b", re.IGNORECASE), ""),
    (re.compile(r"(?:workspace_id|trace_id|call_id|entity_id|session_id|deployment_id)[=:\s]*[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE), ""),
    (re.compile(r"crons/[^\s)\"']+"), "the scheduled task"),
    (re.compile(r"task\.json\b"), ""),
    (re.compile(
        r'\{\s*"(?:success|error|result|project_name|slug|sandbox_path|convex_url|'
        r'deployment_id|file_path|url|subdomain|preview_url|workspace_id|'
        r'created_at|last_deployed|description|name|count|apps)"\s*:'
        r'[^}]*\}',
        re.DOTALL,
    ), ""),
    (re.compile(r"/Users/[^\s)\"']+"), ""),
]

_ALLCAPS_TOOL_RE = re.compile(r"\b[A-Z]{2,}_[A-Z_]{3,}\b")

_HUMANIZE_MAP = {
    "COMPOSIO_SEARCH_TOOLS": "search for tools",
    "COMPOSIO_MANAGE_CONNECTIONS": "manage integrations",
    "COMPOSIO_MULTI_EXECUTE_TOOL": "execute actions",
    "COMPOSIO_REMOTE_WORKBENCH": "run code",
    "COMPOSIO_REMOTE_BASH_TOOL": "run a script",
    "COMPOSIO_GET_TOOL_SCHEMAS": "look up tool details",
    "GOOGLECALENDAR_CREATE_EVENT": "schedule a meeting",
    "GOOGLECALENDAR_EVENTS_LIST": "check your calendar",
    "GOOGLECALENDAR_FIND_FREE_SLOTS": "find open time slots",
    "GMAIL_SEND_EMAIL": "send an email",
    "GMAIL_GET_EMAILS": "check your email",
    "GMAIL_CREATE_DRAFT": "draft an email",
    "GOOGLEDRIVE_LIST_FILES": "check your Drive",
    "GOOGLEDRIVE_CREATE_FILE": "create a file in Drive",
    "GOOGLESHEETS_GET_SPREADSHEET": "check a spreadsheet",
    "GITHUB_LIST_PULL_REQUESTS": "check pull requests",
    "GITHUB_CREATE_ISSUE": "create an issue",
    "GITHUB_GET_REPOSITORY": "check the repository",
    "LINEAR_CREATE_ISSUE": "create a Linear ticket",
    "LINEAR_LIST_ISSUES": "check Linear issues",
}


def _sanitize(text: str) -> str:
    original = text
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)

    def _humanize_or_strip(match: re.Match[str]) -> str:
        name = match.group(0)
        return _HUMANIZE_MAP.get(name, "")

    text = _ALLCAPS_TOOL_RE.sub(_humanize_or_strip, text)
    text = re.sub(r"  +", " ", text)
    if not text.strip() and original.strip():
        return "I've completed the task."
    return text


# ═══════════════════════════════════════════════════════════════════════
# LAYER 2: MARKDOWN -> SLACK MRKDWN CONVERTER
# ═══════════════════════════════════════════════════════════════════════

def _convert_markdown_to_slack(text: str) -> str:
    code_blocks: list[str] = []

    def _stash_code(m: re.Match[str]) -> str:
        code_blocks.append(m.group(0))
        return f"\x00CODEBLOCK{len(code_blocks) - 1}\x00"

    text = re.sub(r"```.*?```", _stash_code, text, flags=re.DOTALL)
    text = re.sub(r"`[^`]+`", _stash_code, text)

    # Strip stray Unicode box-drawing lines outside code blocks
    # (LLM sometimes outputs ─────── or ═══════ as raw separators)
    text = re.sub(r"^[─━═╌╍┄┅]{4,}$", "", text, flags=re.MULTILINE)

    text = _convert_tables_to_code_blocks(text)
    # Convert markdown links FIRST (before bold conversion touches them)
    text = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"<\2|\1>", text,
    )
    # Bold: **text** → *text*, but NOT when the content is a URL
    def _bold_replace(m: re.Match[str]) -> str:
        inner = m.group(1)
        if inner.startswith("http://") or inner.startswith("https://"):
            return inner
        return f"*{inner}*"
    text = re.sub(r"\*\*(.+?)\*\*", _bold_replace, text)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*\*\*", "", text)
    text = re.sub(r"\*\*", "", text)
    # Clean stray * attached to URLs (e.g. "*https://url*" → "https://url")
    text = re.sub(r"\*(https?://[^\s*|>]+)\*", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    for i, block in enumerate(code_blocks):
        text = text.replace(f"\x00CODEBLOCK{i}\x00", block)
    return text


def _convert_tables_to_code_blocks(text: str) -> str:
    """Convert markdown pipe-tables to aligned code block tables.

    Viktor-style: tables become code blocks with box-drawing chars
    for separators and properly aligned columns. This preserves
    tabular data as actual tables instead of converting to bullets.
    """
    lines = text.split("\n")
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "|" in line and line.startswith("|"):
            table_lines: list[str] = []
            while (
                i < len(lines)
                and "|" in lines[i].strip()
                and lines[i].strip().startswith("|")
            ):
                table_lines.append(lines[i].strip())
                i += 1
            result.extend(_table_to_code_block(table_lines))
        else:
            result.append(lines[i])
            i += 1
    return "\n".join(result)


def _table_to_code_block(table_lines: list[str]) -> list[str]:
    """Convert markdown table rows to an aligned code block table.

    Input:  | Product | Subs | MRR |
            |---------|------|-----|
            | Pro     | 84   | $8K |

    Output: ```
            Product     Subs   MRR
            ─────────────────────────
            Pro          84    $8K
            ```

    Tables are capped at ~58 chars wide to prevent overflow in Slack.
    Column headers are truncated if needed to fit.
    """
    rows: list[list[str]] = []
    for line in table_lines:
        cells = [c.strip() for c in line.strip("|").split("|")]
        # Skip separator rows (---|---|---)
        if cells and not all(
            c.replace("-", "").replace(":", "").strip() == "" for c in cells
        ):
            rows.append(cells)

    if len(rows) < 2:
        return table_lines

    # Calculate column widths
    col_count = max(len(row) for row in rows)
    col_widths = [0] * col_count
    for row in rows:
        for j, cell in enumerate(row):
            if j < col_count:
                col_widths[j] = max(col_widths[j], len(cell))

    # Add padding (min 2 spaces between columns)
    col_widths = [w + 2 for w in col_widths]

    # Cap total table width at ~58 chars to prevent Slack overflow
    _MAX_TABLE_WIDTH = 58
    total_width = sum(col_widths)
    if total_width > _MAX_TABLE_WIDTH:
        # Proportionally shrink columns, but keep minimum of 4 chars each
        scale = _MAX_TABLE_WIDTH / total_width
        col_widths = [max(4, int(w * scale)) for w in col_widths]
        # Truncate cell values that exceed their column width
        for row_idx, row in enumerate(rows):
            for col_idx, cell in enumerate(row):
                if col_idx < len(col_widths) and len(cell) > col_widths[col_idx] - 1:
                    rows[row_idx][col_idx] = cell[:col_widths[col_idx] - 2] + "…"

    # Build aligned table
    output: list[str] = ["```"]

    # Header row
    header = "".join(
        rows[0][j].ljust(col_widths[j]) if j < len(rows[0]) else " " * col_widths[j]
        for j in range(col_count)
    )
    output.append(header.rstrip())

    # Separator with simple dashes (avoids wide Unicode rendering issues)
    separator = "-" * min(sum(col_widths), 55)
    output.append(separator)

    # Data rows
    for row in rows[1:]:
        line_str = "".join(
            _align_cell(row[j] if j < len(row) else "", col_widths[j])
            for j in range(col_count)
        )
        output.append(line_str.rstrip())

    output.append("```")
    return [""] + output + [""]


def _align_cell(cell: str, width: int) -> str:
    """Align a cell value. Right-align numbers/currency, left-align text."""
    stripped = cell.strip()
    if not stripped:
        return " " * width
    # Right-align if it looks like a number, currency, or percentage
    if (
        stripped.replace(",", "").replace(".", "").replace("-", "").isdigit()
        or stripped.startswith("$")
        or stripped.endswith("%")
        or stripped.endswith("/mo")
        or stripped.endswith("/yr")
    ):
        return stripped.rjust(width)
    return stripped.ljust(width)


# ═══════════════════════════════════════════════════════════════════════
# LAYER 3: TONE VALIDATOR
# ═══════════════════════════════════════════════════════════════════════

_TONE_REJECT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"I wasn't able to", re.IGNORECASE),
    re.compile(r"Could you try rephrasing", re.IGNORECASE),
    re.compile(r"running into a loop", re.IGNORECASE),
    re.compile(r"tool call[s]? failed", re.IGNORECASE),
    re.compile(r"I hit a snag", re.IGNORECASE),
    re.compile(r"Something went wrong", re.IGNORECASE),
    re.compile(r"several tool calls", re.IGNORECASE),
    re.compile(r"try rephrasing", re.IGNORECASE),
    re.compile(r"after several attempts", re.IGNORECASE),
    re.compile(r"I was(?:n't| not) able to complete", re.IGNORECASE),
    re.compile(r"(?:great|excellent|wonderful|fantastic) question", re.IGNORECASE),
    re.compile(r"(?:I'd be )?happy to help", re.IGNORECASE),
    re.compile(r"it's worth noting", re.IGNORECASE),
    re.compile(r"let me delve into", re.IGNORECASE),
]

_TONE_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"I (?:wasn't|was not) able to (?:complete |finish )?(?:the |this |your )?request[^.]*\.?", re.IGNORECASE),
        "Let me try a different approach on this.",
    ),
    (
        re.compile(r"(?:Could you |Please )try rephrasing[^.]*\.?", re.IGNORECASE),
        "Could you give me a bit more detail on what you're looking for?",
    ),
    (
        re.compile(r"I hit a snag[^.]*\.?", re.IGNORECASE),
        "Let me take another look at this.",
    ),
    (
        re.compile(r"Something went wrong[^.]*\.?", re.IGNORECASE),
        "Working on getting that sorted.",
    ),
    (
        re.compile(r"(?:That's a |This is a |What a )?(?:great|excellent|wonderful|fantastic) question[!.,]?\s*", re.IGNORECASE),
        "",
    ),
    (
        re.compile(r"I'd be happy to help[!.,]?\s*", re.IGNORECASE),
        "",
    ),
    (
        re.compile(r"[Ii]t's worth noting that\s*", re.IGNORECASE),
        "",
    ),
    (
        re.compile(r"[Ll]et me delve into\s*", re.IGNORECASE),
        "Here's ",
    ),
    (
        re.compile(r"(?:Could you |Can you )?refresh my memory[^.]*\.?\s*", re.IGNORECASE),
        "I don't have context on that, could you fill me in? ",
    ),
    (
        re.compile(r"I(?:'ve| have) (?:that |it )?saved[^.]*\.?\s*", re.IGNORECASE),
        "I'll keep that in mind. ",
    ),
    (
        re.compile(r"\*?Summary Table\*?:?\s*\n", re.IGNORECASE),
        "*Quick Summary*\n",
    ),
]


_BROKEN_URLS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"<https?://[a-z]{2,15}\.\|[^>]*>", re.IGNORECASE),
        "_(link unavailable, use `/lucy connect <service>`)_",
    ),
    (
        re.compile(r"<https?://[a-z]{2,15}\.>", re.IGNORECASE),
        "_(link unavailable, use `/lucy connect <service>`)_",
    ),
    (
        re.compile(r"\[([^\]]*)\]\(https?://[a-z]{2,15}\.[)\s]", re.IGNORECASE),
        "_(link unavailable, use `/lucy connect <service>`)_ ",
    ),
    (
        re.compile(r"https?://[a-z]{2,15}\.\s", re.IGNORECASE),
        "_(link unavailable, use `/lucy connect <service>`)_ ",
    ),
]


def _fix_broken_urls(text: str) -> str:
    """Remove broken/truncated URLs in both plain and Slack mrkdwn format."""
    for pattern, replacement in _BROKEN_URLS:
        text = pattern.sub(replacement, text)
    return text


def _validate_tone(text: str) -> str:
    for pattern, replacement in _TONE_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text


# ═══════════════════════════════════════════════════════════════════════
# LAYER 4: DE-AI ENGINE
# Two-tier system: fast regex detection + LLM contextual rewrite
# ═══════════════════════════════════════════════════════════════════════

# --- Detection patterns: each (regex, weight, category) ---

_AI_TELL_PATTERNS: list[tuple[re.Pattern[str], int, str]] = [
    # PUNCTUATION
    (re.compile(r" — "), 3, "em_dash"),
    (re.compile(r"—"), 2, "em_dash"),
    (re.compile(r" – "), 1, "en_dash"),

    # POWER WORDS (the classic LLM vocabulary)
    (re.compile(
        r"\b(?:delve|tapestry|landscape|beacon|pivotal|testament|"
        r"multifaceted|underpinning|underscores|palpable|enigmatic|"
        r"plethora|myriad|paramount|groundbreaking|game-?changing|"
        r"cutting-?edge|holistic|synergy|synergize|leverage|leveraging|"
        r"spearhead|spearheading|bolster|bolstering|unleash|unlock|"
        r"foster|empower|embark|illuminate|elucidate|resonate|"
        r"revolutionize|revolutionizing|elevate|grapple|showcase|"
        r"streamline|harness|harnessing|catapult|supercharge|"
        r"cornerstone|linchpin|bedrock|hallmark|touchstone|"
        r"realm|sphere|arena|facet|nuance|intricacies|"
        r"robust|seamless|seamlessly|comprehensive|meticulous|"
        r"intricate|versatile|dynamic|innovative|transformative|"
        r"endeavor|strive|forge|cultivate|spearhead)\b",
        re.IGNORECASE,
    ), 2, "power_word"),

    # FORMAL TRANSITIONS
    (re.compile(
        r"\b(?:Moreover|Furthermore|Additionally|Consequently|"
        r"Nevertheless|Nonetheless|Henceforth|Accordingly|"
        r"In conclusion|To summarize|In summary|"
        r"It is (?:worth|important to) not(?:e|ing)|"
        r"It bears mentioning|It should be noted|"
        r"As previously mentioned|As noted above|"
        r"In light of|With regard to|In terms of|"
        r"From a broader perspective|On the other hand|"
        r"By the same token|In this context)\b",
        re.IGNORECASE,
    ), 2, "formal_transition"),

    # HEDGING / WEASEL PHRASES
    (re.compile(
        r"\b(?:generally speaking|more often than not|"
        r"it's (?:also )?important to (?:note|remember|consider|mention|highlight)|"
        r"it's crucial to|it's essential to|"
        r"it's worth (?:noting|mentioning|highlighting|pointing out)|"
        r"to some extent|in many ways|for the most part|"
        r"at the end of the day|when all is said and done|"
        r"all things considered|that being said|"
        r"having said that|with that in mind|"
        r"needless to say|goes without saying|"
        r"it goes without saying)\b",
        re.IGNORECASE,
    ), 2, "hedging"),

    # CHATBOT FILLERS / SYCOPHANCY
    (re.compile(
        r"(?:^|\. )(?:Absolutely|Certainly|Of course|Sure thing)[!.,]",
        re.IGNORECASE | re.MULTILINE,
    ), 2, "sycophancy"),
    (re.compile(
        r"(?:Hope (?:this|that) helps|"
        r"(?:I )?hope (?:this|that) (?:was|is) helpful|"
        r"Let me know if you (?:need|have|want) (?:anything|any|more)|"
        r"(?:Feel free to|Don't hesitate to) (?:ask|reach out|let me know)|"
        r"Happy to (?:help|assist|answer|elaborate))[!.]?",
        re.IGNORECASE,
    ), 3, "chatbot_closer"),
    (re.compile(
        r"(?:(?:That's|What) an? (?:great|excellent|wonderful|fantastic|interesting|insightful|thoughtful) "
        r"(?:question|point|observation|thought|idea))[!.,]?\s*",
        re.IGNORECASE,
    ), 3, "sycophancy"),

    # STRUCTURAL PATTERNS
    (re.compile(r"It's not (?:just )?(?:about )?X[,;] it's (?:about )?Y", re.IGNORECASE), 2, "structure"),
    (re.compile(
        r"(?:Let's (?:dive|jump) (?:in|into|right in)|"
        r"Without further ado|"
        r"Let me break (?:this|it) down|"
        r"Here's (?:a |the )?(?:breakdown|overview|rundown|lowdown)|"
        r"(?:So,? )?(?:let's|allow me to) (?:explore|unpack|dissect))",
        re.IGNORECASE,
    ), 2, "structure"),

    # EXCESSIVE EXCLAMATION (3+ in a message)
    (re.compile(r"(?:.*!.*){3,}"), 1, "exclamation"),

    # "IN ESSENCE" / "IN A NUTSHELL" summaries
    (re.compile(
        r"\b(?:In essence|In a nutshell|To put it simply|"
        r"Simply put|Long story short|Bottom line|"
        r"The (?:key|main|bottom) (?:takeaway|point|thing) (?:is|here))\b",
        re.IGNORECASE,
    ), 1, "summary_phrase"),

    # OVER-STRUCTURED OPENINGS
    (re.compile(
        r"(?:Here are|Here's) (?:\d+|a few|some|several) "
        r"(?:key |main |important |critical )?(?:things|points|considerations|factors|aspects|ways|steps|tips)",
        re.IGNORECASE,
    ), 1, "listicle_opener"),

    # "PROACTIVE INSIGHT" labels (Lucy-specific)
    (re.compile(r"\*?Proactive (?:Insight|Follow-?up|Suggestion)\*?:?\s*", re.IGNORECASE), 2, "label"),
]

# ── LLM rewrite threshold ──────────────────────────────────────────
# DISABLED (was 3, now 999). The LLM rewrite (Tier 2) is intentionally
# unreachable because:
#
#   1. The rewriter prompt says "sound like a smart colleague" — it has
#      zero knowledge of Lucy's personality from SOUL.md, so it strips
#      her voice and replaces it with generic corporate-speak.
#   2. It rewrites the ENTIRE text on a cheap model (minimax-m2.5 at
#      0.4 temperature), which can restructure numbered lists, merge
#      bullet points, and lose Slack formatting.
#   3. The regex pass (Tier 1) already handles ~90% of AI tells
#      (em dashes, power words, chatbot closers) without any of these
#      side effects.
#
# The code is preserved so it can be re-enabled as a safety net if we
# ever need it (e.g. with a personality-aware prompt and better model).
# To re-enable, lower this back to 3 (or whatever score makes sense).
# ───────────────────────────────────────────────────────────────────
_LLM_REWRITE_THRESHOLD = 999
_LLM_REWRITE_MIN_CHARS = 120
_DEAI_MAX_TOKENS_MULTIPLIER = 2
_DEAI_MIN_LENGTH_RATIO = 0.3
_DEAI_MAX_LENGTH_RATIO = 2.0


def _detect_ai_tells(text: str) -> list[tuple[str, int, str]]:
    """Scan text for AI-tell patterns. Returns list of (match, weight, category)."""
    tells: list[tuple[str, int, str]] = []
    for pattern, weight, category in _AI_TELL_PATTERNS:
        for match in pattern.finditer(text):
            tells.append((match.group(0), weight, category))
    return tells


def _ai_tell_score(text: str) -> int:
    """Total weighted score of AI tells detected in text."""
    return sum(weight for _, weight, _ in _detect_ai_tells(text))


# --- Tier 1: Regex safety net (always runs, instant) ---

_REGEX_DEAI_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Dashes
    (re.compile(r" — "), ", "),
    (re.compile(r"—"), ", "),
    (re.compile(r" – "), "-"),
    (re.compile(r"–"), "-"),

    # Power words (strip them, surrounding context usually reads fine)
    (re.compile(
        r"\b(?:delve|tapestry|landscape(?:s)?|beacon|pivotal|"
        r"testament|multifaceted|underpinning|underscores|"
        r"palpable|enigmatic|plethora|myriad|paramount|"
        r"groundbreaking|holistic|synergy|synergize|"
        r"revolutionize|revolutionizing|elucidate|"
        r"harnessing|spearheading|bolstering)\b",
        re.IGNORECASE,
    ), ""),

    # Formal transitions (strip, usually beginning-of-sentence filler)
    (re.compile(r"\b(?:Moreover|Furthermore|Additionally|Notably),?\s*", re.IGNORECASE), ""),
    (re.compile(r"(?:It's worth noting that|It is worth noting that|It bears mentioning that)\s*", re.IGNORECASE), ""),
    (re.compile(r"(?:It's important to note that|It is important to note that)\s*", re.IGNORECASE), ""),

    # Chatbot closers (match anywhere, not just end-of-string)
    (re.compile(r"\s*Hope (?:this|that) helps[!.]?\s*", re.IGNORECASE), " "),
    (re.compile(r"\s*Let me know if you (?:need|have|want) (?:anything|any|more)(?:\s+else)?[!.]?\s*", re.IGNORECASE), " "),
    (re.compile(r"\s*(?:Feel free to|Don't hesitate to) (?:ask|reach out|let me know)[!.]?\s*", re.IGNORECASE), " "),
    (re.compile(r"\s*Happy to (?:help|assist|answer|elaborate)(?:\s+(?:with that|with this|further))?[!.]?\s*", re.IGNORECASE), " "),

    # Sycophantic openers
    (re.compile(
        r"^(?:Absolutely|Certainly|Of course|Sure thing)[!.,]?\s*",
        re.IGNORECASE | re.MULTILINE,
    ), ""),
    (re.compile(
        r"(?:(?:That's|This is|What) an? )?(?:great|excellent|wonderful|fantastic|interesting|insightful|thoughtful) "
        r"(?:question|point|observation|thought|idea)[!.,]?\s*",
        re.IGNORECASE,
    ), ""),

    # Lucy-specific labels
    (re.compile(r"\*?Proactive (?:Insight|Follow-?up|Suggestion)\*?:?\s*", re.IGNORECASE), ""),

    # Cleanup double spaces created by stripping
    (re.compile(r"  +"), " "),
    # Cleanup orphaned commas from stripped words
    (re.compile(r" , "), ", "),
    (re.compile(r"^,\s*", re.MULTILINE), ""),
]


def _regex_deai(text: str) -> str:
    """Fast regex-based de-AI pass. Always runs as safety net."""
    for pattern, replacement in _REGEX_DEAI_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# --- Tier 2: LLM-based contextual rewrite ---

_DEAI_SYSTEM_PROMPT = """\
You are a copy editor. Your ONE job: rewrite the text so it reads like a real \
person wrote it on Slack, not like an AI generated it.

FIX these problems (if present):
- Em dashes: replace with commas, periods, or rewrite the clause
- Power words: delve, pivotal, tapestry, beacon, leverage, unleash, unlock, \
foster, empower, synergy, game-changing, holistic, robust, seamless, \
comprehensive, transformative, innovative, dynamic, groundbreaking, \
multifaceted, paramount, plethora, harness, spearhead, illuminate, resonate, \
cultivate, cornerstone, bedrock
- Formal transitions: Moreover, Furthermore, Additionally, Consequently, \
Notably, Nevertheless, In light of, With regard to, In terms of
- Hedging: generally speaking, it's worth noting, it's important to note, \
more often than not, at the end of the day, all things considered, that being \
said, having said that, needless to say
- Sycophancy: "Great question!", "Absolutely!", "Certainly!", "Happy to help!", \
"Hope this helps!", "Feel free to ask!"
- Chatbot closers: "Let me know if you need anything else", "Don't hesitate \
to reach out"
- Listicle openers: "Here are 5 key things...", "Let's dive in", "Without \
further ado", "Let me break this down"
- Uniform sentence length: mix short and long, punch up the rhythm
- Bullet monotony: if bullets all start the same way, vary them
- Over-explanation: don't repeat the same point, move forward

DO NOT change:
- Facts, numbers, percentages, dates
- URLs, links, email addresses
- Code blocks or inline code (anything in backticks)
- Technical terms, product names, proper nouns
- The core meaning or intent
- Slack formatting: *bold*, _italic_, ~strike~, bullet characters

RULES:
- Output ONLY the rewritten text, nothing else
- Keep roughly the same length (within 15%)
- Sound like a smart colleague chatting on Slack
- Use contractions naturally (don't force them)
- Be direct and assertive, not wishy-washy
- If the text is already fine, return it unchanged"""

_DEAI_TIMEOUT = 4.0


async def _llm_deai_rewrite(text: str, tells: list[tuple[str, int, str]]) -> str | None:
    """Use a fast LLM to contextually rewrite AI tells.

    Returns the rewritten text, or None if the LLM call fails.
    """
    try:
        from lucy.core.openclaw import ChatConfig, get_openclaw_client

        client = await get_openclaw_client()

        categories_found = sorted({cat for _, _, cat in tells})
        user_msg = (
            f"Rewrite this text. Issues detected: {', '.join(categories_found)}\n\n"
            f"---\n{text}\n---"
        )

        response = await asyncio.wait_for(
            client.chat_completion(
                messages=[{"role": "user", "content": user_msg}],
                config=ChatConfig(
                    model=settings.model_tier_default,
                    system_prompt=_DEAI_SYSTEM_PROMPT,
                    max_tokens=min(len(text) * _DEAI_MAX_TOKENS_MULTIPLIER, LLMPresets.DEAI_REWRITE.max_tokens),
                    temperature=LLMPresets.DEAI_REWRITE.temperature,
                ),
            ),
            timeout=_DEAI_TIMEOUT,
        )

        result = (response.content or "").strip()

        if result.startswith("---"):
            result = result.lstrip("-").strip()
        if result.endswith("---"):
            result = result.rstrip("-").strip()

        if not result or len(result) < len(text) * _DEAI_MIN_LENGTH_RATIO:
            logger.debug("deai_rewrite_too_short", original_len=len(text), result_len=len(result))
            return None

        if len(result) > len(text) * _DEAI_MAX_LENGTH_RATIO:
            logger.debug("deai_rewrite_too_long", original_len=len(text), result_len=len(result))
            return None

        new_score = _ai_tell_score(result)
        old_score = sum(w for _, w, _ in tells)
        if new_score >= old_score:
            logger.debug(
                "deai_rewrite_no_improvement",
                old_score=old_score,
                new_score=new_score,
            )
            return _regex_deai(text)

        logger.info(
            "deai_rewrite_applied",
            old_score=old_score,
            new_score=new_score,
            categories=categories_found,
        )
        return result

    except asyncio.TimeoutError:
        logger.debug("deai_rewrite_timeout")
        return None
    except Exception as exc:
        logger.debug("deai_rewrite_failed", error=str(exc))
        return None


async def _deai(text: str) -> str:
    """De-AI: detect tells and fix via regex. LLM rewrite path exists but is
    disabled (threshold=999) — see comment on _LLM_REWRITE_THRESHOLD above."""
    tells = _detect_ai_tells(text)

    if not tells:
        return text

    score = sum(w for _, w, _ in tells)

    if score >= _LLM_REWRITE_THRESHOLD and len(text) >= _LLM_REWRITE_MIN_CHARS:
        rewritten = await _llm_deai_rewrite(text, tells)
        if rewritten is not None:
            return rewritten

    return _regex_deai(text)


# ═══════════════════════════════════════════════════════════════════════
# LAYER 2.5: PROACTIVE ENDINGS — AUTO-CTA & RELATED SUGGESTIONS
# ═══════════════════════════════════════════════════════════════════════

# Patterns that detect an existing call-to-action in the response tail.
# Only matches CTAs that survive de-AI (excludes generic closers like
# "Happy to help!" which get stripped in Layer 4).
_HAS_CTA_RE = re.compile(
    r"(?:want me to|would you like|shall I|should I|"
    r"I can also|I could also|like me to|"
    r"want to explore|just say the word|if you'd like|if you want me|"
    r"need (?:any )?help with|dive deeper|dig deeper|"
    r"let me know (?:if|what|which|when|how))",
    re.IGNORECASE,
)

# ── Question type detection (simplified from router.py) ──────────────

_Q_COMPARISON_RE = re.compile(
    r"(?:vs\.?\s|versus|compare|differences?\s+between|better\s+(?:than|between)|"
    r"pros?\s+(?:and|&)\s+cons?|advantages?\s+(?:and|&)\s+disadvantages?|"
    r"which\s+(?:is|one|should))",
    re.IGNORECASE,
)
_Q_HOWTO_RE = re.compile(
    r"(?:how\s+(?:to|do\s+(?:I|you|we))|set\s+up|configure|implement|"
    r"guide\s+me|steps?\s+(?:to|for)|get\s+started)",
    re.IGNORECASE,
)
_Q_CODE_RE = re.compile(
    r"(?:\bcode\b|\bscript\b|\bfunction\b|\bdebug\b|\bdeploy\b|\bregex\b|"
    r"api\s+endpoint|dockerfile|ci/?cd|refactor|"
    r"create\s+a\s+(?:app|program)|\blambda\b)",
    re.IGNORECASE,
)
_Q_CREATIVE_RE = re.compile(
    r"(?:^|\b)(?:write|draft|compose|rewrite|edit|proofread)\s+"
    r"(?:me\s+|us\s+)?(?:a\s+|an\s+|the\s+|some\s+)?"
    r"(?:email|message|announcement|newsletter|blog|post|"
    r"description|pitch|proposal|tagline|headline|copy|letter|memo)",
    re.IGNORECASE,
)
_Q_KNOWLEDGE_RE = re.compile(
    r"(?:what\s+(?:is|are)\s|explain\s|how\s+does\s|tell\s+me\s+about\s|"
    r"overview\s+of\s|walk\s+me\s+through\s|describe\s|teach\s+me)",
    re.IGNORECASE,
)

# Context-aware CTAs by question type
_CTA_MAP: dict[str, str] = {
    "knowledge": "\n\nWant me to dive deeper into any of these, or explore a specific use case?",
    "comparison": "\n\nWant me to go deeper on any of these, or help you evaluate for your specific situation?",
    "howto": "\n\nWant me to walk through any of these steps in more detail, or help you set it up?",
    "creative": "\n\nWant me to adjust the tone, add more detail, or create a variation?",
    "code": "\n\nWant me to explain any part of this, or help you adapt it for your setup?",
    "default": "\n\nLet me know if you'd like me to dig deeper into any of this!",
}

# ── Related-topic suggestions (knowledge/comparison only) ────────────
# Maps topic keywords to one-sentence related exploration prompt.
# Conservative: only fires when a clear topic cluster is detected.

_RELATED_TOPICS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:database|sql|postgres|mysql|mongo|dynamo|supabase)\b", re.IGNORECASE),
     "how caching layers like Redis interact with your DB setup"),
    (re.compile(r"\b(?:api|rest|graphql|grpc|endpoint)\b", re.IGNORECASE),
     "authentication patterns and rate limiting strategies"),
    (re.compile(r"\b(?:ci/?cd|pipeline|github\s+actions?|jenkins|deployment)\b", re.IGNORECASE),
     "rollback strategies and canary deployments"),
    (re.compile(r"\b(?:docker|container|kubernetes|k8s)\b", re.IGNORECASE),
     "orchestration trade-offs between Docker Compose, ECS, and K8s"),
    (re.compile(r"\b(?:react|next\.?js|vue|angular|svelte|frontend)\b", re.IGNORECASE),
     "server-side rendering vs. static generation trade-offs"),
    (re.compile(r"\b(?:aws|gcp|azure|cloud|serverless)\b", re.IGNORECASE),
     "cloud cost optimization and multi-region architecture"),
    (re.compile(r"\b(?:auth|oauth|jwt|session|login|sso)\b", re.IGNORECASE),
     "token refresh strategies and session management best practices"),
    (re.compile(r"\b(?:testing|unit\s+test|e2e|integration\s+test|jest|pytest)\b", re.IGNORECASE),
     "test coverage strategies and when to use mocks vs. integration tests"),
    (re.compile(r"\b(?:typescript|python|rust|go(?:lang)?|java|node)\b", re.IGNORECASE),
     "project structure patterns and dependency management for your stack"),
    (re.compile(r"\b(?:security|encryption|https|ssl|vulnerability)\b", re.IGNORECASE),
     "compliance frameworks and security audit checklists"),
    (re.compile(r"\b(?:microservice|monolith|architecture|system\s+design)\b", re.IGNORECASE),
     "service communication patterns and data consistency strategies"),
    (re.compile(r"\b(?:git|branch|merge|rebase|version\s+control)\b", re.IGNORECASE),
     "branching strategies like trunk-based development vs. GitFlow"),
    (re.compile(r"\b(?:ai|ml|machine\s+learning|llm|gpt|model)\b", re.IGNORECASE),
     "prompt engineering patterns and model evaluation strategies"),
    (re.compile(r"\b(?:monitoring|logging|observability|metrics|alert)\b", re.IGNORECASE),
     "the three pillars of observability: logs, metrics, and traces"),
    (re.compile(r"\b(?:startup|saas|pricing|business\s+model|revenue)\b", re.IGNORECASE),
     "pricing psychology and how to structure tier-based plans"),
    (re.compile(r"\b(?:seo|marketing|growth|conversion|analytics)\b", re.IGNORECASE),
     "content strategy and technical SEO fundamentals"),
    (re.compile(r"\b(?:performance|optimization|speed|latency|caching)\b", re.IGNORECASE),
     "profiling tools and the most common performance bottlenecks"),
]


def _classify_question_type(question: str) -> str:
    """Classify a user question into a type for CTA selection."""
    if not question:
        return "default"
    # Order matters: more specific patterns first
    if _Q_COMPARISON_RE.search(question):
        return "comparison"
    if _Q_HOWTO_RE.search(question):
        return "howto"
    if _Q_CODE_RE.search(question):
        return "code"
    if _Q_CREATIVE_RE.search(question):
        return "creative"
    if _Q_KNOWLEDGE_RE.search(question):
        return "knowledge"
    return "default"


def _find_related_topic(question: str) -> str | None:
    """Find a related topic suggestion based on question keywords.

    Returns a one-sentence suggestion or None if no confident match.
    Only used for knowledge/comparison questions.
    """
    for pattern, suggestion in _RELATED_TOPICS:
        if pattern.search(question):
            return suggestion
    return None


def _ensure_actionable_ending(text: str, question: str = "") -> str:
    """Append a contextual CTA if the response is substantive but trails off.

    Triggered when:
    - Response is >150 words (substantive, not greetings/quick answers)
    - No existing CTA detected in the last 300 chars of the response

    For knowledge/comparison questions, also adds a one-line related
    topic suggestion when a confident topic match is found.
    """
    word_count = len(text.split())
    if word_count <= 150:
        return text

    # Check the tail of the response for existing CTAs
    tail = text[-300:] if len(text) > 300 else text
    if _HAS_CTA_RE.search(tail):
        return text

    q_type = _classify_question_type(question)
    cta = _CTA_MAP.get(q_type, _CTA_MAP["default"])
    text = text.rstrip() + cta

    # For knowledge/comparison: optionally add a related topic hint
    if q_type in ("knowledge", "comparison") and question:
        related = _find_related_topic(question)
        if related:
            text += f"\n💡 *Related*: You might also want to explore {related}."

    return text


# ═══════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════


async def _verify_facts(text: str) -> str:
    """Layer 0.5: Verify factual claims and correct hallucinations.

    Corrects: wrong day-of-week, broken URLs (404/410)
    Flags (logged): stale version numbers, outdated pricing, stale temporal claims
    """
    try:
        result = await verify_claims(text, validate_urls=True, url_timeout=2.0)
        return result.corrected_text
    except Exception as exc:
        # Never let fact verification break the pipeline
        logger.warning("fact_verification_failed", error=str(exc))
        return text


def _verify_facts_sync(text: str) -> str:
    """Layer 0.5 sync: date/day-of-week verification only (no URL checks)."""
    try:
        result = verify_claims_sync(text)
        return result.corrected_text
    except Exception:
        return text


async def process_output(text: str | None, question: str = "") -> str:
    """Run all output layers on a message before posting to Slack.

    Pipeline order:
      Layer 0:   Strip internal content (planning, self-correction, XML tags)
      Layer 0.5: Fact verification — correct dates, validate URLs, flag stale claims
      Layer 1:   Sanitize paths, tool names, internal references
      Layer 2:   Convert Markdown to Slack mrkdwn
      Layer 2.5: Proactive endings — auto-CTA & related topic suggestions
      Layer 3:   Validate tone (catch robotic patterns)
      Layer 4:   De-AI engine (regex pass, LLM rewrite disabled)

    Now async: the de-AI engine may invoke a fast LLM call for contextual
    rewrites when significant AI tells are detected. Falls back to instant
    regex if the LLM is unavailable or the text is clean.
    """
    if not text or not text.strip():
        return text or ""

    # Layer 0: Strip internal reasoning, self-correction, leaked XML tags
    text = strip_internal_content(text)
    if not text.strip():
        return "I've completed the task."

    # Layer 0.5: Fact verification — correct hallucinated dates, validate URLs,
    # flag stale version/pricing claims (W7-TRUTH)
    text = await _verify_facts(text)

    text = _sanitize(text)
    text = _fix_broken_urls(text)
    text = _convert_markdown_to_slack(text)
    text = _ensure_actionable_ending(text, question)
    text = _validate_tone(text)
    text = await _deai(text)
    return text.strip()


def process_output_sync(text: str | None, question: str = "") -> str:
    """Synchronous fallback for contexts where async isn't available.

    Runs the full pipeline except the LLM rewrite tier. Uses regex-only
    de-AI instead. Prefer the async version when possible.
    """
    if not text or not text.strip():
        return text or ""

    # Layer 0: Strip internal reasoning, self-correction, leaked XML tags
    text = strip_internal_content(text)
    if not text.strip():
        return "I've completed the task."

    # Layer 0.5: Fact verification sync (dates only, no URL checks)
    text = _verify_facts_sync(text)

    text = _sanitize(text)
    text = _fix_broken_urls(text)
    text = _convert_markdown_to_slack(text)
    text = _ensure_actionable_ending(text, question)
    text = _validate_tone(text)
    text = _regex_deai(text)
    return text.strip()
