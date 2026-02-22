# Lucy Database Schema — Architecture Overview

## Design Philosophy

This schema is designed for **production-scale multi-tenant AI agents** with these principles:

1. **Workspace-isolated**: Every table has `workspace_id` for tenant separation
2. **Time-series ready**: High-volume tables partitioned by month
3. **JSONB flexibility**: Evolve schemas without migrations
4. **Immutable audit trail**: Soft deletes + audit log = complete history
5. **Performance optimized**: Partial indexes for hot paths (active tasks, pending approvals)

---

## Entity Relationship Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              TENANT LAYER                                        │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ┌──────────────┐         ┌──────────────┐         ┌──────────────┐           │
│  │  workspaces  │◄────────│    users     │◄────────│   channels   │           │
│  │  (tenants)   │  1:N    │  (members)   │  1:N    │  (context)   │           │
│  └──────────────┘         └──────────────┘         └──────────────┘           │
│          │                       │                         │                     │
│          │                       │                         │                     │
│          ▼                       ▼                         ▼                     │
│  ┌──────────────┐         ┌──────────────┐         ┌──────────────┐           │
│  │    agents    │         │   patterns   │         │integrations  │           │
│  │ (instances)  │         │(auto-detect) │         │ (Linear/GH)  │           │
│  └──────────────┘         └──────────────┘         └──────────────┘           │
│                                                                                  │
├─────────────────────────────────────────────────────────────────────────────────┤
│                           ORCHESTRATION LAYER                                    │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │                            tasks                                         │    │
│  │  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐                │    │
│  │  │ created │───►│queued   │───►│ running │───►│completed│                │    │
│  │  └────┬────┘    └────┬────┘    └───┬─────┘    └─────────┘                │    │
│  │       │              │              │                                     │    │
│  │       ▼              ▼              ▼                                     │    │
│  │  ┌─────────┐    ┌─────────┐    ┌─────────┐                              │    │
│  │  │cancelled│    │ timeout │    │ failed  │                              │    │
│  │  └─────────┘    └─────────┘    └─────────┘                              │    │
│  │                                                                         │    │
│  │  ┌───────────────┐       ┌───────────────┐                              │    │
│  │  │  task_steps   │ 1:N   │  approval    │                              │    │
│  │  │ (granular log)│◄──────│(human loop)  │                              │    │
│  │  └───────────────┘       └───────────────┘                              │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                  │
├─────────────────────────────────────────────────────────────────────────────────┤
│                           SCHEDULING LAYER                                       │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ┌──────────────┐              ┌──────────────┐                               │
│  │  schedules   │─────────────►│ heartbeats   │                               │
│  │ (cron jobs)  │              │(monitoring)  │                               │
│  └──────────────┘              └──────────────┘                               │
│                                                                                  │
├─────────────────────────────────────────────────────────────────────────────────┤
│                       TIME-SERIES TABLES (Partitioned)                           │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────────────┐              │
│  │   cost_log   │    │  audit_log   │    │webhook_deliveries │              │
│  │  (billing)   │    │  (compliance)│    │  (reliability)    │              │
│  │ ─────────────│    │ ─────────────│    │ ───────────────── │              │
│  │ • Partition  │    │ • Partition  │    │ • Partition       │              │
│  │   by month   │    │   by month   │    │   by month        │              │
│  │ • Immutable  │    │ • Immutable  │    │ • Retry tracking  │              │
│  └──────────────┘    └──────────────┘    └───────────────────┘              │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Core Tables

### `workspaces` — Tenant Root
Multi-tenant boundary. All data belongs to a workspace (Slack team).

| Column | Purpose |
|--------|---------|
| `slack_team_id` | Slack team ID (T1234567890) — unique identifier |
| `plan` | starter/pro/enterprise for feature gating |
| `settings` | JSONB feature toggles, rate limits, custom config |
| `max_*` columns | Enforced quotas (users, actions, integrations) |
| `current_month_*` | Rolling counters updated by background job |

