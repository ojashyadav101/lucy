# Tools & Integrations — Deep Dive

> How Lucy generates files, searches the web, sends emails, builds web apps,
> connects to third-party services, and resolves unknown integrations.

---

## Tool Categories

Lucy's tools fall into three tiers:

| Tier | Source | Examples |
|------|--------|---------|
| **Native tools** | `src/lucy/tools/` | File generation, web search, email, Spaces |
| **Composio meta-tools** | `src/lucy/integrations/composio_client.py` | Search tools, manage connections, execute actions, run code |
| **Dynamic tools** | `src/lucy/integrations/resolver.py` | Custom wrappers, MCP servers, OpenAPI registrations |

---

## Native Tools

### File Generator

**File:** `src/lucy/tools/file_generator.py`

Generates downloadable files and uploads them to Slack.

#### Tool Definitions

| Tool Name | Purpose | Output |
|-----------|---------|--------|
| `lucy_write_file` | Write file to workspace | File path |
| `lucy_edit_file` | Edit existing workspace file | Updated file path |
| `lucy_store_api_key` | Store API key securely | Confirmation |
| `lucy_generate_pdf` | Generate PDF from HTML | PDF file → Slack upload |
| `lucy_generate_excel` | Generate Excel from tabular data | XLSX file → Slack upload |
| `lucy_generate_csv` | Generate CSV from rows | CSV file → Slack upload |

#### How File Generation Works

```
LLM calls lucy_generate_pdf(title, content_html)
    │
    ├── generate_pdf(title, content_html, filename?)
    │     Uses WeasyPrint to render HTML → PDF
    │     Returns Path to generated file
    │
    ├── upload_file_to_slack(slack_client, path, channel, thread)
    │     Uploads via files_upload_v2 API
    │     Posts with optional comment
    │
    └── Returns {"path": str, "uploaded": bool, "url": str}
```

Excel generation uses `openpyxl`:
```python
sheets = {
    "Summary": [["Metric", "Value"], ["Users", 1250], ...],
    "Details": [["Date", "Signups", ...], ...]
}
```

### Web Search

**File:** `src/lucy/tools/web_search.py`

Provides real-time web search via Gemini on OpenRouter.

| Tool Name | Parameters | Returns |
|-----------|-----------|---------|
| `lucy_web_search` | `query: str` | `{"query": str, "answer": str}` |

Uses `google/gemini-2.5-flash` with grounded search to return
summarized web results.

### Email Tools

**File:** `src/lucy/tools/email_tools.py`

Email operations via AgentMail.

| Tool Name | Purpose |
|-----------|---------|
| `lucy_send_email` | Compose and send email |
| `lucy_read_emails` | Read inbox (optionally filtered) |
| `lucy_reply_to_email` | Reply to an existing email thread |
| `lucy_search_emails` | Search emails by query |
| `lucy_get_email_thread` | Get full email conversation |

All operations go through `src/lucy/integrations/agentmail_client.py`.
Lucy's email address is `lucy@{settings.agentmail_domain}` (default:
`lucy@zeeyamail.com`).

### Spaces Tools

**File:** `src/lucy/tools/spaces.py`

Commands for building and deploying web applications.

| Tool Name | Purpose |
|-----------|---------|
| `lucy_spaces_init` | Scaffold new project (Convex + Vercel) |
| `lucy_spaces_deploy` | Build and deploy to Vercel |
| `lucy_spaces_list` | List all projects |
| `lucy_spaces_status` | Get project deployment status |
| `lucy_spaces_delete` | Delete project (Convex + Vercel + local) |

These dispatch to `src/lucy/spaces/platform.py`.

---

## Composio Meta-Tools

**File:** `src/lucy/integrations/composio_client.py`

Composio provides 5 "meta-tools" that give Lucy access to 100+ third-party
services through a unified interface.

### The 5 Meta-Tools

| Meta-Tool | Purpose | How It Works |
|-----------|---------|-------------|
| `COMPOSIO_SEARCH_TOOLS` | Find tools by use-case | "I need to create a Google Calendar event" → returns matching tool schemas |
| `COMPOSIO_MANAGE_CONNECTIONS` | Check/create OAuth connections | Lists connected apps, generates OAuth links |
| `COMPOSIO_MULTI_EXECUTE_TOOL` | Execute up to 20 tools in parallel | Batch tool execution for efficiency |
| `COMPOSIO_REMOTE_WORKBENCH` | Run Python in sandboxed environment | Full Python execution with pip packages |
| `COMPOSIO_REMOTE_BASH_TOOL` | Run bash in sandboxed environment | Shell commands in isolation |

