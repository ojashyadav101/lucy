---
name: Lucy Spaces Feature
overview: Build the "Lucy Spaces" feature - a platform that enables Lucy to build, test, deploy, and host full-stack web applications on `zeeya.app`, modeled after Viktor's Spaces architecture but natively integrated into Lucy's existing codebase.
todos:
  - id: phase-0a
    content: "Phase 0a: Prerequisites -- Add Convex token to keys.json, get Vercel token and add it, configure zeeya.app wildcard DNS to Vercel."
    status: completed
  - id: phase-0b
    content: "Phase 0b: Environment validation -- Test if Composio sandbox can run bun/node, install npm packages, build a Vite project, and reach Convex Cloud. If not, set up Docker sandbox on VPS."
    status: completed
  - id: phase-1
    content: "Phase 1: Build the Lucy Spaces template at templates/lucy-spaces/ -- adapt Viktor calculator reference to Lucy (rename viktorTools->lucyTools, ViktorSpacesEmail->LucySpacesEmail using AgentMail, 53 shadcn components, Playwright tests, README agent guide)."
    status: completed
  - id: phase-2
    content: "Phase 2: Build platform service at src/lucy/spaces/ -- Convex Management API wrapper, Vercel REST API wrapper, and 6 core functions (init, deploy, list, status, query, delete)."
    status: completed
  - id: phase-3
    content: "Phase 3: Register 6 lucy_spaces_* tools in src/lucy/tools/spaces.py (following email_tools.py pattern exactly) and wire dispatch into agent.py."
    status: completed
  - id: phase-4
    content: "Phase 4: Add HTTP endpoints to FastAPI app -- /api/lucy-spaces/send-email (OTP via AgentMail) and /api/lucy-spaces/tools/call (tool gateway for deployed apps)."
    status: completed
  - id: phase-5
    content: "Phase 5: Create lucy-spaces skill file at workspace_seeds/skills/lucy-spaces/SKILL.md, add spaces.md prompt module, and skill trigger regex."
    status: completed
  - id: phase-6
    content: "Phase 6: End-to-end test -- Full pipeline from 'build me a calculator' to live URL on zeeya.app, tested via Slack."
    status: completed
isProject: false
---

# Lucy Spaces -- Revised Implementation Plan (v2)

## What Changed Since v1


| Area                  | Before                       | Now                                                                                                          |
| --------------------- | ---------------------------- | ------------------------------------------------------------------------------------------------------------ |
| **Email for OTP**     | Required Resend API ($20/mo) | Uses Lucy's AgentMail ([lucy@zeeyamail.com](mailto:lucy@zeeyamail.com)) -- already integrated, zero new cost |
| **Email tools**       | Not built                    | Live and tested -- exact pattern to follow for spaces tools                                                  |
| **Tool registration** | Theoretical                  | Proven pattern: get_**definitions() + istool() + execute*_tool()                                             |
| **Sub-agent system**  | 2 agents                     | 4 agents (research, code, integrations, document) with SOUL_LITE                                             |
| **Convex key**        | Not available                | Provided (Team ID: 435168)                                                                                   |
| **Domain**            | TBD                          | Confirmed: zeeya.app                                                                                         |
| **Architecture plan** | Monolithic prompt            | v3 restructuring underway (prompt modules, model routing, cache-friendly layout)                             |


---

## Architecture Overview

```
User: "Build me a calculator app"
         |
         v
  Lucy Main Agent
    |           |
    |           v
    |    lucy_spaces_init --> Convex Management API (create project)
    |                     --> Vercel REST API (create project)
    |                     --> Copy template + bun install
    |
    v
  Code Sub-Agent
    |  Writes business logic
    |  Uses COMPOSIO_REMOTE_BASH_TOOL for builds
    |  Uses lucy_write_file for code
    |  Runs Playwright tests
    |
    v
  lucy_spaces_deploy(preview) --> preview-calc-abc.zeeya.app
    |
    v
  Screenshot + Slack --> User approves
    |
    v
  lucy_spaces_deploy(production) --> calc-abc.zeeya.app

OTP Email Flow:
  App user signs up --> Convex Auth --> POST /api/lucy-spaces/send-email
    --> Lucy's AgentMail (lucy@zeeyamail.com) --> User's inbox
```

