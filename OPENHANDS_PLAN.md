# Plan: Integrating an Autonomous Coding Agent into Lucy

## Context & The Problem
We are currently working on giving Lucy the ability to autonomously generate, test, and debug code—specifically for building API wrappers dynamically when no native integration exists. 

Our current architecture (`src/lucy/integrations/coding_agent.py`) attempts to do this using a raw LLM loop combined with an E2B cloud sandbox. However, we are running into significant reliability issues:
1. **Context/Output Truncation:** When the LLM generates fixes, the output is often too large, leading to truncated code and syntax errors in the sandbox.
2. **Brittle Debugging Loop:** Our current fix prompt blindly feeds raw `stdout` and `stderr` back to the LLM. It lacks the nuanced, multi-step debugging (like adding print statements or running linters) that a dedicated coding agent would do.
3. **Overfitting/Hardcoding:** We want Lucy to be generally capable of fixing any code she writes, rather than relying on hardcoded heuristics for specific services (like Polar.sh).

## Proposed Solution: OpenHands SDK
Instead of maintaining a fragile, hand-rolled code generation loop, I am proposing we rip out the custom E2B/LLM logic and replace it with the **OpenHands Software Agent SDK** (formerly OpenDevin). 

OpenHands is the leading open-source AI coding framework (68K+ stars, state-of-the-art SWE-bench scores). Crucially, they provide a Python SDK specifically designed to be embedded into existing applications.

### Why OpenHands?
- **Embeddable API:** It’s just `pip install openhands-sdk`. We can spawn it programmatically via Python.
- **Model Agnostic:** It uses LiteLLM under the hood, meaning it works natively with our existing OpenRouter setup and Gemini keys.
- **Pre-built Tools:** It comes with robust `TerminalTool` and `FileEditorTool`, allowing the sub-agent to actually explore the workspace, edit files incrementally, and run tests.
- **Autonomous Self-Healing:** The OpenHands agent loop natively handles context compression, error recovery, and multi-step reasoning, solving our truncation and brittle debugging issues out of the box.

### Architectural Changes
1. **New Bridge Module:** Create `src/lucy/integrations/openhands_bridge.py` to instantiate the OpenHands `Agent`, configure its tools, and manage the `Conversation` loop.
2. **Refactor `coding_agent.py`:** Remove the custom E2B sandbox orchestration and raw Gemini prompts. Route requests directly through the new `openhands_bridge`.
3. **New `lucy_code` Internal Tool:** Expose this capability back to Lucy's main orchestrator (`agent.py`) as a dedicated internal tool, allowing Lucy to delegate *any* general coding or scripting task to her new OpenHands sub-agent, not just wrapper generation.

---

## Questions for Viktor
Before we commit to this architectural shift, we would love your input since you successfully connected a robust coder previously:

1. **Architecture Review:** Does replacing our custom E2B/Gemini loop with an embedded instance of the OpenHands SDK align with how you solved the autonomous coding gap? 
2. **Sub-Agent Delegation:** When your agent delegates a coding task, how do you handle state/context transfer between the main agent (Lucy) and the coding sub-agent? Do they share a workspace volume, or do they pass strings back and forth?
3. **Execution Environment:** OpenHands can run locally or via a Docker-based Agent Server. For production safety, how did you handle the sandboxing of the coding agent's execution environment?
4. **Known Pitfalls:** Are there any specific pitfalls or gotchas we should watch out for when embedding an autonomous coding loop (like OpenHands) directly into a persistent chat agent?