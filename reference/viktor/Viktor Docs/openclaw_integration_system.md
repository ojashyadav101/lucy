# OpenClaw Integration Management System
## Complete Implementation Guide
### How to Connect, Manage, and Build Integrations for a Slack-Native AI Assistant

---

## Executive Summary

This document covers three things:
1. **How to know what's connected** — discovering and cataloging available integrations
2. **How to use the right tool** — routing requests to the correct integration
3. **How to build custom integrations** — when the pre-built one doesn't exist or doesn't work

---

## Part 1: Integration Architecture

### 1.1 The Three-Layer Model

```
┌─────────────────────────────────────────────────────┐
│  LAYER 3: CUSTOM API INTEGRATIONS                    │
│  For services not in the catalog, or when pre-built  │
│  integrations are broken. Direct HTTP connections.   │
│  You build these yourself.                           │
├─────────────────────────────────────────────────────┤
│  LAYER 2: PRE-BUILT INTEGRATIONS (3,000+)           │
│  Middleware-provided connections (e.g., via Pipedream)│
│  OAuth handled automatically. Proxy HTTP methods     │
│  + pre-built actions for common operations.          │
├─────────────────────────────────────────────────────┤
│  LAYER 1: NATIVE INTEGRATIONS                        │
│  Deep, first-party integrations built into the       │
│  platform. Richer functionality, tighter coupling.   │
│  Examples: Slack, GitHub, Google Drive, Linear       │
└─────────────────────────────────────────────────────┘
```

### 1.2 How Integrations Become Tools

When a user connects an integration, the platform:

1. Handles the OAuth flow or API key storage (secure, never visible to AI)
2. Generates Python SDK wrapper files in `sdk/tools/`
3. Updates `sdk/docs/tools.md` with the new function list
4. The AI can immediately call the new functions

**File naming convention:**
```
sdk/tools/
├── mcp_{service}.py           ← Native/MCP integrations (deep functionality)
├── pd_{service}.py            ← Pipedream integrations (middleware)
├── custom_api_{id}.py         ← Custom API integrations (self-built)
├── {service}_tools.py         ← Platform-native tools
└── default_tools.py           ← Always-available core tools
```

**Example: What a generated tool file looks like:**
```python
# sdk/tools/pd_google_sheets.py (auto-generated)

async def pd_google_sheets_add_single_row(
    sheet_id: str,
    worksheet_name: str,
    row_data: dict,
    header_row: int = 1
) -> dict:
    """Add a single row to a Google Sheets spreadsheet.

    Args:
        sheet_id: The spreadsheet ID (from the URL)
        worksheet_name: Name of the worksheet tab
        row_data: Dict mapping column headers to values
        header_row: Row number containing headers (default 1)
    """
    return await get_client().call(
        "pd_google_sheets_add_single_row",
        sheet_id=sheet_id,
        worksheet_name=worksheet_name,
        row_data=row_data,
        header_row=header_row
    )
```

The AI doesn't need to know how OAuth works. It just calls the function.

---

## Part 2: Knowing What's Connected

### 2.1 The Source of Truth

The single source of truth for available tools is: `sdk/docs/tools.md`

This file lists every callable function, grouped by integration module.

**How to check what's connected:**
```bash
# Read the full tool list
cat sdk/docs/tools.md

# Check for a specific integration
grep -n "linear\|pd_linear\|mcp_linear" sdk/docs/tools.md

# Count functions per integration
grep "^## " sdk/docs/tools.md
```

### 2.2 The Integration Catalog

The full catalog of available (connectable but not necessarily connected) integrations lives in:
`sdk/docs/available_integrations.json`

This is a large file (3,000+ entries). Each entry has:
```json
{
  "name": "Stripe",
  "slug": "stripe",
  "description": "Payment processing platform",
  "connect_url": "https://app.platform.com/integrations/stripe",
  "integration_type": "pipedream",
  "auth_type": "oauth"
}
```

**How to check if a service is available:**
```bash
# Search the catalog
grep -i "stripe" sdk/docs/available_integrations.json
```

### 2.3 Integration Knowledge Files

For each connected integration, maintain a knowledge file:

```
memory/knowledge/integrations/
├── linear/
│   └── KNOWLEDGE.md        ← IDs, working examples, known issues
├── google-sheets/
│   └── KNOWLEDGE.md        ← Working vs broken actions, helper functions
├── polar/
│   └── KNOWLEDGE.md        ← Export workaround, proxy issues documented
└── clerk/
    └── KNOWLEDGE.md        ← Auth broken, API surface ready for when fixed
```

These files are what make the AI effective with integrations. Without them, every instance has to re-discover how each integration works.

### 2.4 The System Prompt Section for Integrations

```
<integration_management>
## Integrations

You can connect to 3,000+ third-party services. Check what's currently
connected by reading sdk/docs/tools.md. Check what's available to
connect by searching sdk/docs/available_integrations.json.

### Currently Connected
Read sdk/docs/tools.md for the authoritative list.

### Integration Knowledge
For each connected integration, check memory/knowledge/integrations/{name}/
for account structure, key IDs, working examples, and known issues.

### When a Tool Call Fails
1. Check the error message — is it auth? endpoint? permissions?
2. Check the knowledge file — is this a known issue?
3. Try alternative approaches (proxy endpoint, different function)
4. If unfixable: document the issue, suggest workarounds
5. If the integration isn't connected: guide the user to connect it

### When an Integration Isn't Available
If the service isn't in the 3,000+ catalog, create a custom API integration.
See: memory/knowledge/custom-api-integration.md
</integration_management>
```

---

## Part 3: Routing — Knowing Which Tool to Use

### 3.1 The Decision Tree

When a user asks for something that requires an external service:

```
User asks: "Create a bug ticket for the payment issue"

Step 1: IDENTIFY THE SERVICE
  → "Bug ticket" = project management = Linear

Step 2: CHECK IF CONNECTED
  → grep "linear" sdk/docs/tools.md
  → Found: mcp_linear module with 40+ functions ✓

Step 3: CHECK KNOWLEDGE FILE
  → Read memory/knowledge/integrations/linear/KNOWLEDGE.md
  → Has: Team ID (85af8f8d), Bug label ID (abc123), working examples ✓

Step 4: SELECT THE RIGHT FUNCTION
  → Task is "create a ticket" = linear_create_issue
  → Needs: team_id, title, description, label_ids, priority

Step 5: EXECUTE
  → Call linear_create_issue with the right parameters
  → Report the result to the user
```

### 3.2 Service-to-Tool Mapping

Maintain a mental model (reinforced by knowledge files) of which service handles what:

| Task Category | Service | Tool Prefix | Example Function |
|--------------|---------|-------------|------------------|
| Project management | Linear | `mcp_linear` / `linear_` | `linear_create_issue()` |
| Billing/revenue | Polar/Stripe | `pd_polar` / `custom_api_polar` | `custom_api_polar_get("/v1/subscriptions/export")` |
| Spreadsheets | Google Sheets | `pd_google_sheets` | `pd_google_sheets_add_single_row()` |
| Calendar | Google Calendar | `pd_google_calendar` | `pd_google_calendar_create_event()` |
| Source code | GitHub | `github_tools` | `coworker_git("status")` |
| Deployment | Vercel | `pd_vercel_token_auth` | `pd_vercel_token_auth_list_deployments()` |
| Web scraping | Bright Data | `pd_bright_data` | `pd_bright_data_scrape()` |
| Email | Built-in | `email_tools` | `coworker_send_email()` |
| File storage | Google Drive | `google_drive_tools` | `gdrive_upload()` |
| Auth/users | Clerk | `pd_clerk` | `pd_clerk_list_users()` |

### 3.3 Handling Ambiguity

When it's not clear which tool to use:

1. **Check the knowledge file** — it might have notes about which approach works
2. **Check tools.md** — scan for relevant function names
3. **Try the most specific tool first** — `pd_google_sheets_add_single_row` over `pd_google_sheets_proxy_post`
4. **Fall back to proxy tools** — if pre-built actions don't work, use `pd_{service}_proxy_get/post`
5. **Fall back to custom API** — if the proxy doesn't work either