### How the Agent Uses Composio

```
User: "Create a meeting on Google Calendar for tomorrow at 2pm"
    │
    ├── Agent calls COMPOSIO_SEARCH_TOOLS("create google calendar event")
    │     Returns: tool schema for GOOGLECALENDAR_CREATE_EVENT
    │
    ├── Agent checks if Google Calendar is connected:
    │     COMPOSIO_MANAGE_CONNECTIONS("check google_calendar")
    │     ├── Connected → proceed
    │     └── Not connected → return OAuth link to user
    │
    └── Agent calls COMPOSIO_MULTI_EXECUTE_TOOL({
          actions: [{
            action: "GOOGLECALENDAR_CREATE_EVENT",
            params: { title: "Meeting", start: "...", end: "..." }
          }]
        })
        Returns: event created successfully
```

### Session Management

- Each workspace gets a Composio session with a stable entity ID
- Sessions are cached with LRU eviction (max 200)
- Entity IDs persist across database resets via `set_entity_id()`

### Rate Limiting

Composio API calls go through Lucy's rate limiter:

```python
await get_rate_limiter().acquire_api("google_calendar", timeout=15.0)
```

Per-API limits are configured in `infra/rate_limiter.py`:

| API | Rate | Burst |
|-----|------|-------|
| Google Calendar/Sheets/Drive | 2.0/s | 5 |
| Gmail | 2.0/s | 5 |
| GitHub | 5.0/s | 15 |
| Linear | 3.0/s | 10 |
| Slack | 3.0/s | 10 |

---

## Integration Resolver

**File:** `src/lucy/integrations/resolver.py`

When Lucy encounters a service it doesn't have a pre-built integration for,
the resolver attempts to create one automatically.

### 3-Stage Resolution Pipeline

```
User: "Connect to Airtable and list my bases"
    │
    ├── Stage 0: GROUNDED SEARCH
    │     Uses Gemini to classify the service:
    │       - Has MCP server?
    │       - Has public OpenAPI spec?
    │       - Has SDK/docs?
    │     Returns IntegrationClassification
    │
    ├── Stage 1: MCP
    │     If MCP server available:
    │       Install MCP server → register tools → done
    │     If fails → continue
    │
    ├── Stage 2: OpenAPI
    │     If OpenAPI spec available:
    │       Download spec → register endpoints as tools → done
    │     If fails → continue
    │
    ├── Stage 3: WRAPPER GENERATION
    │     If SDK/docs available:
    │       LLM reads documentation → generates Python wrapper
    │       Wrapper exposes tools → register → done
    │     If fails → continue
    │
    └── Stage 4: HONEST FAILURE
          "I can't connect to {service} automatically.
           Here's what I found about their API..."
```

### ResolutionResult

```python
@dataclass
class ResolutionResult:
    service_name: str
    stage: ResolutionStage          # MCP, OPENAPI, WRAPPER, FAILED
    success: bool
    classification: IntegrationClassification | None
    mcp_result: MCPInstallResult | None
    openapi_result: OpenAPIRegistrationResult | None
    wrapper_result: WrapperDeployResult | None
    needs_api_key: bool
    api_key_env_var: str | None
    result_data: dict[str, Any]
    user_message: str               # Human-friendly result
    error: str | None
    timing_ms: dict[str, float]     # Per-stage timing
    decision_log: list[str]         # Reasoning trail
```

---

## Integration Modules — Detailed Reference

### CamoFox Browser (`integrations/camofox.py`)

Anti-detection headless browser for web scraping, form filling, and content
checking. Used by heartbeat `page_content` evaluator and research tasks.

**Client:** `CamoFoxClient` — async REST client to CamoFox server (port 9377).

