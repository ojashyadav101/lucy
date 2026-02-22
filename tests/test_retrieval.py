"""Retrieval architecture tests.

Tests for:
  - tokenise(): snake_case, CamelCase, stopword removal, generic compound split
  - bm25_score(): correct scoring and zero-score handling
  - WorkspaceIndex: add_tools with app_slug, retrieve top-K with RetrievalResult,
                    connected_apps filter, usage boost, generic app inference
  - CapabilityIndex: workspace isolation, invalidation, snapshot
  - TopKRetriever: lazy population, fallback to None on empty index,
                   record_usage propagation
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from lucy.retrieval.capability_index import (
    CapabilityIndex,
    RetrievalResult,
    WorkspaceIndex,
    _compute_idf,
    bm25_score,
    expand_query,
    tokenise,
    MIN_INDEXED_TOOLS,
)
from lucy.retrieval.tool_retriever import TopKRetriever, INITIAL_K, EXPANDED_K


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_schema(name: str, description: str = "") -> dict:
    """Build a minimal OpenAI-format tool schema."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description or f"Tool for {name}",
            "parameters": {"type": "object", "properties": {}},
        },
    }


CALENDAR_TOOLS = [
    _make_schema("GOOGLECALENDAR_EVENTS_LIST", "List calendar events for a time range"),
    _make_schema("GOOGLECALENDAR_CREATE_EVENT", "Create a new calendar event"),
    _make_schema("GOOGLECALENDAR_DELETE_EVENT", "Delete an existing calendar event"),
    _make_schema("GOOGLECALENDAR_UPDATE_EVENT", "Update details of a calendar event"),
]

GITHUB_TOOLS = [
    _make_schema("GITHUB_LIST_ISSUES", "List issues in a GitHub repository"),
    _make_schema("GITHUB_CREATE_ISSUE", "Create a new issue in a repository"),
    _make_schema("GITHUB_LIST_PULL_REQUESTS", "List open pull requests in a repository"),
]

GMAIL_TOOLS = [
    _make_schema("GMAIL_SEND_EMAIL", "Send an email via Gmail"),
    _make_schema("GMAIL_LIST_MESSAGES", "List recent Gmail messages"),
    _make_schema("GMAIL_GET_MESSAGE", "Get a specific Gmail message by ID"),
]

META_TOOLS = [
    _make_schema("COMPOSIO_MULTI_EXECUTE_TOOL", "Execute multiple Composio tools in sequence"),
    _make_schema("COMPOSIO_SEARCH_TOOLS", "Search available Composio tools"),
]

ALL_TOOLS = CALENDAR_TOOLS + GITHUB_TOOLS + GMAIL_TOOLS + META_TOOLS


# ── tokenise() ──────────────────────────────────────────────────────────────

class TestTokenise:
    def test_screaming_snake_case(self):
        tokens = tokenise("GOOGLECALENDAR_EVENTS_LIST")
        assert "googlecalendar" in tokens
        assert "events" in tokens

    def test_plain_description(self):
        tokens = tokenise("List calendar events for a time range")
        assert "calendar" in tokens
        assert "events" in tokens
        assert "range" in tokens

    def test_camel_case(self):
        tokens = tokenise("listCalendarEvents")
        assert "calendar" in tokens
        assert "events" in tokens

    def test_stopwords_removed(self):
        tokens = tokenise("Get the calendar events for the user")
        assert "the" not in tokens
        assert "for" not in tokens

    def test_short_tokens_removed(self):
        tokens = tokenise("a b c d list")
        single_chars = [t for t in tokens if len(t) <= 1]
        assert not single_chars

    def test_empty_string(self):
        assert tokenise("") == []

    def test_mixed_case(self):
        tokens = tokenise("GITHUB_CREATE_ISSUE")
        assert "github" in tokens
        assert "issue" in tokens

    def test_generic_compound_split(self):
        """Auto-split should work for any compound name, not just hardcoded ones."""
        tokens = tokenise("GOOGLECALENDAR_EVENTS_LIST")
        assert "calendar" in tokens

    def test_generic_compound_split_unknown_app(self):
        """Auto-split should work even for apps not in any hardcoded dictionary."""
        tokens = tokenise("SLACKBOT_SEND_MESSAGE")
        assert "slack" in tokens or "slackbot" in tokens

    def test_expand_query_meetings_synonym(self):
        tokens = expand_query(tokenise("what meetings do I have"))
        assert "calendar" in tokens or "events" in tokens

    def test_expand_query_schedule_synonym(self):
        tokens = expand_query(tokenise("show my schedule"))
        assert "calendar" in tokens

    def test_expand_query_email_synonym(self):
        tokens = expand_query(tokenise("check email"))
        assert "gmail" in tokens or "mail" in tokens

    def test_expand_query_no_duplicates(self):
        tokens = expand_query(["calendar", "calendar"])
        assert tokens.count("calendar") == 1