### 3.4 The Proxy Fallback Pattern

Many Pipedream integrations have limited pre-built actions but unlimited proxy access:

```python
# Pre-built action (preferred — type-safe, documented):
result = await pd_service.pd_service_specific_action(param1, param2)

# Proxy fallback (works when pre-built actions don't exist):
result = await pd_service.pd_service_proxy_get(
    path="/v1/some/endpoint",
    query_params={"limit": 100}
)

# Proxy POST for write operations:
result = await pd_service.pd_service_proxy_post(
    path="/v1/some/endpoint",
    body={"key": "value"}
)
```

**When to use proxy vs. pre-built:**
- Pre-built action exists and works → use it
- Pre-built action exists but is broken → use proxy
- No pre-built action for your endpoint → use proxy
- Proxy is also broken → build a custom API integration

---

## Part 4: Integration Exploration (New Integration Connected)

### 4.1 Exploration Checklist

When a new integration is connected, explore it systematically:

```
□ 1. READ tools.md — what functions are available?
□ 2. IDENTIFY read-only functions — use these for exploration
□ 3. MAP account structure — organizations, workspaces, projects, IDs
□ 4. TEST key operations — does list work? does search work?
□ 5. DOCUMENT key IDs — workspace ID, team ID, project IDs
□ 6. DOCUMENT working examples — copy-pasteable code snippets
□ 7. NOTE broken operations — what doesn't work and why
□ 8. WRITE helper functions — for common operations
□ 9. CREATE knowledge file — save everything to KNOWLEDGE.md
□ 10. LOG the exploration — to daily log and LEARNINGS.md
```

### 4.2 Exploration Prompt for New Integrations

When a new integration is connected, use this exploration approach:

```
A new integration has been connected: {service_name}

## Exploration Protocol

### Phase 1: Discovery (read-only)
1. Check sdk/docs/tools.md for all available functions
2. Categorize functions:
   - List/Read operations (safe to call)
   - Create/Update/Delete operations (do NOT call without permission)
3. Call each read-only function to map the account:
   - List workspaces/organizations
   - List projects/repositories/boards
   - List users/team members
   - List any key configurations

### Phase 2: Documentation
Create memory/knowledge/integrations/{service}/KNOWLEDGE.md with:
- Account structure (what the workspace looks like)
- Key IDs (workspace ID, project IDs — things future agents need)
- Working examples for key operations (read AND write)
- Known issues or limitations

### Phase 3: Helper Functions (if needed)
If the integration has limited pre-built actions:
- Write helper functions in memory/knowledge/integrations/{service}/scripts/
- Test each helper with read-only operations
- Document the helpers in KNOWLEDGE.md

### Rules
- Use READ-ONLY tools during exploration. Never create/modify/delete.
- If an operation fails, document the error. Don't retry destructively.
- Focus on things that will save time for future interactions.
```

### 4.3 Example: Exploring a Linear Integration

```python
# Step 1: Discover available functions
# (Read sdk/tools/mcp_linear.py to see all functions)

# Step 2: Map the account structure
teams = await mcp_linear.linear_list_teams()
# → Found: "Mentions App" (ID: 85af8f8d)

projects = await mcp_linear.linear_list_projects()
# → Found: 17 projects

users = await mcp_linear.linear_list_users()
# → Found: 8 team members

labels = await mcp_linear.linear_list_issue_labels()
# → Found: Bug (abc123), Feature (def456), Enhancement (ghi789)

# Step 3: Document everything
# → Write to memory/knowledge/integrations/linear/KNOWLEDGE.md
```

---

## Part 5: Building Custom Integrations

### 5.1 When to Build a Custom Integration

Build a custom integration when:
- The service is NOT in the 3,000+ integration catalog
- The pre-built integration is broken (OAuth issues, proxy blocked)
- You need endpoints that the pre-built integration doesn't expose
- The proxy approach doesn't work (redirect issues, custom auth)

### 5.2 The Custom Integration Workflow