| Category | Methods |
|----------|---------|
| **Tab Management** | `create_tab(user_id?)` → tab_id, `list_tabs()`, `close_tab(tab_id)` |
| **Navigation** | `navigate(tab_id, url)` (supports @search macros, 45s timeout), `go_back()`, `go_forward()`, `reload()` |
| **Reading** | `snapshot(tab_id)` → accessibility tree with `eN` element references |
| **Interaction** | `click(ref)`, `type_text(ref, text)`, `fill(ref, text)`, `press_key(ref, key)`, `select_option(ref, value)`, `scroll(ref, direction)`, `hover(ref)` |
| **Screenshot** | `screenshot(tab_id)` → PNG bytes |
| **Health** | `is_healthy()` → bool |

**Search macros:** `@google_search`, `@youtube_search`, `@bing_search`,
`@duckduckgo_search`, `@reddit_search`, `@github_search`,
`@stackoverflow_search`, `@wikipedia_search`, `@amazon_search`,
`@twitter_search`, `@linkedin_search`, `@hackernews_search`,
`@arxiv_search`, `@scholar_search`.

**Workflow pattern:**
```
create_tab() → navigate(url) → snapshot() → click(ref) → snapshot() → close_tab()
```

**Error:** `CamoFoxError(status_code, detail)` raised on API failures.

---

### MCP Manager (`integrations/mcp_manager.py`)

Installs and manages Model Context Protocol servers on the OpenClaw VPS.

**Functions:**

| Function | Purpose |
|----------|---------|
| `install_mcp_server(classification)` | Install + start MCP server on VPS |
| `stop_mcp_server(session_id)` | Stop a running MCP server |
| `list_running_mcp_servers()` | List MCP processes on VPS |

**Installation flow:**
```
IntegrationClassification (from grounded search)
    │
    ├── Create directory: /home/lucy-oclaw/mcp-servers/{slug}/
    ├── Detect type: npm package or git repo
    ├── Install:
    │     npm: npm install (120s timeout)
    │     git: git clone + pip/npm install (180s timeout)
    ├── Start as background process via Gateway
    └── Return MCPInstallResult with session_id
```

**MCPInstallResult:** `success`, `service_name`, `session_id`, `install_log`,
`error`, `needs_api_key`, `api_key_env_var`.

---

### OpenAPI Registrar (`integrations/openapi_registrar.py`)

Fetches OpenAPI specs and registers them with Composio as Custom Apps.

**Flow:**
```
IntegrationClassification
    │
    ├── Download spec (tries URL variants):
    │     /openapi.json, /swagger.json, /v3/api-docs, etc.
    │     Also tries: api.{domain}/openapi.json
    ├── POST to Composio custom-app endpoints:
    │     /v1/apps/openapi → /v1/apps → /v2/apps (tries in order)
    ├── Map auth scheme:
    │     oauth2 → OAUTH2, api_key → API_KEY, bearer → BEARER_TOKEN
    └── Return OpenAPIRegistrationResult with toolkit_slug
```

**Timeouts:** 30s for spec fetch, 60s for registration.

---

### Wrapper Generator (`integrations/wrapper_generator.py`)

Generates Python API wrappers via LLM when MCP and OpenAPI are unavailable.

**Flow:**
```
IntegrationClassification
    │
    ├── Gather context:
    │     ├── Endpoint inventory (from Phase 2 discovery)
    │     └── Or: fetch OpenAPI spec summary
    │
    ├── Call Gemini to generate wrapper code:
    │     Model: google/gemini-2.5-flash
    │     Temperature: 0.2, max_tokens: 16384
    │     Retries: up to 2 on syntax errors
    │
    ├── Save wrapper:
    │     custom_wrappers/{slug}/wrapper.py
    │     custom_wrappers/{slug}/meta.json
    │
    ├── Validate:
    │     Module imports successfully
    │     TOOLS list exists and is non-empty
    │
    └── Return WrapperDeployResult with tool names
```

**Discovery:** `discover_saved_wrappers()` scans `custom_wrappers/` for
saved wrappers and returns metadata from `meta.json` files.

**Deletion:** `delete_custom_wrapper(slug)` removes the wrapper directory
and its API key from `keys.json`.

**Current wrappers:** `clerk/`, `polarsh/` (generated for these services).

---

### Grounded Search (`integrations/grounded_search.py`)

Two-phase research engine for classifying third-party services.

**Phase 1: `classify_service(service_name)`**
- Uses Gemini to determine: has MCP? has OpenAPI? has SDK?
- Returns `IntegrationClassification` with 15+ fields
- Verifies `api_base_url` is actually an API (not a website)
- Auto-corrects base URL from OpenAPI spec or web search

