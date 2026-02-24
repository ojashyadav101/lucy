# Integration Architecture: Lucy (Composio) vs Viktor (OpenClaw/Pipedream)

**Date:** February 24, 2026  
**Purpose:** Deep comparative research for aligning Lucy's integration pipeline with Viktor's  
**Repo:** https://github.com/ojashyadav101/lucy/tree/lucy-openrouter-v2

---

## 1. Architecture Comparison

### Viktor's Stack (OpenClaw + Pipedream)

Viktor operates on OpenClaw — a self-hosted agent gateway that provides:
- **3-layer integration model**: Native/MCP → Pipedream (3,114 pre-built) → Custom API
- **Tool registration**: JSON-schema functions via TypeBox plugin system
- **Memory**: File-based Markdown (MEMORY.md + daily logs) with vector search (SQLite + BM25 + embeddings)
- **Agent loop**: Single-threaded per session, multi-turn tool calling with loop detection, auto-compaction
- **Model routing**: Server-side binding-based routing with auth profile rotation and failover

**Pipedream integration flow:**
1. Pre-built integrations auto-generate Python SDK wrappers in `sdk/tools/`
2. OAuth handled automatically at platform level
3. Tools immediately available after connection
4. Naming: `pd_{service}.py` for Pipedream, `mcp_{service}.py` for native

**Custom API integration flow:**
1. `create_custom_api_integration()` → generates secure credential form URL
2. User fills form (credentials never visible to AI)
3. Platform stores credentials server-side, encrypted
4. Platform generates SDK tools: `custom_api_{slug}_get/post/put/etc.`

### Lucy's Stack (Composio + OpenRouter)

Lucy uses Composio's session-based meta-tools API:
- **5 meta-tools**: SEARCH_TOOLS, MANAGE_CONNECTIONS, MULTI_EXECUTE_TOOL, REMOTE_WORKBENCH, REMOTE_BASH_TOOL
- **Tool discovery**: Runtime via SEARCH_TOOLS (hybrid vector + BM25 search)
- **Auth**: Session.authorize() or MANAGE_CONNECTIONS → hosted OAuth page at `connect.composio.dev`
- **Memory**: Workspace filesystem (SKILL.md, company/team knowledge) — no vector search
- **Agent loop**: Multi-turn with depth enforcement, model escalation, loop detection

**Composio connection flow:**
1. LLM calls `COMPOSIO_MANAGE_CONNECTIONS(toolkits: ["github", "linear"])`
2. Returns `redirect_url: "https://connect.composio.dev/link/ln_<hash>"`
3. User clicks link → completes OAuth on Composio's hosted page
4. User confirms in chat → LLM proceeds with tool execution
5. Sessions auto-pick up new connections (tied to user_id)

---

## 2. Key Differences

| Aspect | Viktor (OpenClaw) | Lucy (Composio) |
|--------|-------------------|-----------------|
| Integration count | 3,114 (Pipedream) + native | 10,000+ (Composio catalog) |
| Auth handling | Platform-level, automatic | Session-based, meta-tool driven |
| Tool format | TypeBox JSON-schema (Node.js) | OpenAI function-calling JSON (Python) |
| Discovery | Static SDK files + `tools.md` | Runtime search via SEARCH_TOOLS |
| Connection tracking | Immediate SDK file generation | Session cache + API polling |
| Custom integrations | `create_custom_api_integration()` | Not yet implemented |
| Memory on connect | Proactive exploration → SKILL.md | No post-connect exploration |

---

## 3. Bugs Found & Fixed in Lucy's Pipeline

### Bug 1: Auth URLs stripped by sanitizer
- **File**: `src/lucy/core/output.py` line 34
- **Cause**: `composio\.dev[^\s)\"']*` pattern stripped the entire domain from auth URLs
- **Effect**: `https://app.composio.dev/auth/linear?token=...` → `https://app.` → "link unavailable"
- **Fix**: Changed to only strip "composio" in plain text context, never inside URLs

