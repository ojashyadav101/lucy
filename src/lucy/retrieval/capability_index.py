"""Capability Index — workspace-scoped BM25 tool index for Lucy.

Stores tool metadata in memory and scores any query against it using
BM25 in < 1 ms, returning only the K most relevant schemas to the LLM.

Architecture
------------
  CapabilityIndex (process singleton)
    +-- WorkspaceIndex (per workspace_id)
          +-- ToolRecord x N  (metadata + tokenised form)
          +-- IDF table  (re-computed on each add_tools call)

BM25 parameters: k1=1.5, b=0.75 (Okapi BM25 standard values).

All mutations happen under a per-workspace asyncio.Lock for thread-safety.
"""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger()

_BM25_K1 = 1.5
_BM25_B = 0.75

MIN_INDEXED_TOOLS = 5

_INDEX_TTL_SECONDS = 300  # 5 minutes

# Only truly meaningless words. Action verbs like "list", "create", "delete"
# are kept because they differentiate tools (e.g., "list events" vs "delete event").
_STOPWORDS = frozenset(
    "a an the and or of to in for on with by at is are was were be been"
    " this that it its they their them what which who when where how".split()
)

# Optional query-time synonym expansion. These improve recall but are NOT
# required — BM25 still works on raw tool name/description tokens without them.
_QUERY_SYNONYMS: dict[str, list[str]] = {
    "meeting": ["calendar", "event", "events"],
    "meetings": ["calendar", "events", "list"],
    "schedule": ["calendar", "events", "list"],
    "email": ["mail", "gmail", "message", "fetch"],
    "emails": ["mail", "gmail", "messages", "fetch"],
    "inbox": ["mail", "gmail", "messages", "fetch"],
    "ticket": ["issue"],
    "tickets": ["issues"],
    "bug": ["issue"],
    "task": ["issue", "todo"],
    "tasks": ["issues", "todos"],
    "file": ["drive", "document", "find"],
    "files": ["drive", "documents", "find", "list"],
    "pr": ["pull", "request"],
    "repo": ["repository"],
    "repos": ["repositories"],
    "next": ["list", "find", "get", "upcoming"],
    "show": ["list", "find", "get", "fetch"],
    "check": ["list", "find", "get", "fetch"],
    "what": ["list", "get", "find"],
    "search": ["find", "list", "query"],
    "find": ["search", "list", "get"],
    "recent": ["fetch", "list", "latest"],
    "latest": ["fetch", "list", "recent"],
}


def _auto_split_compound(token: str) -> list[str]:
    """Split compound tokens like 'googlecalendar' into constituent parts.

    Generates ALL valid splits (both halves >= 3 chars) so that at least one
    matches real word boundaries. This is more inclusive than picking a single
    "best" split and improves BM25 recall without any hardcoded dictionary.
    """
    if len(token) < 6:
        return []
    parts: list[str] = []
    seen: set[str] = set()
    for i in range(3, len(token) - 2):
        left, right = token[:i], token[i:]
        if len(left) >= 3 and len(right) >= 3 and left.isalpha() and right.isalpha():
            for p in (left, right):
                if p not in seen and p != token:
                    seen.add(p)
                    parts.append(p)
    return parts


def tokenise(text: str) -> list[str]:
    """Convert a tool name or description string to BM25 tokens.

    Handles SCREAMING_SNAKE_CASE, CamelCase, and plain prose generically.
    No integration-specific logic.
    """
    parts = re.split(r"[_\s\-/]+", text)

    tokens: list[str] = []
    for part in parts:
        split = re.sub(r"([a-z])([A-Z])", r"\1 \2", part)
        tokens.extend(split.lower().split())

    tokens = [t for t in tokens if len(t) > 1 and t.isalpha() and t not in _STOPWORDS]

    expanded: list[str] = []
    for tok in tokens:
        expanded.append(tok)
        parts = _auto_split_compound(tok)
        if parts:
            expanded.extend(parts)
    return expanded


def expand_query(tokens: list[str]) -> list[str]:
    """Add synonym tokens to a query token list (query-time only)."""
    expanded = list(tokens)
    for tok in tokens:
        syns = _QUERY_SYNONYMS.get(tok)
        if syns:
            expanded.extend(syns)
    seen: set[str] = set()
    result: list[str] = []
    for t in expanded:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result