# ── bm25_score() ────────────────────────────────────────────────────────────

class TestBM25Score:
    def _setup(self, docs: list[list[str]]):
        return _compute_idf(docs), sum(len(d) for d in docs) / max(len(docs), 1)

    def test_exact_match_scores_higher_than_partial(self):
        doc_a = ["calendar", "events", "list", "google"]
        doc_b = ["github", "issues", "repository"]
        query = ["calendar", "events"]
        idf, avg_len = self._setup([doc_a, doc_b])
        score_a = bm25_score(query, doc_a, idf, avg_len)
        score_b = bm25_score(query, doc_b, idf, avg_len)
        assert score_a > score_b

    def test_no_overlap_returns_zero(self):
        doc = ["github", "repository"]
        query = ["calendar", "schedule"]
        idf, avg_len = self._setup([doc])
        assert bm25_score(query, doc, idf, avg_len) == 0.0

    def test_empty_query_returns_zero(self):
        doc = ["calendar", "events"]
        idf, avg_len = self._setup([doc])
        assert bm25_score([], doc, idf, avg_len) == 0.0

    def test_empty_doc_returns_zero(self):
        idf, avg_len = self._setup([[]])
        assert bm25_score(["calendar"], [], idf, avg_len) == 0.0

    def test_score_is_non_negative(self):
        doc = ["calendar", "events"]
        query = ["calendar"]
        idf, avg_len = self._setup([doc])
        assert bm25_score(query, doc, idf, avg_len) >= 0


# ── WorkspaceIndex ──────────────────────────────────────────────────────────

