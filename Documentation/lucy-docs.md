# Lucy — Vision Document

> Last updated: February 21, 2026
> Status: Active Development
> Confidentiality: Internal — Investors, Customers, Developers

---

## Part A: Product Vision

### What Is Lucy?

Lucy is an AI coworker that lives inside your Slack workspace. She has her own persistent cloud computer, connects to thousands of business tools, and does real work — not just answers questions.

She writes code, deploys apps, manages ad campaigns, creates reports, monitors infrastructure, coordinates multi-week projects, and automates repetitive workflows — all from inside Slack threads.

She's not a chatbot. She doesn't generate text for you to copy-paste somewhere else. She takes action directly: sends the email, creates the ticket, publishes the dashboard, adjusts the ad spend. When she spots a problem, she tells you before you notice it.

She's the team member who never sleeps, never forgets, and never drops the ball.

---

### Who Is Lucy For?

**Marketing teams** that manage ad spend across Google, Meta, and LinkedIn — and need daily performance digests, automatic bid adjustments, competitor tracking, and content calendar management without switching between 12 tabs.

**Engineering teams** that want automated PR summaries in `#engineering`, CI failure diagnosis posted to the thread where the deploy was discussed, and an always-watching eye on error rates and latency spikes.

**Operations teams** that coordinate across departments, track project milestones over weeks, need someone to chase down blockers, and produce board-ready reports on demand.

**Founders and executives** who want a real-time pulse on the business — revenue, churn, pipeline, team velocity — without scheduling a meeting or opening a dashboard.

**Any team that uses Slack as their home** and is tired of context-switching between 20 tools to get one thing done.

---

### What Pain Points Does Lucy Solve?

**1. The Context-Switch Tax**
The average knowledge worker switches between 10+ tools per day. Every switch costs 23 minutes of refocus time (UC Irvine research). Lucy eliminates this by bringing every tool's capabilities into the conversation where work actually happens.

**2. "I Forgot to Follow Up"**
Tasks discussed in Slack threads die there. Lucy doesn't forget. She captures commitments, sets follow-ups, and nudges people when deadlines approach — proactively, without being asked.

**3. Reporting Is Manual and Painful**
Pulling data from Stripe, cross-referencing with HubSpot, formatting in Google Sheets, emailing to stakeholders — this takes hours. Lucy does it in seconds, on a recurring schedule, posted to the right channel every Monday morning.

**4. Monitoring Is Reactive**
By the time you see the alert in PagerDuty, customers have already complained. Lucy watches your metrics continuously and tells you "Stripe webhook success rate dropped to 91% in the last hour — want me to investigate?" before the ticket arrives.

**5. Tribal Knowledge Is Trapped in People's Heads**
New hires ask questions that veterans answered 6 months ago in a thread that's now buried. Lucy remembers everything discussed in channels she's part of. She becomes the team's living knowledge base.

**6. Automation Requires Developers**
Setting up a Zapier workflow, configuring a webhook, writing a cron job — all of this requires engineering time. Lucy lets non-technical team members automate workflows through natural language: "Every Monday at 9am, pull our Google Ads metrics and post a summary to #marketing."

---

### Core Features

**1. Execute, Don't Just Answer**
Lucy doesn't tell you how to create a Linear ticket — she creates it. She doesn't summarize your Google Ads — she adjusts the bids. Every response is backed by real action.

**2. Persistent Workspace Memory**
Lucy remembers what your team discusses across channels, builds a knowledge base over time, and uses that context to give better answers and make smarter decisions. She knows your sprint cycle is 2 weeks, your deploys happen on Thursdays, and Lisa owns the landing page — because she was in the channel when those things were discussed.

**3. Smart Task Orchestration**
When you give Lucy a complex task, she breaks it into parallel or sequential sub-tasks, delegates to specialized sub-agents, and reports back with a consolidated result. She knows whether to run things simultaneously or chain them, and she keeps you updated: "Working on this — should take about 10 minutes. I'll post the result here when it's done."

**4. Proactive Intelligence**
Lucy doesn't wait to be asked. She monitors your connected tools, detects repeated patterns in conversations, and surfaces issues unprompted:
- "Your Google Ads CPA jumped 23% this week. I've analyzed the campaigns — 'enterprise' ad group is underperforming."
- "I noticed you ask for this report every Monday. Want me to automate it?"
- "The PR from yesterday has been approved but not merged for 18 hours. Should I ping the author?"

**5. Human-in-the-Loop Approvals**
For high-stakes actions, Lucy asks before she acts. She posts an approval card with [Approve] and [Reject] buttons. You stay in control.

**6. Scheduled Workflows & Cron Jobs**
"Every Friday at 5pm, generate a weekly engineering summary from GitHub and post it to #team-updates." Lucy handles recurring tasks on autopilot with configurable schedules.