```
Step 1: RESEARCH THE API
├── Find official API documentation
├── Identify base URL (e.g., https://api.service.com/v1)
├── Identify authentication method (Bearer token, API key, Basic auth, OAuth)
├── List the endpoints you need
└── Note any quirks (required headers, pagination, rate limits)

Step 2: CREATE THE INTEGRATION
├── Call create_custom_api_integration() with:
│   ├── name: "Service Name"
│   ├── base_url: "https://api.service.com"
│   ├── api_type: "rest"
│   ├── methods: ["GET", "POST", "PUT", "DELETE"]  (what you need)
│   └── auth_config: (see auth types below)
├── Platform returns a secure connect_url
└── Send connect_url to the user (never ask for credentials in chat)

Step 3: USER PROVIDES CREDENTIALS
├── User fills in the secure form
├── Platform stores credentials securely
├── New SDK tool files are auto-generated
└── Functions like custom_api_{slug}_get() become available

Step 4: BUILD HIGHER-LEVEL FUNCTIONS
├── Wrap the raw HTTP functions into domain-specific helpers
├── Add error handling and retry logic
├── Test with read-only operations
└── Document in the knowledge file

Step 5: DOCUMENT EVERYTHING
├── Update knowledge file with working endpoints
├── Note any API quirks discovered
└── Save helper functions for future use
```

### 5.3 Authentication Configuration

**Bearer Token (most common):**
```json
{
  "type": "bearer",
  "token": {
    "label": "API Token",
    "placeholder": "sk-...",
    "description": "Find this in your account dashboard under Settings → API",
    "secret": true
  }
}
```

**API Key in Header:**
```json
{
  "type": "header",
  "header_name": { "label": "Header Name", "value": "X-API-Key" },
  "header_value": {
    "label": "API Key",
    "placeholder": "your-api-key",
    "secret": true
  }
}
```

**Basic Authentication:**
```json
{
  "type": "basic",
  "username": { "label": "Username", "placeholder": "user@example.com" },
  "password": { "label": "Password", "placeholder": "...", "secret": true }
}
```

**Query Parameter:**
```json
{
  "type": "query-parameter",
  "query_parameters": [
    {
      "name": { "value": "api_key" },
      "value": { "label": "API Key", "placeholder": "...", "secret": true }
    }
  ]
}
```

**No Authentication:**
```json
{ "type": "none" }
```

### 5.4 The Credential Security Rule

**NEVER ask users to paste credentials in chat.** Always use the secure form provided by `create_custom_api_integration()`. The secure form:
- Stores credentials server-side, encrypted
- Credentials are never visible to the AI
- Credentials are injected into API calls at the platform level

### 5.5 Real-World Example: Building a Custom Polar Integration

This is the actual story from Viktor's workspace — a case study in custom integration building.

**Timeline:**

```
3:15 PM — User asks for MRR from Polar billing platform
         → Try pre-built integration: pd_polar_proxy_get("/v1/subscriptions/")
         → FAIL: "domain not allowed for this app"
         → Root cause: Pipedream proxy hasn't whitelisted api.polar.sh

3:17 PM — Ask user to reconnect Polar OAuth
         → User reconnects
         → Same error. Platform-level issue, not auth.

3:22 PM — Build custom API integration:
         1. Research Polar API docs (114 endpoints documented)
         2. Call create_custom_api_integration():
            - name: "Polar"
            - base_url: "https://api.polar.sh"
            - methods: ["GET"]
            - auth_config: { type: "bearer", token: {...} }
         3. Platform generates secure form URL
         4. Send form to user, ask for Polar API token

3:25 PM — User provides token via secure form
         → New tool generated: custom_api_polar_get()
         → Test: custom_api_polar_get("/v1/subscriptions/")
         → FAIL: 307 redirect. Polar requires trailing slash,
           proxy strips it. Infinite redirect loop.

3:28 PM — Try workarounds:
         → URL encoding (%2F) — stripped by proxy
         → Different base URL — same issue
         → Browser automation — requires login, not scalable
         → Honestly admit: "I've hit a wall"

3:39 PM — Breakthrough: Discovery that sub-path endpoints
           (/v1/subscriptions/export) don't require trailing slashes
         → custom_api_polar_get("/v1/subscriptions/export")
         → SUCCESS: Full CSV data returned
         → Parse CSV, compute MRR: $18,743.67 across 192 subscriptions

3:45 PM — Build production-ready solution:
         → Write daily_revenue_report.py (180 lines):
           - 3-retry exponential backoff
           - CSV parsing with plan-level breakdown
           - Snapshot storage for delta calculations
           - Formatted Slack message output
         → Create cron job: 9 AM IST, Mon-Fri
         → Store first snapshot for tomorrow's comparison
         → Update Polar knowledge file with working approach
```