**Phase 2: `discover_endpoints(classification)`**
- Fetches API docs (OpenAPI spec → API docs page → SDK readme)
- Uses Gemini to extract endpoint inventory
- Groups by category with method, path, description, parameters
- Max 30,000 chars of documentation, 8192 max_tokens output

**IntegrationClassification fields:**

| Field | Type | Purpose |
|-------|------|---------|
| `service_name` | str | Service name |
| `has_mcp` | bool | MCP server available |
| `mcp_repo_url` | str | MCP repo URL |
| `has_openapi` | bool | OpenAPI spec available |
| `openapi_spec_url` | str | Spec URL |
| `has_sdk` | bool | SDK/library available |
| `sdk_package` | str | Package name |
| `api_docs_url` | str | Documentation URL |
| `api_base_url` | str | API base URL (verified) |
| `auth_method` | str | oauth2/api_key/bearer/basic/none |
| `summary` | str | Service description |
| `endpoint_categories` | list | Phase 2: grouped endpoints |
| `total_endpoints` | int | Phase 2: endpoint count |

**URL verification:** Probes the claimed `api_base_url` to confirm it serves
JSON (not HTML). Falls back to extracting from OpenAPI spec's `servers`
block, then asks LLM to web-search for the correct URL.

---

### OpenClaw Gateway (`integrations/openclaw_gateway.py`)

HTTP client for the OpenClaw VPS. Provides shell execution, file management,
and web fetching.

| Category | Methods |
|----------|---------|
| **Exec** | `exec_command(cmd, timeout=120, workdir?, env?)` → foreground execution |
| **Background** | `start_background(cmd)` → session_id, `poll_process(id)`, `log_process(id, limit=200)`, `kill_process(id)`, `list_processes()` |
| **Files** | `write_file(path, content)`, `read_file(path)` → text, `edit_file(path, old, new)` |
| **Web** | `web_fetch(url, max_chars=30000)` → extracted text/markdown |
| **Health** | `health_check()` → bool, `check_coding_tools()` → dict of available tools |

**Connection:** Base URL from `settings.openclaw_base_url`, Bearer auth.
Timeout: connect=10s, read=180s, write=10s, pool=30s.

**Error:** `OpenClawGatewayError(tool)` raised on failures.

---

### AgentMail Client (`integrations/agentmail_client.py`)

Async wrapper for Lucy's email identity via AgentMail SDK.

| Category | Methods |
|----------|---------|
| **Inbox** | `create_inbox(username, display_name)` → inbox_id, `list_inboxes()` |
| **Send** | `send_email(to, subject, text, html?, cc?, bcc?)`, `reply_to_email(message_id, text, html?)` |
| **Read** | `list_threads(inbox_id?, labels?, limit=20)`, `get_thread(thread_id)`, `list_messages(inbox_id?, limit=20)` |
| **Search** | `search_messages(query, inbox_id?, limit=10)` — client-side search over 100 recent messages |

**Default inbox:** `lucy@{settings.agentmail_domain}` (default: `lucy@zeeyamail.com`).

---

### Email Listener (`integrations/email_listener.py`)

WebSocket listener for real-time inbound email processing.

**Flow:**
```
start(slack_client, inbox_ids, notification_channel)
    │
    ├── Connect WebSocket to AgentMail
    ├── Subscribe to inbox events
    │
    └── _listen_loop:
          ├── Receive event
          ├── _handle_inbound(event):
          │     Parse email → build Block Kit notification → post to Slack
          ├── On disconnect:
          │     Exponential backoff (2s → 4s → 8s → ... → max 120s)
          └── Auto-reconnect
```

**Lifecycle:** `start()` runs as background asyncio task. `stop()` cancels
gracefully. `running` property checks state.

---

### Custom Wrappers (`integrations/custom_wrappers/`)

Auto-discovery and registration of LLM-generated API wrappers.

**Directory structure:**
```
custom_wrappers/
├── clerk/
│   ├── wrapper.py       # Generated Python wrapper
│   ├── meta.json         # Metadata (slug, service_name, tools)
│   └── __init__.py
└── polarsh/
    ├── wrapper.py
    ├── meta.json
    └── __init__.py
```

**Functions:**