**7. Real-Time Heartbeat Monitoring**
Define conditions. Lucy watches continuously. When a threshold is crossed, she acts:
- "Alert me if API error rate exceeds 2%"
- "Notify #sales if any deal in HubSpot hasn't been updated in 7 days"

**8. Build & Deploy**
Lucy has her own cloud computer. She writes code, builds internal tools, creates dashboards, and deploys them — all from a Slack thread. Her sandbox persists state across sessions — installed packages, databases, and files carry forward.

**9. Deep Research**
Lucy browses the web with a stealth browser that bypasses anti-bot detection, reads documents, cross-references sources, and synthesizes findings into actionable intelligence.

**10. Skills That Compound**
Lucy documents what she learns in "skills" — internal reference notes. Over time, she gets better at serving your specific workflows. A new hire gets the same quality on day 1 as a veteran on day 300.

**11. Team Knowledge Base**
Lucy indexes your Slack channels, Google Docs, Notion pages, and Confluence wikis into a unified knowledge base. Ask her anything about your team's work and she retrieves the answer in seconds — with citations.

**12. Semantic Caching**
Slack teams ask the same questions repeatedly: "What's our PTO policy?", "How do I submit expenses?" Lucy caches answers semantically — similar questions get instant responses without burning API calls. Up to 10x cost reduction on repeated queries.

**13. Self-Building Integrations**
When Lucy doesn't have a pre-built connector for a tool your team uses, she builds one herself. Give her an API key and point her at the docs — she generates the integration, validates it, and registers it as a new capability.

**14. Pattern Detection**
Lucy passively observes team conversations and detects repeated workflows: who asks whom for what, which reports get requested weekly, which questions keep coming back. She offers to automate anything she sees happening more than a few times.

---

### What Makes Lucy Different from Viktor?

Everything Viktor does, Lucy does. But Lucy goes further in areas that matter for serious teams.

| Capability | Viktor | Lucy |
|---|---|---|
| Slack-native execution | Yes | Yes |
| 3,000+ integrations | Yes (browser + APIs) | Yes (native APIs first, browser fallback, self-building for gaps) |
| Persistent memory | Basic workspace context | Three-layer memory: semantic cache, vector recall, deep brain. Thread + channel + preference aware |
| Model intelligence | Single model | Intelligent model routing — cheap/fast for simple, frontier for complex. 60-80% cost savings |
| Proactive monitoring | Basic alerts | Configurable heartbeat monitors with condition evaluation and suggested fixes |
| Approval workflows | Basic approve/reject | Approve/reject/edit with denial feedback loops |
| Anti-detection browsing | Standard browser | CamoFox engine-level fingerprint spoofing (C++ level, not JS patches) |
| Task orchestration | Single agent | Smart orchestrator: parallel/sequential sub-task decomposition with status updates |
| Cost efficiency | Single model burns tokens | Smart routing + semantic caching — up to 85% cost reduction |
| Self-improving | Static skills | Skills update based on access frequency, team correction, and failure feedback |
| Self-building integrations | None | OpenAPI spec auto-discovery + MCP server generation for any API |
| Webhook automation | Limited | Full webhook ingestion — trigger workflows from any external event |
| Code sandbox | Shared compute | Per-workspace isolated Firecracker microVM with persistent state (<150ms cold start) |
| Knowledge base | None | Unified RAG across Slack, Docs, Notion, Confluence |
| Pattern detection | None | Detects repeated team workflows and offers to automate them |
| Semantic caching | None | GPTCache — 10x cost reduction on repeated queries |
| Prompt injection defense | None visible | LlamaFirewall on every inbound message |
| Cost tracking | None visible | Per-workspace, per-task, per-model cost visibility |
| Personality | Generic | Custom SOUL.md with voice examples + anti-slop output filtering |

---

## Part B: Technical Architecture & Developer Guide

### Foundation

Lucy is built on OpenClaw with a custom execution intelligence layer. The architecture follows a lean philosophy: ~15 carefully chosen dependencies instead of 60+, covering every capability users notice while avoiding infrastructure bloat.

**From OpenClaw**: Gateway orchestration, plugin system, persistent memory with engram enhancement, tool execution (browser, file system, shell, web search), cron scheduling, sub-agent session management, sandbox isolation.

**Custom-built**: Three-layer memory, intelligent model routing, Composio integration with GPT-4.1 planning, capability index for sub-millisecond routing, multi-step workflow engine with error recovery, approval system, cost tracker, knowledge base, security guardrails, pattern detector.

---

### System Architecture