---

## Technology Stack


| Layer      | Technology                                                  | Why                                                        |
| ---------- | ----------------------------------------------------------- | ---------------------------------------------------------- |
| Backend    | Convex Cloud                                                | Real-time DB + auth + server functions, one-command deploy |
| Frontend   | Vite + React 19 + Tailwind v4                               | Fast builds (~3s), agent-friendly                          |
| UI Kit     | shadcn/ui (53 components)                                   | Pre-installed, agent just imports                          |
| Hosting    | Vercel (git-less deploy via API)                            | Free tier, wildcard domain support                         |
| Domain     | zeeya.app (wildcard: *.zeeya.app)                           | User-specified                                             |
| Testing    | Playwright (headless)                                       | E2E test loop catches agent mistakes                       |
| Runtime    | Bun                                                         | Fast installs and builds                                   |
| Auth Email | AgentMail ([lucy@zeeyamail.com](mailto:lucy@zeeyamail.com)) | Already integrated, replaces Resend                        |


---

## Phase 0a: Prerequisites (Before Writing Any Code)

These are manual/config steps that must be done first.

### Step 1: Add Convex token to keys.json

Add this to the existing keys.json:

```json
"convex": {
    "team_token": "eyJ2MiI6ImFjZTZiYzU1MzI3NjQyYzU5ZDNmM2Y2ZmRhMTQ2ZThkIn0=",
    "team_id": "435168",
    "management_api_url": "https://api.convex.dev"
}
```

Add to config.py (same section as agentmail):

```python
# Lucy Spaces
convex_team_token: str = ""
convex_team_id: str = ""
vercel_token: str = ""
vercel_team_id: str = ""
spaces_domain: str = "zeeya.app"
spaces_enabled: bool = True
```

Wire loading in model_post_init (same pattern as agentmail):

```python
if not self.convex_team_token:
    cx = keys.get("convex", {})
    if cx.get("team_token"):
        self.convex_team_token = cx["team_token"]
    if cx.get("team_id"):
        self.convex_team_id = cx["team_id"]

if not self.vercel_token:
    vl = keys.get("vercel", {})
    if vl.get("token"):
        self.vercel_token = vl["token"]
    if vl.get("team_id"):
        self.vercel_team_id = vl["team_id"]
```

### Step 2: Get Vercel token