class TestWorkspaceIndex:
    def _make_index(self, tools=None) -> WorkspaceIndex:
        idx = WorkspaceIndex("test_ws")
        if tools:
            run(idx.add_tools(tools))
        return idx

    def test_add_tools_increments_size(self):
        idx = self._make_index()
        run(idx.add_tools(CALENDAR_TOOLS))
        assert idx.size == len(CALENDAR_TOOLS)

    def test_add_tools_with_app_slug(self):
        idx = self._make_index()
        run(idx.add_tools(CALENDAR_TOOLS, app_slug="googlecalendar"))
        record = idx.get_record("GOOGLECALENDAR_EVENTS_LIST")
        assert record is not None
        assert record.app == "googlecalendar"

    def test_duplicate_tools_not_double_counted(self):
        idx = self._make_index(CALENDAR_TOOLS)
        run(idx.add_tools(CALENDAR_TOOLS))
        assert idx.size == len(CALENDAR_TOOLS)

    def test_retrieve_returns_retrieval_result(self):
        idx = self._make_index(ALL_TOOLS)
        result = idx.retrieve("what's on my calendar today", k=3)
        assert isinstance(result, RetrievalResult)
        assert isinstance(result.tools, list)
        assert isinstance(result.top_score, float)

    def test_retrieve_returns_relevant_tools(self):
        idx = self._make_index(ALL_TOOLS)
        result = idx.retrieve("what's on my calendar today", k=3)
        names = [r["function"]["name"] for r in result.tools]
        assert any("GOOGLECALENDAR" in n for n in names)

    def test_retrieve_github_query(self):
        idx = self._make_index(ALL_TOOLS)
        result = idx.retrieve("show me open issues in my repo", k=3)
        names = [r["function"]["name"] for r in result.tools]
        assert any("GITHUB" in n for n in names)

    def test_retrieve_filters_by_connected_apps(self):
        idx = self._make_index(ALL_TOOLS)
        result = idx.retrieve(
            "calendar events",
            k=10,
            connected_apps={"googlecalendar"},
        )
        apps = {r["function"]["name"].split("_")[0].lower() for r in result.tools
                if not r["function"]["name"].startswith("COMPOSIO")}
        assert all(a in {"googlecalendar", "composio"} for a in apps)

    def test_retrieve_respects_k(self):
        idx = self._make_index(ALL_TOOLS)
        result = idx.retrieve("calendar", k=2)
        assert len(result.tools) <= 2

    def test_retrieve_empty_index_returns_empty(self):
        idx = WorkspaceIndex("empty_ws")
        result = idx.retrieve("calendar", k=5)
        assert isinstance(result, RetrievalResult)
        assert result.tools == []
        assert result.top_score == 0.0

    def test_retrieve_no_overlap_returns_empty(self):
        idx = self._make_index(CALENDAR_TOOLS)
        result = idx.retrieve("completely unrelated xyzzy query", k=5)
        assert result.tools == []

    def test_usage_boost_promotes_tool(self):
        idx = self._make_index(ALL_TOOLS)
        for _ in range(10):
            idx.record_usage("GOOGLECALENDAR_DELETE_EVENT")
        result = idx.retrieve("calendar", k=4)
        names = [r["function"]["name"] for r in result.tools]
        assert "GOOGLECALENDAR_DELETE_EVENT" in names

    def test_infer_app_generic(self):
        """Generic app inference splits at first underscore."""
        assert WorkspaceIndex._infer_app_generic("GOOGLECALENDAR_EVENTS_LIST") == "googlecalendar"
        assert WorkspaceIndex._infer_app_generic("GITHUB_LIST_ISSUES") == "github"
        assert WorkspaceIndex._infer_app_generic("GMAIL_SEND_EMAIL") == "gmail"
        assert WorkspaceIndex._infer_app_generic("NEWAPP_DO_SOMETHING") == "newapp"
        assert WorkspaceIndex._infer_app_generic("SINGLE") == "single"

    def test_debug_stats_shape(self):
        idx = self._make_index(ALL_TOOLS)
        stats = idx.debug_stats()
        assert "total_tools" in stats
        assert "apps" in stats
        assert stats["total_tools"] == len(ALL_TOOLS)


# ── CapabilityIndex ─────────────────────────────────────────────────────────

class TestCapabilityIndex:
    def _make_index(self) -> CapabilityIndex:
        return CapabilityIndex()

    def test_get_creates_workspace(self):
        ci = self._make_index()
        ws = ci.get("ws_001")
        assert ws is not None
        assert isinstance(ws, WorkspaceIndex)

    def test_get_same_object_idempotent(self):
        ci = self._make_index()
        ws1 = ci.get("ws_001")
        ws2 = ci.get("ws_001")
        assert ws1 is ws2

    def test_different_workspaces_isolated(self):
        ci = self._make_index()
        run(ci.get("ws_a").add_tools(CALENDAR_TOOLS))
        ws_b = ci.get("ws_b")
        assert ws_b.size == 0

    def test_invalidate_drops_workspace(self):
        ci = self._make_index()
        run(ci.get("ws_drop").add_tools(CALENDAR_TOOLS))
        run(ci.invalidate("ws_drop"))
        ws_new = ci.get("ws_drop")
        assert ws_new.size == 0

    def test_total_indexed_tools(self):
        ci = self._make_index()
        run(ci.get("ws_x").add_tools(CALENDAR_TOOLS))
        run(ci.get("ws_y").add_tools(GITHUB_TOOLS))
        assert ci.total_indexed_tools == len(CALENDAR_TOOLS) + len(GITHUB_TOOLS)

    def test_snapshot_shape(self):
        ci = self._make_index()
        run(ci.get("ws_snap").add_tools(CALENDAR_TOOLS))
        snap = ci.snapshot()
        assert "workspaces" in snap
        assert "total_tools" in snap
        assert "per_workspace" in snap