```
Slack Workspaces (Multi-Tenant)
        |
        v
Slack Bolt + FastAPI App Server
        |
        |---> LlamaFirewall (prompt injection defense)
        |
        |---> Orchestrator Agent
        |       |
        |       |---> OpenClaw Sub-Agents (parallel/sequential task decomposition)
        |       |---> Composio IntegrationWorker (1,000+ native tools)
        |       |---> openapi-mcp-generator (self-building integrations)
        |       |---> E2B Sandbox (per-workspace code execution)
        |       |---> CamoFox Browser (stealth web research)
        |
        |---> Model Router
        |       |---> LiteLLM Gateway (100+ LLMs, cost tracking)
        |       |---> RouteLLM Classifier (complexity-based tier selection)
        |       |---> GPTCache (semantic response caching)
        |
        |---> Memory
        |       |---> Layer 0: GPTCache semantic cache (<5ms)
        |       |---> Layer 1: Mem0 + Qdrant vector memory (<50ms)
        |       |---> Layer 2: OpenClaw engram deep brain (async)
        |
        |---> Knowledge (RAG)
        |       |---> Unstructured (Slack, Docs, Notion ingest)
        |       |---> LlamaIndex (chunking, embedding, retrieval)
        |
        |---> TaskRegistry (lifecycle, heartbeats, approvals)
        |---> HumanLayer (Slack approval decorators)
        |---> Webhook Server (external event ingestion)
        |---> Pattern Detector (topic frequency, repeated requests)
        |---> Cost Tracker (PostgreSQL: per-workspace, per-task, per-model)
        |
        |---> OpenClaw Gateway (VPS)
                |---> Kimi K2.5 (262K context, deep reasoning)
                |---> openclaw-engram (memory plugin)
                |---> openclaw-memory janitor (cleanup cron)
                |---> Brave web search
                |---> CamoFox Browser Server
                |---> pass-vault (credentials)
```

---

### Module Map

Each architectural component maps to a module in the `src/lucy/` package:

| Architecture Component | Module Path | Key File(s) |
|---|---|---|
| Slack Bolt + FastAPI App Server | `src/lucy/` | `app.py`, `config.py` |
| LlamaFirewall | `src/lucy/security/` | `firewall.py` |
| Orchestrator Agent | `src/lucy/core/` | `orchestrator.py` |
| LucyAgent | `src/lucy/core/` | `agent.py`, `soul.py` |
| Model Router | `src/lucy/routing/` | `router.py`, `classifier.py`, `tiers.py` |
| Memory (all layers) | `src/lucy/memory/` | `cache.py`, `vector.py`, `deep.py`, `sync.py` |
| Knowledge (RAG) | `src/lucy/knowledge/` | `ingest.py`, `retrieval.py` |
| TaskRegistry + Approvals | `src/lucy/tasks/` | `registry.py`, `approvals.py`, `scheduler.py` |
| Integrations (Composio) | `src/lucy/integrations/` | `worker.py`, `composio_client.py`, `registry.py`, `toolset.py`, `capability_index.py` |
| Self-building integrations | `src/lucy/integrations/` | `self_builder.py` |
| Webhook Server | `src/lucy/monitors/` | `webhooks.py` |
| Pattern Detector | `src/lucy/monitors/` | `patterns.py` |
| Cost Tracker | `src/lucy/costs/` | `tracker.py` |
| E2B Sandbox | `src/lucy/sandbox/` | `e2b.py` |
| CamoFox Browser | `src/lucy/browser/` | `camofox.py` |
| Database (PostgreSQL) | `src/lucy/db/` | `models.py`, `session.py`, `migrations/` |
| Observability (Langfuse) | `src/lucy/observability/` | `traces.py` |
| PII Filter | `src/lucy/security/` | `pii.py` |
| Personality | `assets/` | `SOUL.md` |

---

### Dependency Stack (~15 total)

| Category | V1 Stack | Count |
|---|---|---|
| Slack | Slack Bolt (Python) | 1 |
| Agent Core | OpenClaw (gateway + sub-agents + cron + engram) | 1 (already deployed) |
| Integrations | Composio + openapi-mcp-generator | 2 |
| Memory | Mem0 + Qdrant + GPTCache | 3 |
| Knowledge | Unstructured + LlamaIndex | 2 |
| Model Routing | LiteLLM + RouteLLM | 2 |
| Sandbox | E2B | 1 |
| Browser | CamoFox | 1 |
| Security | LlamaFirewall | 1 |
| Approvals | HumanLayer | 1 |
| Database | PostgreSQL | 1 |
| Credentials | Composio OAuth + Nango (for gaps) | 1 |
| Observability | Langfuse | 1 |

---

### Memory Architecture — Three-Layer System