**ACTION REQUIRED from user**: Create a Vercel account (if not already), generate an API token at [https://vercel.com/account/tokens](https://vercel.com/account/tokens), and provide it so we can add to keys.json:

```json
"vercel": {
    "token": "<VERCEL_ACCESS_TOKEN>",
    "team_id": "<OPTIONAL_TEAM_ID>"
}
```

### Step 3: Configure zeeya.app DNS

Point zeeya.app nameservers to Vercel:

- ns1.vercel-dns.com
- ns2.vercel-dns.com

OR add a wildcard CNAME record:

- *.zeeya.app -> cname.vercel-dns.com

Vercel handles SSL certificates automatically per subdomain.

### Checklist before proceeding:

- Convex token in keys.json
- Vercel token in keys.json
- zeeya.app DNS configured (wildcard to Vercel)
- Spaces config vars added to config.py
- Config loading from keys.json wired (same pattern as agentmail)

---

## Phase 0b: Environment Validation

Before building anything, validate that Lucy's execution environment can build apps.

### Test 1: Node.js/Bun availability

```bash
node --version && bun --version && npm --version
```

### Test 2: Package installation

```bash
mkdir /tmp/test-spaces && cd /tmp/test-spaces
npm init -y && npm install react react-dom vite
npx vite --version
```

### Test 3: Vite build

```bash
echo '<div id="root"></div>' > index.html
echo 'document.getElementById("root").textContent = "hello"' > main.js
npx vite build && ls dist/
```

### Test 4: Network access to Convex

```bash
curl -s -o /dev/null -w "%{http_code}" https://api.convex.dev/version
```

### Decision tree:

- **All pass** -> Use Composio sandbox directly (simplest)
- **Node/Bun missing** -> Install via sandbox setup script
- **Network blocked** -> Build locally on VPS, upload artifacts
- **Everything fails** -> Docker sandbox on VPS (docker run -v ... node:22-slim)

---

## Phase 1: Template Repository

Create templates/lucy-spaces/ by adapting the Viktor reference at reference/viktor/viktor-spaces/calculator/.

### What to copy as-is:

- All src/components/ui/ (53 shadcn components)
- src/contexts/ThemeContext.tsx
- src/hooks/use-mobile.tsx
- convex/schema.ts, convex/auth.ts, convex/auth.config.ts
- convex/http.ts, convex/users.ts, convex/testAuth.ts, convex/seedTestUser.ts
- scripts/test.ts, scripts/auth.ts, scripts/screenshot.ts
- package.json (update name), vite.config.ts, vercel.json
- All tsconfig files

### What to adapt:

convex/viktorTools.ts -> convex/lucyTools.ts:

- VIKTOR_SPACES_API_URL -> LUCY_SPACES_API_URL
- VIKTOR_SPACES_PROJECT_NAME -> LUCY_SPACES_PROJECT_NAME
- VIKTOR_SPACES_PROJECT_SECRET -> LUCY_SPACES_PROJECT_SECRET
- Endpoint /api/viktor-spaces/tools/call -> /api/lucy-spaces/tools/call

convex/ViktorSpacesEmail.ts -> convex/LucySpacesEmail.ts:

- All VIKTOR_SPACES_* env vars to LUCY_SPACES_*
- Endpoint /api/viktor-spaces/send-email -> /api/lucy-spaces/send-email
- Backend handler uses AgentMail ([lucy@zeeyamail.com](mailto:lucy@zeeyamail.com)) instead of Resend

convex/constants.ts:

- Change default APP_NAME

src/App.tsx and page components:

- Generic starter (landing page, dashboard, settings) -- agent fills in the rest

README.md:

- Comprehensive agent developer guide (~20KB)
- Must cover: project structure, adding pages, adding Convex functions, using shadcn, running tests, deploying
- This is the single most important file -- it is Lucy's manual for building apps

.env.template:

```
VITE_CONVEX_URL=
LUCY_SPACES_API_URL=
LUCY_SPACES_PROJECT_NAME=
LUCY_SPACES_PROJECT_SECRET=
```

### Template structure:

```
templates/lucy-spaces/
  convex/
    schema.ts, auth.ts, auth.config.ts, http.ts
    lucyTools.ts          # Adapted from viktorTools.ts
    LucySpacesEmail.ts    # Uses AgentMail via Lucy API
    users.ts, testAuth.ts, seedTestUser.ts, constants.ts
  src/
    components/ui/        # 53 shadcn components (copied)
    components/           # AppLayout, PublicLayout, ProtectedRoute, SignIn, SignUp
    pages/                # DashboardPage, LandingPage, SettingsPage
    contexts/             # ThemeContext
    hooks/                # use-mobile
    App.tsx, main.tsx, index.css
  scripts/
    test.ts, auth.ts, screenshot.ts
  package.json, vite.config.ts, vercel.json
  tsconfig.json, tsconfig.app.json, tsconfig.node.json
  README.md               # 20KB+ agent guide (CRITICAL)
  .env.template
```

---

## Phase 2: Platform Service

New module: src/lucy/spaces/

### src/lucy/spaces/**init**.py

Public API exports.

### src/lucy/spaces/convex_api.py

Async wrapper around Convex Management API using httpx.AsyncClient:

- create_project(team_id, name) -> dict
- list_projects(team_id) -> list[dict]
- create_deployment(project_id, kind) -> dict  # "dev" or "prod"
- get_deploy_key(deployment_id) -> str
- set_env_var(deployment_name, key, value)
- delete_project(project_id)

Authentication: Authorization: Bearer {team_token}
Base URL: [https://api.convex.dev](https://api.convex.dev)

### src/lucy/spaces/vercel_api.py

Async wrapper around Vercel REST API:

- create_project(name) -> dict
- deploy_files(project_id, files, target) -> dict  # git-less upload
- add_domain(project_id, domain) -> dict
- get_deployment(deployment_id) -> dict
- delete_project(project_id)

Authentication: Authorization: Bearer {vercel_token}
Base URL: [https://api.vercel.com](https://api.vercel.com)

Vercel git-less deploy process:

1. Upload each file: POST /v2/files (with x-vercel-digest header = sha1 of content)
2. Create deployment: POST /v13/deployments with file list referencing uploaded SHAs

### src/lucy/spaces/platform.py

Six core async functions:

init_app_project(project_name, description, workspace_id):

1. Validate project name (slug-safe, unique)
2. Copy template to {workspace_root}/{workspace_id}/spaces/{project_name}/
3. Create Convex project via Management API
4. Create dev + prod deployments
5. Create Vercel project
6. Configure subdomain: {project_name}-{short_hash}.zeeya.app
7. Generate project_secret (32-byte hex)
8. Write .env.local with all secrets
9. Run bun install in sandbox
10. Push initial Convex functions (bunx convex dev --once)
11. Return {success, sandbox_path, convex_url_dev, preview_url}

deploy_app(project_name, workspace_id, environment="preview"):

1. Build frontend: bun run sync:build
2. Upload dist/ to Vercel via REST API
3. Return {success, url, deployment_id}

list_apps(workspace_id) -- Scan workspace spaces dir
get_app_status(project_name, workspace_id) -- Read config + check Vercel
query_app_database(project_name, query_path, args, workspace_id) -- Run Convex query
delete_app_project(project_name, workspace_id) -- Cleanup Convex + Vercel + local

### src/lucy/spaces/config.py

Per-project config stored as project.json in each space directory.

---

## Phase 3: Internal Tools

### src/lucy/tools/spaces.py

Follow the exact pattern from src/lucy/tools/email_tools.py:

```python
def get_spaces_tool_definitions() -> list[dict[str, Any]]:
    """Return OpenAI-format tool definitions for spaces operations."""
    return [
        # lucy_spaces_init, lucy_spaces_deploy, lucy_spaces_list,
        # lucy_spaces_status, lucy_spaces_query_db, lucy_spaces_delete
    ]

_SPACES_TOOL_NAMES = frozenset({...})

def is_spaces_tool(tool_name: str) -> bool:
    return tool_name in _SPACES_TOOL_NAMES

async def execute_spaces_tool(tool_name, parameters, workspace_id, ...) -> dict:
    ...
```

### Integration into agent.py

Tool definitions (in run(), after email tools):

```python
from lucy.tools.spaces import get_spaces_tool_definitions
if settings.spaces_enabled:
    tools.extend(get_spaces_tool_definitions())
```

Tool dispatch (in *execute_internal_tool(), before lucy_custom*):

```python
from lucy.tools.spaces import is_spaces_tool
if is_spaces_tool(tool_name):
    from lucy.tools.spaces import execute_spaces_tool
    return await execute_spaces_tool(tool_name, parameters, workspace_id, ...)
```

System prompt (in prompt.py, after email identity block):

```python
if settings.spaces_enabled:
    static_parts.append(
        "<spaces_capability>\n"
        "You can build and deploy full-stack web applications.\n"
        "Use lucy_spaces_init to create a new app project, then write code, "
        "test with Playwright, and deploy with lucy_spaces_deploy.\n"
        "Apps are hosted on zeeya.app with their own subdomain.\n"
        "</spaces_capability>"
    )
```

---

## Phase 4: HTTP Endpoints

Add to src/lucy/app.py (FastAPI):

### OTP Email Endpoint (uses AgentMail)

POST /api/lucy-spaces/send-email

- Validates project_secret against stored project config
- Rate limits (100/hr per project)
- Sends via AgentMail: get_email_client().send_email(...)
- Returns {success: true}

This is the key win over Viktor's architecture: no Resend dependency, zero incremental cost.

### Tool Gateway Endpoint

POST /api/lucy-spaces/tools/call

- Validates project_secret
- Routes to appropriate tool (Composio search, grounded search, etc.)
- Returns {success: true, result: ...}

---

## Phase 5: Skill File + Prompt Module

### workspace_seeds/skills/lucy-spaces/SKILL.md

Development workflow guide for Lucy:

1. Call lucy_spaces_init to scaffold
2. Read README.md (the agent's manual)
3. Create todo.md plan
4. Implement features (backend -> frontend -> test loop)
5. Deploy preview -> screenshot -> user approval -> deploy production

### assets/prompt_modules/spaces.md

Intent-specific prompt loaded when router detects app-building requests.

### Skill trigger regex

Add: "build.*app|create.*app|web.*app|deploy.*app|lucy.spaces" -> "lucy-spaces"

---

## Phase 6: End-to-End Testing

1. Unit tests for convex_api.py and vercel_api.py (mock HTTP)
2. Integration test: init_app_project -> verify Convex + Vercel created
3. Build test: Write calculator -> bun run sync:build -> verify dist/
4. Deploy test: Upload to Vercel -> verify live at test-calc-xxx.zeeya.app
5. Live Slack test: "build me a calculator app" -> full pipeline to live URL

---

## What We Still Need from the User


| Item                   | Status                     | Notes                                                        |
| ---------------------- | -------------------------- | ------------------------------------------------------------ |
| Convex team token      | Provided                   | Added to plan                                                |
| Convex team ID         | Provided                   | 435168                                                       |
| Vercel access token    | **NEEDED**                 | Create at vercel.com/account/tokens                          |
| Vercel team ID         | **NEEDED** (if using team) | Optional, personal account works                             |
| zeeya.app DNS access   | **NEEDED**                 | Point to Vercel nameservers or wildcard CNAME                |
| AgentMail (OTP emails) | Done                       | [lucy@zeeyamail.com](mailto:lucy@zeeyamail.com) already live |


---

## Risk Assessment


| Risk                                 | Likelihood | Impact | Mitigation                                   |
| ------------------------------------ | ---------- | ------ | -------------------------------------------- |
| Composio sandbox can't run Bun       | Medium     | High   | Phase 0b validation; fallback to VPS Docker  |
| Convex free tier limit (20 projects) | Medium     | Medium | Cleanup old projects; upgrade to Pro         |
| Agent writes buggy code              | High       | Medium | Mandatory Playwright test loop before deploy |
| Vercel API rate limits               | Low        | Low    | Batch operations; retry with backoff         |
| AgentMail rate limits for OTP        | Low        | Low    | Rate limit per project (100/hr)              |


---

## Cost Estimate


| Service                    | Free Tier                           | Paid Estimate               |
| -------------------------- | ----------------------------------- | --------------------------- |
| Convex                     | 20 projects, 1M function calls/mo   | ~$25/mo Pro if exceeded     |
| Vercel                     | 100 deploys/day, unlimited projects | Free tier likely sufficient |
| Domain (zeeya.app)         | -                                   | ~$15/year                   |
| AgentMail (OTP emails)     | Already paying                      | $0 incremental              |
| LLM tokens (building apps) | -                                   | ~$0.50-2.00 per app build   |


---

## Execution Order

Phase 0a: Prerequisites (config + keys + DNS)     [30 min manual setup]
Phase 0b: Environment validation                   [1-2 hours]
Phase 1:  Template repository                      [1-2 days]
Phase 2:  Platform service                         [2-3 days]
Phase 3:  Internal tools + agent wiring            [0.5 day]
Phase 4:  HTTP endpoints                           [0.5 day]
Phase 5:  Skill + prompt module                    [0.5 day]
Phase 6:  End-to-end testing                       [1-2 days]
                                          Total:    6-9 days

Start with Phase 0a. Once the Vercel token and DNS are confirmed, Phase 0b validates the build environment, and from there it is straight implementation.