**Indexes:**
- `slack_team_id` UNIQUE — fast lookup during Slack events
- `status, created_at` — for listing active workspaces

---

### `users` — Workspace Members
Humans (and bots) who interact with Lucy.

| Column | Purpose |
|--------|---------|
| `slack_user_id` | Slack user ID (U1234567890) |
| `role` | owner/admin/member/guest for permissions |
| `preferences` | JSONB: timezone, notification prefs, model tier preference |
| `personal_agent_config` | V2 feature: custom agent settings per user |

**Unique Constraint:** `workspace_id + slack_user_id` — one user record per Slack user per workspace.

---

### `channels` — Context Scope
Slack channels where Lucy operates.

| Column | Purpose |
|--------|---------|
| `memory_scope_key` | Qdrant namespace for vector memory isolation |
| `settings` | Channel-specific overrides (require_approval, model_tier) |
| `is_monitored` | Whether to scan history for patterns |

---

### `agents` — Agent Instances
Main Lucy + future personal agents (V2).

| Column | Purpose |
|--------|---------|
| `agent_type` | main/personal/custom |
| `owner_user_id` | For personal agents, who owns it |
| `config` | JSONB: model preferences, tool allowlist, personality |

---

## Orchestration Tables

### `tasks` — The Core Work Unit
Everything Lucy does is a task with full lifecycle tracking.

| Column | Purpose |
|--------|---------|
| `status` | Enum: created → pending_approval → running → completed/failed/timeout |
| `intent` | Classified: lookup/tool_use/reasoning/code/chat |
| `priority` | 0=critical, 4=batch — affects queue order |
| `config` | JSONB: tool allowlist, approval requirements, timeouts |
| `slack_thread_ts` | For Slack conversation threading |

**Critical Indexes:**
```sql
-- Partial index: only active tasks (hot path for task queue)
CREATE INDEX ix_tasks_active ON tasks (workspace_id, status)
WHERE status IN ('created', 'pending_approval', 'running');

-- For workspace dashboard
CREATE INDEX ix_tasks_workspace_created ON tasks (workspace_id, created_at);
```

---

### `task_steps` — Granular Tracking
Individual steps within a complex task.

**Use Case:** A "Generate weekly report" task has steps:
1. `llm_call` — understand request
2. `tool_use` — query database
3. `tool_use` — call Slack API
4. `llm_call` — summarize results
5. `approval_wait` — wait for review (optional)

---

### `approvals` — Human-in-the-Loop
Approval requests with Block Kit tracking.

| Column | Purpose |
|--------|---------|
| `action_type` | tool_execution/code_deployment/message_send/data_export |
| `risk_level` | low/medium/high/critical — affects UI prominence |
| `slack_message_ts` | For updating Block Kit message on response |
| `expires_at` | Auto-reject after timeout |

**Hot Path Index:**
```sql
-- For "my pending approvals" Slack home tab
CREATE INDEX ix_approvals_pending ON approvals (approver_id)
WHERE status = 'pending';
```

---

## Scheduling & Monitoring

### `schedules` — Cron Jobs
Recurring workflows created from patterns or manually.

| Column | Purpose |
|--------|---------|
| `cron_expression` | Standard cron: "0 9 * * MON" |
| `intent_template` | Natural language: "Generate weekly sales report" |
| `next_run_at` | Computed by cron parser, indexed for polling |

---

### `heartbeats` — Proactive Monitoring
Conditions that trigger alerts when met.

| Column | Purpose |
|--------|---------|
| `condition_type` | metric_threshold/api_health/schedule_miss/custom |
| `condition_config` | JSONB: `{metric: 'error_rate', operator: '>', threshold: 2.0}` |
| `alert_cooldown_seconds` | Prevent spam (default 1 hour) |

---

## Time-Series Tables (Partitioned by Month)

