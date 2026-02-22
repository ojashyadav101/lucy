# Production Hardening Roadmap

## Goal

Bring Lucy to production-ready reliability for multi-integration tool-calling under high scale.

## Phase 0 (Done In This Pass)

- Added loop detection and context-window bounds in `src/lucy/core/agent.py`.
- Added malformed argument surfacing in:
  - `src/lucy/routing/router.py`
  - `src/lucy/core/openclaw.py`
- Added unknown-tool validation and structured tool error typing.
- Added non-empty finalization fallback path for tool loops.
- Added Composio compatibility/retry/cache improvements in:
  - `src/lucy/integrations/composio_client.py`
  - `src/lucy/integrations/registry.py`
- Added markdown-to-Slack normalization in `src/lucy/slack/handlers.py`.

## Phase 1 (Next 3-5 days)

### Reliability controls

1. Add per-tool timeout budgets and retry policy by error type.
2. Add circuit breaker around Composio API paths.
3. Add deterministic "connection required" user-facing responses when auth is missing.
4. Add validation gate for "completeness claims" before responding with "that is all".

### Tests

1. Unit tests:
   - repeated tool-call break
   - unknown tool rejection
   - parse_error propagation
   - non-empty fallback generation
2. Integration tests:
   - missing-connection behavior
   - no-text loop behavior

## Phase 2 (1-2 weeks)

### Scale architecture

1. Capability index for tools (provider, scope, intent tags, usage statistics).
2. Top-K retrieval before LLM planning instead of broad schema pass.
3. Staged planning:
   - stage 1: small candidate set
   - stage 2: expand only if needed
4. Auth-aware retrieval filter by workspace/user connection state.

### Performance work

1. Cache tool schemas and connected toolkits in shared cache.
2. Add cache invalidation hooks on auth/sync events.
3. Add background schema refresh jobs.

## Phase 3 (2-4 weeks)

### Observability and operations

1. Metrics:
   - `tool_loop_rate`
   - `unknown_tool_rate`
   - `no_text_fallback_rate`
   - `tool_execution_error_rate`
   - `tool_p95_latency_ms`
2. Alerts:
   - fallback rate spike
   - tool error spike by provider
   - degraded cache hit ratio
3. Canary rollout and rollback:
   - 5% -> 25% -> 50% -> 100%
   - rollback on SLO breach.

## SLO Targets

1. Tool-call success rate: >= 99%
2. No-text fallback rate: <= 0.5%
3. Unknown tool-call rate: <= 0.1%
4. Tool-intent p95 latency: <= 8s
5. Tool retrieval p95 latency: <= 500ms
6. Cache hit ratio (tool metadata): >= 80%

## Release Gate Checklist

1. All Phase 1 tests passing.
2. Thread auto-response regression pass.
3. Schedule completeness regression pass.
4. Provider auth failure paths produce actionable user responses.
5. Canary metrics stable for 24 hours.