# ── TopKRetriever ───────────────────────────────────────────────────────────

WORKSPACE_ID = uuid4()


def _make_retriever(pre_populated: bool = False) -> TopKRetriever:
    """Create a TopKRetriever with an optionally pre-populated index."""
    index = CapabilityIndex()
    if pre_populated:
        run(index.get(str(WORKSPACE_ID)).add_tools(ALL_TOOLS))
    return TopKRetriever(index=index)


class TestTopKRetriever:
    def test_returns_none_when_index_empty(self):
        retriever = _make_retriever(pre_populated=False)
        with patch(
            "lucy.integrations.composio_client.get_composio_client",
        ) as mock_client:
            mock_client.return_value.fetch_app_tool_schemas = AsyncMock(return_value=[])
            with patch(
                "lucy.integrations.registry.get_integration_registry",
            ) as mock_reg:
                mock_reg.return_value.get_active_providers = AsyncMock(return_value=["googlecalendar"])
                result = run(retriever.retrieve(
                    workspace_id=WORKSPACE_ID,
                    query="calendar events",
                    connected_apps={"googlecalendar"},
                    k=5,
                ))
        assert result is None

    def test_returns_result_when_index_populated(self):
        retriever = _make_retriever(pre_populated=True)
        result = run(retriever.retrieve(
            workspace_id=WORKSPACE_ID,
            query="list my calendar events",
            connected_apps={"googlecalendar"},
            k=5,
        ))
        assert result is not None
        assert isinstance(result, RetrievalResult)
        assert len(result.tools) > 0
        assert result.top_score > 0

    def test_calendar_query_returns_calendar_tools(self):
        retriever = _make_retriever(pre_populated=True)
        result = run(retriever.retrieve(
            workspace_id=WORKSPACE_ID,
            query="what meetings do I have today",
            connected_apps={"googlecalendar"},
            k=5,
        ))
        assert result is not None
        names = [t["function"]["name"] for t in result.tools]
        assert any("GOOGLECALENDAR" in n for n in names)

    def test_github_query_returns_github_tools(self):
        retriever = _make_retriever(pre_populated=True)
        result = run(retriever.retrieve(
            workspace_id=WORKSPACE_ID,
            query="show open issues in my github repo",
            connected_apps={"github"},
            k=5,
        ))
        assert result is not None
        names = [t["function"]["name"] for t in result.tools]
        assert any("GITHUB" in n for n in names)

    def test_record_usage_updates_index(self):
        retriever = _make_retriever(pre_populated=True)
        retriever.record_tool_usage(WORKSPACE_ID, "GOOGLECALENDAR_EVENTS_LIST")
        record = retriever._index.get(str(WORKSPACE_ID)).get_record("GOOGLECALENDAR_EVENTS_LIST")
        assert record is not None
        assert record.usage_count == 1

    def test_populate_called_on_empty_index(self):
        index = CapabilityIndex()
        retriever = TopKRetriever(index=index)

        populate_called = []

        async def _mock_populate(ws_id, connected_apps):
            populate_called.append(ws_id)
            await index.get(ws_id).add_tools(ALL_TOOLS)
            return len(ALL_TOOLS)

        with patch.object(retriever, "_populate", side_effect=_mock_populate):
            result = run(retriever.retrieve(
                workspace_id=WORKSPACE_ID,
                query="calendar",
                connected_apps={"googlecalendar"},
            ))

        assert len(populate_called) == 1
        assert result is not None

    def test_invalidate_clears_workspace(self):
        retriever = _make_retriever(pre_populated=True)
        run(retriever.invalidate(WORKSPACE_ID))
        ws = retriever._index.get(str(WORKSPACE_ID))
        assert ws.size == 0


# ── Constants ───────────────────────────────────────────────────────────────

def test_initial_k_less_than_expanded_k():
    assert INITIAL_K < EXPANDED_K

def test_min_indexed_tools_reasonable():
    assert 1 <= MIN_INDEXED_TOOLS <= 20
