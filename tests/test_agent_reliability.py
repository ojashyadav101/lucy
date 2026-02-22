#!/usr/bin/env python
"""Reliability guard unit tests for LucyAgent."""

import sys

sys.path.insert(0, "src")

from lucy.core.agent import LucyAgent, TOOL_RESULT_MAX_CHARS


def test_tool_call_signature_is_stable() -> None:
    agent = LucyAgent()
    a = {"name": "COMPOSIO_MULTI_EXECUTE_TOOL", "parameters": {"b": 2, "a": 1}}
    b = {"name": "COMPOSIO_MULTI_EXECUTE_TOOL", "parameters": {"a": 1, "b": 2}}
    assert agent._tool_call_signature(a) == agent._tool_call_signature(b)


def test_trim_tool_context_window() -> None:
    agent = LucyAgent()
    messages = [{"role": "user", "content": str(i)} for i in range(100)]
    trimmed = agent._trim_tool_context(messages)
    assert len(trimmed) <= 40
    assert trimmed[0]["content"] == "60"
    assert trimmed[-1]["content"] == "99"


def test_tool_result_truncation_marker() -> None:
    agent = LucyAgent()
    large = {"payload": "x" * (TOOL_RESULT_MAX_CHARS * 2)}
    text = agent._tool_result_to_llm_content(large)
    assert ("[TRUNCATED:" in text) or ("...(truncated)" in text)


def test_error_classification() -> None:
    agent = LucyAgent()
    assert agent._classify_tool_error("429 rate limit") == "retryable"
    assert agent._classify_tool_error("permission denied") == "auth"
    assert agent._classify_tool_error("invalid parameters") == "invalid_params"
    assert agent._classify_tool_error("unexpected failure") == "fatal"