def _compute_idf(corpus: list[list[str]]) -> dict[str, float]:
    """Compute IDF (log-smoothed) for all tokens in a corpus."""
    n = len(corpus)
    if n == 0:
        return {}
    df: dict[str, int] = {}
    for doc in corpus:
        for tok in set(doc):
            df[tok] = df.get(tok, 0) + 1
    return {
        tok: math.log(1 + (n - freq + 0.5) / (freq + 0.5))
        for tok, freq in df.items()
    }


def bm25_score(
    query_tokens: list[str],
    doc_tokens: list[str],
    idf: dict[str, float],
    avg_doc_len: float,
) -> float:
    """Compute BM25 score for (query, document) pair."""
    if not query_tokens or not doc_tokens:
        return 0.0
    doc_len = len(doc_tokens)
    tf_map: dict[str, int] = {}
    for tok in doc_tokens:
        tf_map[tok] = tf_map.get(tok, 0) + 1

    score = 0.0
    for tok in query_tokens:
        if tok not in tf_map:
            continue
        tf = tf_map[tok]
        idf_val = idf.get(tok, 0.0)
        numerator = tf * (_BM25_K1 + 1)
        denominator = tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * doc_len / max(avg_doc_len, 1))
        score += idf_val * (numerator / denominator)
    return score


@dataclass
class ToolRecord:
    """Indexed representation of a single Composio tool."""
    name: str
    app: str
    description: str
    tokens: list[str]
    schema: dict[str, Any]
    usage_count: int = 0


@dataclass
class RetrievalResult:
    """Result from BM25 retrieval with score information."""
    tools: list[dict[str, Any]]
    top_score: float
    scores: list[tuple[float, str]]  # (score, tool_name) for debugging