### Bug 2: Tool results truncated before LLM sees auth URLs
- **File**: `src/lucy/core/agent.py` lines 1050-1054
- **Cause**: TOOL_RESULT_MAX_CHARS (12,000) and TOOL_RESULT_SUMMARY_THRESHOLD (4,000) truncate results. Auth URLs buried in large JSON responses get cut.
- **Fix**: Extract auth URLs (matching `composio.dev` patterns) before truncation and append them to the truncated result

### Bug 3: Overly aggressive tool name sanitizer
- **File**: `src/lucy/core/agent.py` line 47
- **Cause**: `[A-Z]{3,}_[A-Z_]{3,}` matched URL parameters, JSON keys, and other uppercase strings
- **Fix**: Narrowed to `COMPOSIO_\w+` only

### Bug 4: Wrong parameters in fallback connection check
- **File**: `src/lucy/integrations/composio_client.py` line 356
- **Cause**: Called COMPOSIO_MANAGE_CONNECTIONS with `{"action": "list_connections"}` — not a valid parameter
- **Fix**: Changed fallback to use `session.toolkits(is_connected=True)` via SDK

### Bug 5: No composio.dev in Slack link formatter
- **File**: `src/lucy/slack/rich_output.py` line 70
- **Cause**: Auth URLs got generic domain labels instead of "Connect here"
- **Fix**: Added composio.dev domains to _DOMAIN_NAMES map

---

## 4. Composio API Reference (Current)

### Session Creation
```python
session = composio.create(user_id="workspace_123")
```

### Get Meta-Tools
```python
tools = session.tools()  # Returns 5 meta-tools in OpenAI format
```

### Generate Auth Link
```python
# Programmatic
request = session.authorize("github")
url = request.redirect_url  # https://connect.composio.dev/link/ln_<hash>
connected = request.wait_for_connection(60000)  # blocks until auth completes

# Via meta-tool (LLM-driven)
COMPOSIO_MANAGE_CONNECTIONS(toolkits=["github", "linear"])
# Returns redirect_url per unconnected toolkit
```

### Check Connection Status
```python
# All toolkits with status
toolkits = session.toolkits()
for tk in toolkits.items:
    print(f"{tk.name}: {tk.connection.is_active}")

# Only connected
connected = session.toolkits(is_connected=True)
```

### Auth URL Format
- `https://connect.composio.dev/link/ln_<hash>` — hosted OAuth page
- User completes flow on Composio's page
- Tokens stored and auto-refreshed by Composio
- No expiration documented; connection persists until user revokes

---

## 5. What Viktor Does That Lucy Doesn't (Yet)

1. **Proactive integration exploration**: On day one, Viktor explores every connected integration — maps account structure, tests endpoints, documents working patterns, writes SKILL.md files.

2. **Post-connect verification**: When a new integration is connected, Viktor immediately tests it with read-only operations and documents what works.

3. **Custom API fallback**: When a pre-built integration fails, Viktor builds a custom API integration on the fly.

4. **Integration SKILL.md files**: Viktor maintains per-integration skill files with working patterns, known issues, and helper functions.

5. **Connection failure recovery**: Viktor has a multi-step recovery flow (retry → reconnect → custom API → manual).

---

## 6. Recommended Next Steps for Lucy

1. **Post-connect hook**: When a user confirms a connection, invalidate Composio cache, verify the connection via `session.toolkits()`, and write an integration SKILL.md with basic details.

2. **Proactive exploration**: After connecting a new service, Lucy should proactively test it (read-only) and document what works.

3. **Connection state tracking**: Store connection status in workspace files (`integrations/connections.json`) so it persists across sessions without API calls.

4. **Custom API bridge**: Implement a `lucy_create_custom_integration` internal tool that uses Composio's custom integration API for services not in the catalog.

5. **Auth URL presentation**: The system prompt now explicitly instructs the LLM to present auth URLs directly and never fabricate them.