**Key lessons documented:**
```markdown
## Polar Integration — What Works

### Working Approach
- Use custom_api_polar_get() (NOT pd_polar proxy)
- Endpoint: /v1/subscriptions/export (CSV format, no trailing slash needed)
- Returns all active subscriptions with plan, amount, status, dates

### Known Issues
- Pipedream proxy blocks api.polar.sh (domain not whitelisted)
- Polar API requires trailing slashes on most endpoints
- Custom API proxy strips trailing slashes → 307 redirect loop
- WORKAROUND: Use /export sub-path endpoints (don't need trailing slashes)

### Helper Script
- scripts/polar/daily_revenue_report.py
- Includes retry logic, CSV parsing, MRR calculation, delta tracking
```

### 5.6 Debugging Integration Failures

When an integration doesn't work, follow this diagnostic tree:

```
ERROR RECEIVED
│
├── "Not authenticated" / "Unauthorized" / "403"
│   → Auth problem
│   ├── Token expired → Ask user to refresh/reconnect
│   ├── Wrong token format → Check API docs for correct format
│   ├── Insufficient permissions → Ask user to check token scopes
│   └── Document in knowledge file for future reference
│
├── "Domain not allowed" / "App not found"
│   → Proxy/middleware problem
│   ├── The middleware hasn't whitelisted this API's domain
│   ├── Workaround: Build a custom API integration (bypass middleware)
│   └── Document the broken proxy in knowledge file
│
├── "307 Redirect" / "301 Moved"
│   → URL formatting issue
│   ├── Trailing slash required? (common with Python APIs)
│   ├── API version prefix missing? (e.g., /v1/ vs /v2/)
│   ├── HTTPS vs HTTP mismatch?
│   └── Workaround: Find sub-path endpoints that don't redirect
│
├── "404 Not Found"
│   → Wrong endpoint
│   ├── Check API docs for correct path
│   ├── Check if API version changed
│   ├── Check if resource ID is correct
│   └── Try the API's discovery endpoint (if it has one)
│
├── "429 Rate Limited"
│   → Too many requests
│   ├── Add retry logic with exponential backoff
│   ├── Cache responses where possible
│   ├── Reduce polling frequency
│   └── Check API docs for rate limit headers
│
└── "500 Server Error"
    → Their problem, not yours
    ├── Retry with backoff (might be transient)
    ├── Try again in 5-10 minutes
    ├── Check service's status page
    └── If persistent: document and alert the user
```

---

## Part 6: Managing Many Integrations

### 6.1 How to Keep Track of 10, 20, 50 Integrations

As integrations grow, organization becomes critical:

```
memory/knowledge/integrations/
├── _index.md                    ← Summary of all integrations + status
├── linear/KNOWLEDGE.md          ← Working ✓
├── google-sheets/KNOWLEDGE.md   ← Proxy works, actions broken ⚠️
├── polar/KNOWLEDGE.md           ← Custom integration ✓
├── clerk/KNOWLEDGE.md           ← Auth broken ✗
├── github/KNOWLEDGE.md          ← Working ✓
├── vercel/KNOWLEDGE.md          ← Working ✓
└── ...
```

**The _index.md file:**
```markdown
# Integration Status Index

| Service | Status | Module | Key Endpoints | Last Verified |
|---------|--------|--------|---------------|---------------|
| Linear | ✅ Working | mcp_linear | Issues, Projects, Users | 2026-02-14 |
| Google Sheets | ⚠️ Partial | pd_google_sheets | Proxy works, actions broken (OAuth) | 2026-02-14 |
| Polar | ✅ Working | custom_api_polar | /v1/subscriptions/export | 2026-02-14 |
| Clerk | ❌ Broken | pd_clerk | All endpoints (invalid secret key) | 2026-02-14 |
| GitHub | ✅ Working | github_tools | Git, GitHub CLI | 2026-02-14 |
| Google Calendar | ⚠️ Partial | pd_google_calendar | Proxy works, built-in broken | 2026-02-22 |
```