class WorkspaceIndex:
    """BM25 index for a single workspace."""

    def __init__(self, workspace_id: str) -> None:
        self.workspace_id = workspace_id
        self._records: dict[str, ToolRecord] = {}
        self._idf: dict[str, float] = {}
        self._avg_doc_len: float = 0.0
        self._lock = asyncio.Lock()
        self._indexed_at: float = 0.0

    @property
    def size(self) -> int:
        return len(self._records)

    @property
    def is_stale(self) -> bool:
        import time
        return (time.monotonic() - self._indexed_at) > _INDEX_TTL_SECONDS

    async def add_tools(self, tools: list[dict[str, Any]], app_slug: str = "") -> int:
        """Index a batch of OpenAI-format tool schemas.

        Args:
            tools: List of OpenAI-format tool schemas.
            app_slug: The toolkit slug these tools belong to (e.g. 'googlecalendar').
                      If empty, inferred from tool name prefix.

        Returns the number of new tools added.
        """
        if not tools:
            return 0
        async with self._lock:
            return self._add_tools_locked(tools, app_slug)

    def _add_tools_locked(self, tools: list[dict[str, Any]], app_slug: str) -> int:
        import time
        added = 0
        for schema in tools:
            fn = schema.get("function", {})
            name: str = fn.get("name", "")
            if not name or name in self._records:
                continue
            desc: str = fn.get("description", "")
            app = app_slug or self._infer_app_generic(name)
            tokens = tokenise(f"{name} {desc}")
            self._records[name] = ToolRecord(
                name=name,
                app=app,
                description=desc,
                tokens=tokens,
                schema=schema,
            )
            added += 1

        if added > 0:
            corpus = [r.tokens for r in self._records.values()]
            self._idf = _compute_idf(corpus)
            total_len = sum(len(r.tokens) for r in self._records.values())
            self._avg_doc_len = total_len / max(len(corpus), 1)
            self._indexed_at = time.monotonic()

        return added

    def retrieve(
        self,
        query: str,
        k: int = 10,
        connected_apps: set[str] | None = None,
        boost_recent: bool = True,
        min_per_app: int = 3,
    ) -> RetrievalResult:
        """Return the top-K tool schemas with BM25 scores.

        Uses per-app minimum representation to guarantee every connected app
        contributes at least `min_per_app` tools, preventing dominant apps
        from crowding out others in multi-app queries.

        Returns a RetrievalResult containing tools, top_score, and debug info.
        """
        if not self._records:
            return RetrievalResult(tools=[], top_score=0.0, scores=[])

        q_tokens = expand_query(tokenise(query))
        if not q_tokens:
            sorted_records = sorted(self._records.values(), key=lambda r: -r.usage_count)
            return RetrievalResult(
                tools=[r.schema for r in sorted_records[:k]],
                top_score=0.0,
                scores=[],
            )

        all_scores: list[tuple[float, ToolRecord]] = []
        per_app_scores: dict[str, list[tuple[float, ToolRecord]]] = {}
        for record in self._records.values():
            if connected_apps and record.app not in connected_apps:
                continue
            score = bm25_score(q_tokens, record.tokens, self._idf, self._avg_doc_len)
            if boost_recent and record.usage_count > 0:
                score += min(0.5, math.log1p(record.usage_count) * 0.1)
            if score > 0:
                all_scores.append((score, record))
                per_app_scores.setdefault(record.app, []).append((score, record))

        # Phase 1: guarantee min_per_app tools from each connected app
        selected: dict[str, tuple[float, ToolRecord]] = {}
        for app, app_scores in per_app_scores.items():
            app_scores.sort(key=lambda x: -x[0])
            for score, record in app_scores[:min_per_app]:
                selected[record.name] = (score, record)

        # Phase 2: fill remaining slots from global ranking
        all_scores.sort(key=lambda x: -x[0])
        for score, record in all_scores:
            if len(selected) >= k:
                break
            if record.name not in selected:
                selected[record.name] = (score, record)

        final = sorted(selected.values(), key=lambda x: -x[0])
        top_score = final[0][0] if final else 0.0

        return RetrievalResult(
            tools=[record.schema for _, record in final],
            top_score=top_score,
            scores=[(s, r.name) for s, r in final],
        )

    def record_usage(self, tool_name: str) -> None:
        """Increment usage counter for a tool (called after successful execution)."""
        if tool_name in self._records:
            self._records[tool_name].usage_count += 1

    def get_record(self, tool_name: str) -> ToolRecord | None:
        return self._records.get(tool_name)

    @staticmethod
    def _infer_app_generic(tool_name: str) -> str:
        """Infer app slug from tool name prefix generically.

        Composio tools follow {APP}_{ACTION} naming. Extract the prefix
        before the first underscore, lowercased. No hardcoded map needed.
        """
        idx = tool_name.find("_")
        if idx > 0:
            return tool_name[:idx].lower()
        return tool_name.lower()

    def debug_stats(self) -> dict:
        app_counts: dict[str, int] = {}
        for r in self._records.values():
            app_counts[r.app] = app_counts.get(r.app, 0) + 1
        return {
            "workspace_id": self.workspace_id,
            "total_tools": self.size,
            "apps": app_counts,
            "avg_doc_len": round(self._avg_doc_len, 1),
            "idf_vocabulary": len(self._idf),
        }


class CapabilityIndex:
    """Process-global registry of per-workspace BM25 capability indexes."""

    def __init__(self) -> None:
        self._indexes: dict[str, WorkspaceIndex] = {}
        self._lock = asyncio.Lock()

    def get(self, workspace_id: str) -> WorkspaceIndex:
        """Get (or create) the index for a workspace."""
        if workspace_id not in self._indexes:
            self._indexes[workspace_id] = WorkspaceIndex(workspace_id)
        return self._indexes[workspace_id]

    async def invalidate(self, workspace_id: str) -> None:
        """Drop the index for a workspace."""
        async with self._lock:
            self._indexes.pop(workspace_id, None)
        logger.info("capability_index_invalidated", workspace_id=workspace_id)

    @property
    def total_indexed_tools(self) -> int:
        return sum(idx.size for idx in self._indexes.values())

    def snapshot(self) -> dict:
        return {
            "workspaces": len(self._indexes),
            "total_tools": self.total_indexed_tools,
            "per_workspace": [idx.debug_stats() for idx in self._indexes.values()],
        }


_capability_index: CapabilityIndex | None = None


def get_capability_index() -> CapabilityIndex:
    global _capability_index
    if _capability_index is None:
        _capability_index = CapabilityIndex()
    return _capability_index