| Function | Purpose |
|----------|---------|
| `load_custom_wrapper_tools()` | Scan directories, return OpenAI-format tool definitions. Tool names prefixed with `lucy_custom_`. |
| `execute_custom_tool(name, params, api_key)` | Load wrapper module dynamically, call `execute()`. Handles both sync and async. |

**Registration:** Called during tool assembly in `agent.run()`. Tools appear
alongside native and Composio tools.

---

## Lucy Spaces Platform

**File:** `src/lucy/spaces/platform.py`

Lucy Spaces lets users build and deploy web applications directly from Slack.

### Architecture

```
Lucy Spaces Stack:
├── Frontend: React 19 + Vite + TypeScript (deployed to Vercel)
├── Styling: Tailwind CSS v4 + shadcn/ui (53 components)
├── Backend: Convex (serverless functions + database)
├── Auth: Email/password with OTP
├── Domain: {app-name}.zeeya.app
├── Template: templates/lucy-spaces/
└── Package Manager: Bun
```

### Project Lifecycle

```
"Build me a task tracker app"
    │
    ├── init_app_project(name, description, workspace_id)
    │     1. Slugify name → "task-tracker"
    │     2. Copy template to workspace
    │     3. Create Convex project + deployment
    │     4. Create Vercel project + custom domain + protection bypass
    │     5. Generate secrets (.env.local)
    │     6. Save project.json
    │
    ├── Agent modifies code via COMPOSIO_REMOTE_WORKBENCH
    │     Edits React components, Convex functions, etc.
    │
    ├── deploy_app(name, workspace_id)
    │     1. bun install (in project dir)
    │     2. vite build
    │     3. Upload dist/ to Vercel
    │     4. Wait for deployment → validate
    │
    └── Returns: "Your app is live at task-tracker.zeeya.app"
```

### SpaceProject Dataclass

```python
@dataclass
class SpaceProject:
    name: str                           # Display name
    description: str                    # Project description
    workspace_id: str                   # Owner workspace
    convex_project_id: int              # Convex project ID
    convex_deployment_name: str         # Deployment name
    convex_deployment_url: str          # Convex URL
    convex_deploy_key: str              # CLI deploy key
    vercel_project_id: str              # Vercel project ID
    subdomain: str                      # Custom subdomain
    project_secret: str                 # Generated secret
    created_at: str                     # ISO timestamp
    vercel_project_name: str = ""       # Vercel project name
    vercel_bypass_secret: str = ""      # Standard Protection bypass
    last_deployed_at: str | None = None # Last deployment timestamp
    vercel_deployment_url: str | None = None  # Deployment URL

    def public_url(self) -> str         # Returns public URL
    def save(self, path: Path) -> None  # Serialize to JSON
    @classmethod
    def load(cls, path: Path) -> SpaceProject  # Deserialize
```

### ConvexAPI Client

**File:** `src/lucy/spaces/convex_api.py`
**Base URL:** `https://api.convex.dev/v1`

| Method | Purpose |
|--------|---------|
| `create_project(name)` | Create project with dev deployment |
| `list_projects()` | List all team projects |
| `create_deployment(project_id, type)` | Create dev/prod deployment |
| `create_deploy_key(name, key_name)` | Create CLI deploy key |
| `get_deployment(name)` | Get deployment details |
| `list_deployments(project_id)` | List all deployments |
| `delete_project(project_id)` | Delete project + deployments |

### VercelAPI Client

**File:** `src/lucy/spaces/vercel_api.py`
**Base URL:** `https://api.vercel.com`

| Method | Purpose |
|--------|---------|
| `create_project(name)` | Create Vercel project |
| `generate_protection_bypass(id)` | Generate Standard Protection bypass secret |
| `add_domain(id, domain)` | Add custom domain |
| `deploy_directory(id, dist, name, target)` | Upload built files + create deployment |
| `get_deployment(id)` | Get deployment status |
| `delete_project(id)` | Delete project |

---

## How Tools Are Registered

During `agent.run()`, tools are assembled from multiple sources:

```
Agent starts
    │
    ├── Composio meta-tools (5 tools)
    │     composio_client.get_tools(workspace_id)
    │
    ├── Native tools:
    │     ├── File tools (6): write, edit, store_key, pdf, excel, csv
    │     ├── Web search (1): lucy_web_search
    │     ├── Spaces tools (5): init, deploy, list, status, delete
    │     ├── Email tools (5): send, read, reply, search, thread
    │     └── History tools (3): search, channel_history, list_channels
    │
    ├── Delegation tools (4):
    │     delegate_to_research_agent
    │     delegate_to_code_agent
    │     delegate_to_integrations_agent
    │     delegate_to_document_agent
    │
    ├── Monitoring tools (3):
    │     lucy_create_heartbeat
    │     lucy_delete_heartbeat
    │     lucy_list_heartbeats
    │
    ├── Cron tools (4):
    │     lucy_create_cron
    │     lucy_delete_cron
    │     lucy_modify_cron
    │     lucy_list_crons
    │
    └── Custom wrapper tools (dynamic):
          Discovered from workspace custom_integrations/
```

Total: ~35+ tools available to the agent in every request.

---

## Cross-System Effects

| If You Change... | Also Check... |
|-----------------|---------------|
| Tool definitions | Agent tool execution switch/case |
| Composio meta-tool names | `_execute_composio_tool()` in agent |
| File generation libraries | `requirements.txt` dependencies |
| Spaces template | `templates/zeeya-main/` directory |
| AgentMail domain | `config.py` settings |
| Rate limiter API limits | Composio execution flow |
| Resolver stages | `integrations/` submodules |
| Tool names | `output.py` sanitization patterns + `_HUMANIZE_MAP` |

---

## Composio Client Internals

**File:** `src/lucy/integrations/composio_client.py`

### Session Management

Each workspace gets a Composio session (entity). Sessions are cached
in memory with an LRU eviction policy (max 200 sessions).

```
get_composio_client()  →  singleton ComposioClient
    │
    ├── _get_session(workspace_id)
    │     ├── Check LRU cache
    │     ├── If cached → return
    │     └── If not → create new session, cache, return
    │
    └── _get_session_with_recovery(workspace_id)
          ├── Try _get_session()
          ├── On stale/expired session error:
          │     ├── Invalidate cache for workspace
          │     └── Retry _get_session()
          └── Return recovered session
```

### The 5 Meta-Tools

Composio exposes exactly 5 meta-tools to the LLM:

| Meta-Tool | Purpose |
|-----------|---------|
| `COMPOSIO_SEARCH_TOOLS` | Search for available tools/actions by keyword |
| `COMPOSIO_EXECUTE_TOOL` | Execute a discovered tool/action |
| `COMPOSIO_MANAGE_CONNECTIONS` | Connect/disconnect OAuth services |
| `COMPOSIO_GET_INTEGRATIONS` | List available integrations |
| `COMPOSIO_CHECK_CONNECTIONS` | Check which services are connected |

Tool schemas are cached for 30 minutes per workspace.

### Auto-Repair for MANAGE_CONNECTIONS

When `COMPOSIO_MANAGE_CONNECTIONS` fails with a "not found" error:

```
LLM calls MANAGE_CONNECTIONS with toolkit="google_drive"
    │
    ├── Composio returns: "Toolkit 'google_drive' not found"
    │
    ├── Auto-repair kicks in:
    │     ├── Try common corrections: "google_drive" → "googledrive"
    │     ├── If still fails: Search for toolkit name via API
    │     ├── Map recovered slug back to LLM request
    │     └── Retry with corrected toolkit name
    │
    └── Returns result with corrected name
```

### Retryable Errors

Errors containing these keywords trigger retries with exponential
backoff (max 3 attempts):

```
"500", "502", "503", "504", "402", "601", "901",
"timeout", "temporarily", "rate limit", "connection reset"
```

### Context Optimization

`current_user_info` is stripped from Composio responses to avoid
context window bloat (it can contain verbose user profile data).

### Connected App Discovery

`get_connected_app_names_reliable(workspace_id)` uses multiple
detection methods with fallback:

1. Primary: Query Composio SDK for connected apps
2. Fallback: Check cached session data
3. Last resort: Return empty list (agent will discover on demand)

Results are injected into the system prompt so the agent knows which
services are available without needing a tool call.

---

## OpenClaw LLM Client Internals

**File:** `src/lucy/core/openclaw.py`

### Response Caching

Caches LLM responses for deterministic calls (no tools, short input):