### 6.2 Integration Health Monitoring

Add integration health checks to the heartbeat:

```
During each heartbeat:
1. Read integrations/_index.md
2. For any integration marked as "broken":
   - Try a lightweight read-only call
   - If it now works → update status, alert the user
   - If still broken → note in learnings, move on
3. For any integration with recent errors in logs:
   - Check if the issue is persistent or transient
   - Update knowledge file if persistent
```

### 6.3 When Users Request a New Integration

```
User asks: "Can you connect to [Service]?"

Step 1: Check the catalog
  → grep -i "service" sdk/docs/available_integrations.json
  → Found? → Share the connect_url, guide them through OAuth
  → Not found? → Proceed to Step 2

Step 2: Check if a custom API is feasible
  → Does the service have a documented API? (search web)
  → Is the auth model supported? (bearer, API key, basic, etc.)
  → Yes? → Build a custom API integration
  → No API at all? → Browser automation is the fallback (but flag it as fragile)

Step 3: Connect and explore
  → Once connected, run the exploration checklist (Part 4)
  → Create the knowledge file
  → Report back to the user what's available
```

---

## Part 7: Helper Functions & Scripts

### 7.1 When to Write Helper Functions

Write helpers when:
- The raw API calls require repetitive boilerplate
- You need to combine multiple API calls into one logical operation
- Error handling is complex and shouldn't be repeated
- The same operation is needed by multiple tasks (heartbeat + reports + ad-hoc requests)

### 7.2 Helper Function Pattern

```python
# memory/knowledge/integrations/polar/scripts/polar_helpers.py

import csv
import io
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def get_active_subscriptions():
    """Fetch all active subscriptions from Polar.

    Returns list of dicts with keys:
    - plan_name, amount, interval, status, created_at, customer_email
    """
    from sdk.tools.custom_api_vvpjfwhokmnwxs2d5xgqpm import custom_api_polar_get

    result = await custom_api_polar_get(
        path="/v1/subscriptions/export",
        query_params={"status": "active"}
    )

    # Parse CSV response
    reader = csv.DictReader(io.StringIO(result["body"]))
    return list(reader)


async def compute_mrr():
    """Compute MRR from active subscriptions.

    Returns dict with:
    - mrr: float (total monthly recurring revenue)
    - count: int (number of active subscriptions)
    - by_plan: dict mapping plan_name to {count, mrr}
    """
    subs = await get_active_subscriptions()
    mrr = 0
    by_plan = {}

    for sub in subs:
        amount = float(sub.get("amount", 0)) / 100  # cents to dollars
        interval = sub.get("interval", "month")
        monthly = amount if interval == "month" else amount / 12

        plan = sub.get("plan_name", "Unknown")
        if plan not in by_plan:
            by_plan[plan] = {"count": 0, "mrr": 0}
        by_plan[plan]["count"] += 1
        by_plan[plan]["mrr"] += monthly
        mrr += monthly

    return {
        "mrr": round(mrr, 2),
        "count": len(subs),
        "by_plan": by_plan
    }
```

### 7.3 Documenting Helpers in Knowledge Files

```markdown
## Helper Functions

### scripts/polar_helpers.py
- `get_active_subscriptions()` — Returns list of all active subs as dicts
- `compute_mrr()` — Returns MRR total, count, and breakdown by plan

### Usage
```python
from memory.knowledge.integrations.polar.scripts.polar_helpers import compute_mrr
data = await compute_mrr()
print(f"MRR: ${data['mrr']:,.2f} ({data['count']} subs)")
```​
```

---

*This document describes an integration management architecture for Slack-native AI assistants. The patterns — three-layer integration model, exploration checklists, custom API creation, and failure diagnostic trees — are designed to scale from 5 to 50+ integrations while maintaining reliability and self-healing capability.*