These tables grow fast. Partitioning keeps queries fast and enables easy archival.

### `cost_log` — Billing & Usage
Every LLM call, API request, and tool execution with cost.

```sql
-- Partitioned by year_month: cost_log_2025_02, cost_log_2025_03, etc.
-- Monthly rollup: aggregate costs for billing
-- Model analytics: which models are expensive?
```

### `audit_log` — Compliance
Immutable record of all changes.

```sql
-- Never delete, never update
-- before_state + after_state = full change tracking
-- Partitioned for compliance retention policies
```

### `webhook_deliveries` — Reliability
Incoming webhooks with retry tracking.

```sql
-- status = pending → processing → completed/failed/ignored
-- retry logic: attempt_count, next_attempt_at
```

---

## Flexibility Features

### JSONB Columns for Schema Evolution

| Table | JSONB Column | Use Case |
|-------|--------------|----------|
| workspaces | `settings` | Feature toggles, rate limits |
| users | `preferences` | Timezone, notifications, model tier |
| channels | `settings` | Channel-specific overrides |
| tasks | `config` | Task-specific settings |
| tasks | `result_data` | Structured results (flexible per task type) |
| agents | `config` | Personal agent customization |
| integrations | `provider_config` | Provider-specific settings |
| heartbeats | `condition_config` | Arbitrary condition definitions |

**Benefit:** Add new features without migrations. Query with PostgreSQL JSON operators:
```sql
SELECT * FROM workspaces
WHERE settings->>'enable_advanced_approval' = 'true';
```

---

## Performance Characteristics

### Query Hot Paths

| Use Case | Query Pattern | Index |
|----------|---------------|-------|
| Task queue | `status IN ('created', 'pending_approval', 'running')` | `ix_tasks_active` (partial) |
| User dashboard | `workspace_id = X ORDER BY created_at DESC` | `ix_tasks_workspace_created` |
| Pending approvals | `approver_id = X AND status = 'pending'` | `ix_approvals_pending` (partial) |
| Cron polling | `next_run_at < NOW()` | `ix_schedules_next_run` |
| Workspace billing | `workspace_id = X AND year_month = '2025-02'` | `ix_cost_log_workspace_month` |

### Expected Scale

| Table | 100 users | 1,000 users | 10,000 users |
|-------|-----------|-------------|--------------|
| workspaces | 1 | 10 | 100 |
| users | 500 | 5,000 | 50,000 |
| tasks/day | 1,000 | 10,000 | 100,000 |
| cost_log/month | 100K rows | 1M rows | 10M rows |
| audit_log/month | 500K rows | 5M rows | 50M rows |

**With partitioning:** Cost and audit logs stay fast (each partition ~10M rows max).

---

## Security Features

1. **Encrypted credentials** — `integration_credentials.encrypted_value` (use pgsodium extension)
2. **Soft deletes** — All tables have `deleted_at` for audit trail
3. **Workspace isolation** — All queries filter by `workspace_id`
4. **Immutable audit log** — Never update, never delete

---

## Migration Strategy

```bash
# Development
alembic revision --autogenerate -m "add_new_feature"
alembic upgrade head

# Production (zero-downtime)
1. Add new column (nullable, default)
2. Backfill data in batches
3. Make column non-nullable
4. Deploy code using new column
5. Drop old column in next migration
```

---

## Future Extensions

| Feature | Schema Addition |
|-----------|-----------------|
| Knowledge Graph | `knowledge_nodes` + `knowledge_edges` tables |
| A/B Testing | `experiments` table + `task.experiment_id` FK |
| Billing Subscriptions | `subscriptions` + `invoices` tables |
| Custom Integrations | `custom_tools` + `tool_schemas` tables |
| Agent Teams | `agent_teams` + `team_memberships` tables |

All extensions use the same pattern: workspace-scoped, JSONB for flexibility, proper indexes for performance.