```
Layer 0 — Semantic Cache (<5ms)
  GPTCache: semantic similarity matching on previous queries
  Repeated/similar questions get instant answers, no LLM cost
  Handles Slack's repetitive question patterns (PTO policy, deploy process, etc.)

Layer 1 — Vector Memory (<50ms)
  Mem0 + Qdrant local vector database
  Thread context, channel context, team preferences, user roles
  Scoped: per-workspace, per-channel, per-user
  Stores: facts, preferences, decisions, team member profiles
  "Who's the CEO?" "What did we decide about pricing last week?"
  "Nicole prefers bullet points. Jake likes detailed explanations."

Layer 2 — Deep Brain (async, 5-18s)
  OpenClaw engram: hourly summaries, fact extraction, topic tracking
  Kimi K2.5 (262K context) for deep synthesis
  Accessed when complex multi-hop reasoning is needed
  openclaw-memory janitor runs daily: compress, archive, validate, deduplicate
```

**What this covers:**
- Thread awareness (knows what the current conversation is about)
- Channel awareness (knows what's been discussed in this channel)
- Preferences and patterns (knows what each user likes, who has which role)
- Team awareness (knows the org structure, who to please, who contributes most)
- Short-term recall (last few weeks of context, instantly available)
- Long-term synthesis (deeper reasoning delegated to OpenClaw async)

**Memory Scoping for Multi-Tenant Slack:**
```
Qdrant Collection: "lucy_memories"
  Scoping keys:
    "ws:T1234ABC"               -> workspace-global facts
    "ch:T1234ABC:C5678"         -> channel-specific context
    "usr:T1234ABC:U9012"        -> per-user preferences and private memory
```

---

### Intelligent Model Routing

```
Incoming message
  -> GPTCache semantic lookup -> HIT? Return cached (0ms LLM cost)
  -> MISS -> Lightweight classifier (GPT-4.1-mini, ~50 tokens, <200ms)
  -> Outputs: {complexity: 0-10, category: "lookup|tool_use|reasoning|code|chat"}
  -> Tier mapping:
      complexity 0-2  -> Tier 0 (no LLM) or Tier 1 (fast model)
      complexity 3-5  -> Tier 2 (standard model)
      complexity 6-8  -> Tier 3 (frontier model)
      complexity 9-10 -> Tier 3 + sub-agent delegation

Model Tiers:
  Tier 1 (Fast):     GPT-4.1-mini, Gemini Flash     | <500ms  | $0.20/M tokens
  Tier 2 (Standard): GPT-4.1, Kimi K2.5             | 1-3s    | $2/M tokens
  Tier 3 (Frontier): Claude Opus, GPT-4.5           | 3-8s    | $15/M tokens
  Tier 0 (No LLM):  Cached responses, DB lookups    | <5ms    | $0

Feedback loop (PostgreSQL):
  (task_hash, model_tier, success, latency_ms, tokens, cost, timestamp)
  On failure at Tier 1: log escalation signal -> future similar tasks auto-escalate
  On repeated success at Tier 1: reinforce cheap routing
```

Expected impact: 60-80% cost reduction vs. single-model competitors.

---

### Task Orchestration

Lucy's orchestrator decides how to execute every request:

```
User request arrives
        |
        v
Intent Classification
  "What kind of task is this?"
        |
        v
Decomposition Decision
  Can this be broken into parallel sub-tasks?
  Or does it require sequential steps (output of step 3 feeds step 4)?
        |
        +--> Simple task -> Execute directly, respond inline
        |
        +--> Parallel tasks -> Spawn OpenClaw sub-agents simultaneously
        |    -> Webhook/heartbeat to track completion
        |    -> Consolidate results, respond when all done
        |
        +--> Sequential workflow -> Chain sub-agents in order
        |    -> Each step feeds the next
        |    -> Status updates in Slack thread ("Step 2 of 5 complete...")
        |
        +--> Long-running task (>30s estimated)
             -> "Working on this -- should take about 10 minutes."
             -> Execute in background
             -> Post result when done

Sub-agent spawning via OpenClaw native sessions_spawn:
  - Each sub-agent gets its own isolated session
  - Can use different model tiers (cheap for simple sub-tasks)
  - Results announced back to orchestrator
  - Full context preserved across the chain
```

---

### Multi-Tenant Architecture

```
PostgreSQL (primary data store):
  workspaces      (id, slack_team_id, plan, settings, created_at)
  users           (id, workspace_id, slack_user_id, role, created_at)
  channels        (id, workspace_id, slack_channel_id, memory_scope)
  tasks           (id, workspace_id, status, agent, model_tier, tokens, cost_usd, ts)
  approvals       (id, task_id, approver_id, action, timestamp)
  schedules       (id, workspace_id, cron_expr, workflow_def, last_run)
  heartbeats      (id, workspace_id, condition, check_interval, last_checked, status)
  integrations    (id, workspace_id, provider, status, encrypted_token, scopes, last_used)
  patterns        (id, workspace_id, channel_id, topic, frequency, last_seen, suggested)
  cost_log        (id, workspace_id, task_id, model, input_tokens, output_tokens, cost_usd, ts)
  audit_log       (id, workspace_id, actor, action, tool, params, result, cost_usd, ts)

Isolation guarantees:
  - Every query is scoped by workspace_id
  - Memory collections are namespace-scoped in Qdrant
  - Sandboxes are per-workspace isolated VMs
  - Credentials stored encrypted per-workspace
  - No cross-workspace data access, period
```

---

### Approval Flow (Slack Block Kit)

```
Lucy posts to thread:
+---------------------------------------------+
| I've prepared a Linear issue:                |
|                                              |
| Title: Update landing page pricing details   |
| Team: Growth                                 |
| Labels: enhancement, pricing                 |
|                                              |
| [Approve]  [Reject]  [Edit]                 |
+---------------------------------------------+

On Approve:
  -> HumanLayer callback -> TaskRegistry PENDING_APPROVAL -> RUNNING
  -> IntegrationWorker executes LINEAR_CREATE_ISSUE
  -> Lucy updates message: "Created: LIN-1234"

On Reject:
  -> Denial reason fed back to LLM
  -> Lucy adjusts approach for similar future tasks
```

---

### Proactive Monitor Agent

Lucy doesn't just respond — she initiates:

```
Monitor Agent (background daemon):
  - Watches webhook events (GitHub, Stripe, Datadog, etc.)
  - Evaluates heartbeat conditions on schedule
  - Detects anomalies in connected metrics
  - When trigger fires -> posts to channel with context

Example:
  1. Datadog webhook: API error rate at 3.2%
  2. Monitor evaluates: exceeds 2% threshold configured for #ops
  3. Posts to #ops: "API error rate hit 3.2%. 80% are timeout failures
     from /checkout. Stripe webhooks averaging 4.2s. Want me to investigate?"
  4. Team replies: "Create an incident"
  5. Lucy creates Linear incident, checks deploy logs, posts findings
```

---

### Pattern Detection (Simple, Effective)

No academic frameworks. A straightforward PostgreSQL-backed pattern detector:

```
Every substantive message Lucy observes:
  1. Extract topic via cheap LLM call (GPT-4.1-mini, ~20 tokens)
  2. Upsert into patterns table: (workspace_id, channel_id, topic, frequency++)
  3. Track who asks whom for what: (requester, target, request_type, count)

Trigger thresholds:
  - Same topic requested 3+ times in 2 weeks -> suggest automation
  - Same person asked for same thing 3+ times -> suggest automation
  - User rejected suggestion last time -> don't suggest again for 30 days

When threshold hit, Lucy posts in thread:
  "I noticed this Google Ads report gets requested every Monday.
   Want me to automate it? I can pull the data and post it here
   every Monday at 9am."

  [Yes, automate it]  [Customize first]  [No thanks]

On approval -> register as cron job in schedules table.

Privacy rules:
  - Lucy never stores raw message content, only extracted topics
  - Sensitive channels (#hr, #legal) excluded via admin settings
  - /lucy patterns shows detected patterns; users can delete any
```

---

### Self-Building Integrations

When Composio doesn't have a pre-built connector:

```
User: "Connect to Polar.sh"
        |
        v
Step 1: Check Composio registry
        -> Found? Use native integration.
        -> Not found? Continue.
        |
        v
Step 2: Search for OpenAPI/Swagger spec
        -> Check {service}.com/openapi.json, /api-docs, /swagger.json
        -> Check docs.{service}.com/llms.txt
        -> Found spec? Continue.
        |
        v
Step 3: Auto-generate MCP server
        -> openapi-mcp-generator --input spec.json --output ./polar-mcp
        -> Validate against live API
        -> Register in Lucy's capability index
        |
        v
Step 4: No spec found? OpenClaw skill approach
        -> Agent writes a SKILL.md with endpoint docs
        -> LLM calls API directly using skill instructions
        -> No code generation needed
```

For simple cases, Composio's `@action` decorator wraps any API in ~10 lines of Python.

---

### Credential Management

```
User clicks "Connect Polar.sh" in Slack
        |
        v
Composio handles OAuth (for supported tools)
  OR
Nango handles OAuth (for gaps Composio doesn't cover)
  OR
Secure Slack modal for API keys (paste token, validated before storage)
        |
        v
Encrypted storage in PostgreSQL (AES-256, per-workspace scoped)
        |
        v
Integration registry tracks: provider, status, scopes, last_used, health

Token lifecycle:
  - Validated against live API before storage
  - Auto-refresh via Nango for OAuth tokens
  - Daily health check cron: lightweight API call per connection
  - /lucy disconnect <service> revokes and deletes
  - Audit log entry for every credential access
```

---

### Security

Two focused tools instead of six:

**LlamaFirewall** (Meta's PurpleLlama): Runs on every inbound Slack message. Blocks prompt injection, jailbreaks, and harmful outputs. Slack messages are untrusted input — this is non-negotiable.

**Lightweight PII filter**: Regex-based scanner for common PII patterns (emails, phone numbers, SSNs, credit card numbers) before processing and before responding. Catches 80% of cases with zero infrastructure overhead. Full Presidio deployment deferred to V2.

---

### Cost Tracking

Built into the architecture from day one:

```
Every LLM call logged to cost_log table:
  (workspace_id, task_id, model, input_tokens, output_tokens, cost_usd, timestamp)

Every tool execution logged:
  (workspace_id, tool, provider, cost_usd, timestamp)

Aggregation queries:
  - Total cost per workspace this month
  - Cost breakdown by model tier
  - Cost breakdown by tool/integration
  - Average cost per task
  - Most expensive tasks (candidates for optimization)
  - Per-user usage breakdown

Visible via:
  /lucy costs           -> this month's spending summary
  /lucy costs breakdown  -> per-model, per-tool breakdown
  /lucy costs user @jake -> Jake's usage this month
```

---

### Observability

**Langfuse** for trace visibility and debugging:
- Every agent action traced: what was requested, which model was used, what tools were called, what was returned, how long it took, what it cost
- Self-hostable, no vendor lock-in
- Integrated with LiteLLM gateway for automatic trace capture

Custom cost tracker (described above) handles the business analytics: margins, spending, per-workspace costs.

---

### Personality — SOUL.md + Anti-Slop

**Lucy's SOUL.md** follows the Anchor-Trait-Voice format with few-shot examples:

```markdown
# Lucy's Soul

## Anchor
Lucy is the teammate who actually gets things done. Sharp, reliable,
and genuinely helpful without being annoying about it.

## Traits
- Direct because she respects people's time
- Warm without being sycophantic
- Occasionally witty, never forced
- Admits uncertainty rather than bullshitting
- Pushes back when something doesn't make sense
- Celebrates wins genuinely, not performatively

## Voice Examples

Helping with a task:
> "Done -- merged the PR and updated the Linear ticket.
> The CI run is green. Jake's been notified."

Pushing back:
> "I can do that, but heads up -- last time we changed the
> pricing page mid-campaign, CPA spiked for 3 days. Want
> me to wait until the current campaign cycle ends?"

Spotting a problem:
> "Something's off with checkout -- error rate jumped to 4.2%
> in the last 30 minutes. Looks like the Stripe webhook is
> timing out. I've pulled the logs. Want me to dig deeper?"

Being honest:
> "I'm not confident about this one. The data I have is from
> last quarter. Let me pull fresh numbers before you make
> a decision."
```

**Anti-slop output filter**: Before every message Lucy posts to Slack, check against a blocklist of 8,000+ overused AI phrases ("I'd be happy to help", "It's worth noting", "Let me delve into", "In today's fast-paced world"). If detected, regenerate. Zero latency cost — runs locally as a regex check.

---

### Design Influences

Lucy's architecture draws on patterns validated in prior agent development work, particularly around Composio integration, OpenClaw gateway orchestration, and vector memory scoping. Every module below is implemented from scratch for this codebase — no code is carried over. The designs are informed by lessons learned, not by copy-paste.

### Core Modules to Build

| Module | Location | Architectural Notes |
|---|---|---|
| **IntegrationWorker** (multi-step Composio execution) | `src/lucy/integrations/worker.py` | Async multi-step tool execution with retry and rollback |
| **ComposioClient** (async SDK wrapper) | `src/lucy/integrations/composio_client.py` | Thin async wrapper; handles auth refresh and rate limiting |
| **IntegrationRegistry** (TTL connection cache) | `src/lucy/integrations/registry.py` | Per-workspace connection pool with TTL eviction |
| **ComposioToolset** (dynamic schema building) | `src/lucy/integrations/toolset.py` | Builds tool schemas at runtime from Composio's registry |
| **CapabilityIndex** (sub-ms tool routing) | `src/lucy/integrations/capability_index.py` | In-memory index mapping intents to tools in <1ms |
| **TaskRegistry** (task lifecycle + approvals) | `src/lucy/tasks/registry.py` | State machine with PENDING_APPROVAL state for human-in-the-loop |
| **LucyAgent** (core agent class) | `src/lucy/core/agent.py` | Wraps OpenClaw gateway; manages session lifecycle and personality |
| **Memory** (Mem0 + Qdrant vector store) | `src/lucy/memory/vector.py` | English embeddings, workspace-scoped Qdrant namespaces |
| **MemorySync** (bidirectional sync) | `src/lucy/memory/sync.py` | Write-through to vector store on every interaction |
| **Background Poller** (proactive monitoring) | `src/lucy/monitors/heartbeat.py` | Evaluates configured conditions on schedule, posts to Slack |

### New Capabilities to Build

| Component | Tech | Priority |
|---|---|---|
| **Slack Bolt app entry point** | slack-bolt (Python) | P0 |
| **Slack Block Kit composer** | Slack Block Kit | P0 |
| **Multi-tenant PostgreSQL schema** | PostgreSQL + SQLAlchemy | P0 |
| **SOUL.md + anti-slop filter** | Custom | P0 |
| **Model Router** | LiteLLM + RouteLLM + GPTCache | P0 |
| **Cost tracker** | PostgreSQL (custom) | P0 |
| **Webhook ingestion server** | FastAPI | P1 |
| **Approval system** | HumanLayer + Block Kit | P1 |
| **LlamaFirewall integration** | PurpleLlama | P1 |
| **RAG pipeline** | Unstructured + LlamaIndex | P1 |
| **E2B sandbox manager** | E2B SDK | P1 |
| **CamoFox browser integration** | CamoFox | P1 |
| **Sub-agent spawning** | OpenClaw sessions_spawn | P1 |
| **Pattern detector** | Custom (PostgreSQL) | P1 |
| **Self-building integrations** | openapi-mcp-generator | P1 |
| **Nango OAuth for gaps** | Nango | P1 |
| **Proactive Monitor Agent** | Custom daemon | P1 |
| **Langfuse integration** | Langfuse | P2 |
| **Onboarding flow** | Slack interactive setup | P2 |
| **Skill store** | Custom (semantic retrieval) | P2 |
| **Lightweight PII filter** | Regex-based | P2 |

---

### Pricing Model

| Plan | Price | What's Included |
|---|---|---|
| **Starter** | $49/mo per workspace | 5 users, 500 actions/mo, 3 integrations, basic memory, fast models only |
| **Pro** | $149/mo per workspace | 25 users, 5,000 actions/mo, unlimited integrations, full memory, all model tiers, code sandbox, pattern detection, scheduled workflows |
| **Enterprise** | Custom | Unlimited users, unlimited actions, SSO/SAML, RBAC, audit trail, dedicated sandbox, SLA, on-prem option |

**Unit economics**: With intelligent model routing and semantic caching, Lucy's average cost per action is estimated at $0.003-$0.02 (vs. $0.05-$0.15 for single-model competitors). Gross margins above 70% at scale.

---

### Why This Wins

**Against Viktor**: Cheaper (model routing + semantic caching = 60-85% savings), faster (three-tier memory with <5ms cache hits), self-building integrations (Viktor can't connect to tools it doesn't know), pattern detection (Viktor doesn't proactively suggest automations), and transparent cost tracking.

**Against Slack's native AI**: Slackbot summarizes and searches. It doesn't execute. Lucy does the work.

**Against Zapier/Make**: Rigid trigger-action pipelines that break on edge cases. Lucy understands context, recovers from errors, handles nuance, and chains multi-step workflows through natural language.

**Against hiring another person**: $49-149/month. Works 24/7. Never forgets. Gets faster over time. Handles the work nobody wants to do. Onboards instantly.

**Against vanilla OpenClaw**: OpenClaw is a powerful foundation, but out of the box it lacks: intelligent model routing, semantic caching, multi-tenant isolation, Slack-native UX patterns, cost tracking, pattern detection, self-building integrations, and the personality layer that makes Lucy feel like she truly knows your team.

---

### Competitive Moat

1. **Memory compounds**: The longer Lucy serves a team, the more she knows. Switching costs increase over time.
2. **Skills compound**: Lucy learns team-specific workflows. A new Slack agent would start from zero.
3. **Cost advantage**: Model routing and semantic caching make Lucy structurally cheaper to operate than single-model competitors.
4. **Open-source foundation**: Built on OpenClaw (215K+ stars). Community contributions continuously improve the base layer for free.
5. **Self-building integrations**: Lucy can connect to any tool with an API, not just pre-built connectors. The long tail of SaaS is Lucy's territory.

---

## 90-Day Roadmap

### Days 1-30: Ship Lucy V1 That Works

- `src/lucy/app.py` — Slack Bolt + FastAPI entry point with `/health` endpoint
- `src/lucy/db/models.py` — Multi-tenant PostgreSQL schema (workspaces, users, channels, tasks, cost_log)
- `src/lucy/config.py` — Pydantic Settings loading all `LUCY_*` env vars
- `src/lucy/core/agent.py` — LucyAgent class wrapping OpenClaw gateway
- `src/lucy/core/soul.py` — SOUL.md personality loader + anti-slop filter
- `src/lucy/core/orchestrator.py` — Intent classification, task decomposition (parallel vs sequential), status updates
- `src/lucy/memory/vector.py` — Mem0 + Qdrant memory (thread context, channel context, preferences, team awareness)
- `src/lucy/memory/cache.py` — GPTCache semantic cache
- `src/lucy/routing/` — LiteLLM gateway + RouteLLM (3-tier model routing)
- `src/lucy/integrations/worker.py` — Composio IntegrationWorker for tool execution
- `src/lucy/security/firewall.py` — LlamaFirewall on all inbound messages
- `src/lucy/costs/tracker.py` — Per-workspace, per-task, per-model cost tracking
- `src/lucy/tasks/scheduler.py` — OpenClaw cron for scheduled tasks
- `src/lucy/monitors/webhooks.py` — Basic webhook ingestion (FastAPI endpoint)
- `src/lucy/slack/blocks.py` — Block Kit composers for approvals, status updates, connect buttons
- Dogfood with own team

### Days 31-60: Make Lucy Smart and Proactive

- `src/lucy/knowledge/` — RAG pipeline: Unstructured ETL + LlamaIndex retrieval
- `src/lucy/sandbox/e2b.py` — E2B sandbox (code execution per workspace)
- `src/lucy/browser/camofox.py` — CamoFox stealth browser for research tasks
- OpenClaw sub-agent spawning for parallel task execution (via `src/lucy/core/agent.py`)
- `src/lucy/tasks/approvals.py` — HumanLayer approval flows
- `src/lucy/monitors/patterns.py` — Simple pattern detector (topic frequency + repeated request tracker)
- `src/lucy/integrations/self_builder.py` — Self-building integrations (openapi-mcp-generator for Composio gaps)
- Nango for OAuth gaps (integrated into `src/lucy/integrations/registry.py`)
- `src/lucy/memory/deep.py` — OpenClaw engram deep brain sync
- openclaw-memory janitor (cleanup cron via OpenClaw)

### Days 61-90: Polish and Launch

- Pattern detection suggestions in channels ("Want me to automate this?") via `src/lucy/monitors/patterns.py`
- `src/lucy/monitors/heartbeat.py` — Proactive Monitor Agent (heartbeats + webhook-triggered alerts)
- Onboarding flow for new workspaces (Slack interactive setup)
- Cost analytics via `/lucy costs` slash command
- Skill store (semantic retrieval of learned workflows)
- `src/lucy/observability/traces.py` — Langfuse integration for trace visibility
- `src/lucy/security/pii.py` — Lightweight PII filter
- Beta launch with 10 design partner teams

---

## V2 Backlog (Month 4-6)

Features explicitly deferred from V1 to keep the launch lean. Architecture is designed to support all of these without refactoring.

### Personal AI Assistants (Tier 2 Agents)
Each employee creates their own personal AI assistant with a custom name (Zed, Aria, etc.). Lives in DMs only, connects to personal tools (Gmail, WhatsApp, Telegram), has private memory that never leaks to the team. Charged per-seat as an add-on ($19/user/month). Architecture designed in V1 (DB schema supports `agent_instances` with `tier = 'personal'`, Qdrant namespace isolation), built in V2.

### Knowledge Graph Memory (Cognee)
Graph-based memory layer connecting people, projects, and decisions across channels. Entity extraction, relationship reasoning, temporal queries ("when did we decide X?"). Adds a graph database dependency (Neo4j) — deferred to keep V1 infrastructure simple.

### Enterprise Security Stack
- HashiCorp Vault for credential management (replaces encrypted PostgreSQL columns)
- RBAC with Cedar policies (Leash) for workspace-level permission control
- Full Presidio NER pipeline for enterprise-grade PII detection
- SOC 2 Type II compliance documentation
- Eunomia agent-aware authorization

### Advanced Observability
- DeepEval quality gates in CI (Task Completion, Tool Correctness scoring)
- Helicone Slack usage reports (automated weekly digest to #lucy-admin)
- AgentOps session replays for debugging

### Personality Evolution
- Soulscript-based evolving personality (Core Traits + Memories = Personality)
- PersonaMem-v2 per-user tone adaptation (learns Nicole prefers bullet points)
- Context-adaptive personality (PersonaFuse-inspired: serious in #incidents, casual in #random)

### Advanced Pattern Detection
- TSpan temporal pattern mining (distinguishes bursts from routines)
- AFlow workflow synthesis (MCTS-based automatic workflow generation)
- ContextAgent proactive prediction model (trained on team interaction data)
- Galaxy Framework Cognition Forest for growing pattern memory

### Durable Workflow Engine
- Temporal for multi-day workflows that survive crashes and wait for approvals
- Complex conditional workflow chains with branching logic
- Natural language workflow builder ("when X happens, do Y, then Z if approved")

### Cross-Channel Notifications (Novu)
For personal agents: route notifications to WhatsApp, Telegram, email based on per-user preferences and urgency.

### Admin Dashboard
React/Next.js web interface for workspace admins: user management, integration health, cost analytics, pattern library, agent configuration.

### Billing Integration
Stripe per-workspace subscription + per-seat personal agent add-on + metered token overages.

---

*Lucy isn't just another AI chatbot in Slack. She's the teammate who remembers everything, executes instantly, detects what your team needs before you ask, and gets better every day. Built lean — 15 dependencies, not 60 — with every capability users actually notice.*

*She's the coworker every team deserves.*