| Condition | Cached? |
|-----------|---------|
| Has tools | No |
| Input > 200 chars | No |
| Multiple messages | No |
| Single short message | Yes (5-minute TTL) |

Cache key: `"{model}:{system_prompt_hash}:{content}"`

Cache eviction: When cache exceeds 500 entries, oldest 100 are removed.

### Retry Logic

Uses `tenacity` for automatic retries:

| Setting | Value |
|---------|-------|
| Max attempts | 3 |
| Wait strategy | Exponential backoff |
| Wait multiplier | 1 second |
| Min wait | 1 second |
| Max wait | 4 seconds |
| Retryable status codes | 429, 500, 502, 503, 504 |
| Retryable exceptions | `httpx.ReadTimeout`, `httpx.ConnectTimeout`, `httpx.PoolTimeout` |

### Rate Limiting Integration

Before every LLM call:

```python
limiter = get_rate_limiter()
acquired = await limiter.acquire_model(config.model, timeout=30.0)
if not acquired:
    raise OpenClawError("Rate limited", status_code=429)
```

### HTTP Client Configuration

| Setting | Value |
|---------|-------|
| Connect timeout | 5 seconds |
| Read timeout | `settings.openclaw_read_timeout` (default 120s) |
| Write timeout | 5 seconds |
| Pool timeout | 15 seconds |
| Max connections | 20 |
| Max keepalive | 10 |

### SOUL.md Loading

`load_soul()` loads `prompts/SOUL.md` as the default system prompt.
Falls back to a hardcoded default if the file is missing. The SOUL
is automatically prepended when no `system_prompt` is provided in
`ChatConfig`.

### Tool Call Parsing

`_parse_tool_calls(raw_tool_calls)` converts OpenAI-format tool calls:

```python
# Input (from OpenAI API):
[{"id": "call_123", "function": {"name": "tool", "arguments": "{\"key\": \"value\"}"}}]

# Output (internal format):
[{"id": "call_123", "name": "tool", "parameters": {"key": "value"}, "parse_error": None}]
```

Handles malformed JSON arguments gracefully — sets `parse_error` instead
of crashing, allowing the agent to report the issue to the user.

---

## Integration Resolver Pipeline Detail

**File:** `src/lucy/integrations/resolver.py`

### Three-Stage Resolution

When the agent needs a tool that isn't in Composio's registry:

```
Stage 1: MCP Check
    ├── Is there an MCP server that provides this tool?
    ├── If yes → install MCP server, register tools → done
    └── If no → Stage 2

Stage 2: OpenAPI Registration
    ├── Does the service have an OpenAPI spec?
    ├── Try URL variants: /openapi.json, /swagger.json, /api-docs
    ├── If found → register with Composio as custom app → done
    └── If not found → Stage 3

Stage 3: Wrapper Generation
    ├── Use LLM to research the API (grounded search)
    ├── Generate Python wrapper with endpoint stubs
    ├── Store wrapper + meta.json in integrations directory
    └── Register as custom tool → done
```

### Decision Logging

Each resolution attempt is logged with timing metrics:

```json
{
    "service": "polar",
    "stage": "wrapper_generation",
    "duration_ms": 2340,
    "success": true,
    "tools_registered": 5
}
```

### Grounded Search (Stage 3 Preprocessing)

**File:** `src/lucy/integrations/grounded_search.py`

Two-phase research before wrapper generation:

**Phase 1: Classification**
- Identifies the service type (REST API, GraphQL, SDK)
- Determines authentication method (API key, OAuth, Bearer)
- Classifies available endpoints

**Phase 2: Endpoint Discovery**
- Finds specific API endpoints via web search
- Extracts request/response formats
- Builds endpoint inventory for the wrapper generator

### Wrapper Generator

**File:** `src/lucy/integrations/wrapper_generator.py`

- Uses LLM to generate a Python API wrapper from endpoint inventory
- Validates generated code syntax
- Stores wrapper file + `meta.json` (name, version, endpoints)
- Wrappers are placed in `integrations/custom_wrappers/`

### Custom Wrappers Registry

**File:** `src/lucy/integrations/custom_wrappers/__init__.py`

Auto-discovers wrapper modules at startup:
- Scans `custom_wrappers/` directory
- Imports each module
- Registers tools from `get_tools()` export
- Makes them available as `lucy_custom_{service}_{action}` tools
