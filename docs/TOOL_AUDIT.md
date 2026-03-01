# Lucy Tool Audit — Sprint 2

> Generated 2026-03-01 | Branch: sprint2/w4-core-skills

## Executive Summary

Lucy has **12 tool modules** providing **~51 internal tools** (lucy_* prefix) plus
dynamic custom wrapper tools (lucy_custom_* prefix). Overall architecture is solid
but several areas need hardening: error handling in custom wrappers is too generic,
tool-selection guidance in SYSTEM_CORE.md is implicit rather than explicit, and
there is no health-check mechanism to verify tool readiness at startup.

---

## Tool Inventory

### 1. code_executor.py (834 lines)
**Tools:** `lucy_execute_python`, `lucy_execute_bash`, `lucy_run_script`
**Purpose:** Sandboxed code execution with pre-validation, auto-fix, and auto-install.
**Health:** ✅ Excellent
**Strengths:**
- Full validation pipeline: syntax check → auto-fix → execute → auto-install → retry
- Dangerous code detection (rm -rf, fork bombs, system file access)
- Auto-install on ModuleNotFoundError (up to 3 packages per execution)
- Auto-retry with error analysis (up to 2 retries)
- Activity logging to workspace
- Code templates for common patterns

**Issues:**
- `_check_dangerous_code` uses simple string matching — `code_lower` comparison
  can be bypassed with case variations or obfuscation. Low severity since this
  runs in a sandbox anyway.
- `_MAX_RESULT_CHARS = 4000` may truncate important output for data-heavy tasks.
  Consider increasing for bulk data operations.

---

### 2. browser.py (398 lines)
**Tools:** `lucy_browse_url`, `lucy_browser_snapshot`, `lucy_browser_interact`, `lucy_browser_close`
**Purpose:** Stealth web browsing via CamoFox (anti-detection Firefox).
**Health:** ✅ Good
**Strengths:**
- Anti-detection via CamoFox (C++-level anti-bot bypass)
- @search shorthand for Google searches
- Persistent per-workspace tabs with cleanup
- Accessibility-tree-based content extraction (not raw HTML)
- Graceful fallback when CamoFox is unavailable

**Issues:**
- `_active_tabs` is module-level dict — not safe for concurrent workers.
  Should use workspace_id-scoped storage or async-safe structure.
- `_MAX_SNAPSHOT_CHARS = 6000` is tight for content-heavy pages.
- No timeout on `client.navigate()` — could hang indefinitely if CamoFox
  is slow to respond.
- Tab cleanup on error paths could leak tabs (navigate fails → tab stays open).

---

### 3. file_generator.py (580 lines)
**Tools:** `lucy_write_file`, `lucy_edit_file`, `lucy_store_api_key`, `lucy_generate_pdf`, `lucy_generate_excel`, `lucy_generate_csv`
**Purpose:** File creation (PDF via WeasyPrint, Excel via openpyxl, CSV via stdlib) and workspace file operations.
**Health:** ✅ Good
**Strengths:**
- Professional PDF template with proper CSS styling
- Excel with auto-column-width, header formatting, multi-sheet support
- Slack upload with v1/v2 fallback
- `lucy_edit_file` uses exact-match SEARCH/REPLACE (safe, no regex)
- `_resolve_mangled_path` handles LLM path hallucinations gracefully
- PDF has minimum-length guard (200 chars) to prevent trivial PDFs

**Issues:**
- `lucy_generate_pdf` depends on WeasyPrint which requires system libraries
  (libcairo, libpango). ImportError handling is good but should surface the
  actual dependency issue to ops, not just the user.
- `lucy_store_api_key` is in file_generator.py — architecturally misplaced.
  Should live in an auth/secrets module.
- No DOCX (Word) generation support — users may expect this alongside PDF/Excel.
- Temp files from `tempfile.mkdtemp()` are never cleaned up. Should use
  atexit or periodic cleanup.

---

### 4. chart_generator.py (335 lines)
**Tools:** `lucy_generate_chart`
**Purpose:** Chart generation via matplotlib (line, bar, pie, scatter, area).
**Health:** ✅ Good
**Strengths:**
- Professional styling (Tailwind-inspired color palette, clean axes)
- 5 chart types with proper data validation
- Auto-upload to Slack with v1/v2 fallback
- Value labels on bar charts (when ≤12 groups)
- Configurable sizes (small/medium/large)

**Issues:**
- `import numpy as np` inside `_draw_bar` — this import should be at module
  level or checked at startup. numpy is heavy.
- No stacked bar chart support (common request).
- Pie chart autopct color is hardcoded white — may be invisible on light slices.
- Temp files (`NamedTemporaryFile(delete=False)`) are never cleaned up.

---

### 5. email_tools.py (214 lines)
**Tools:** `lucy_send_email`, `lucy_read_emails`, `lucy_reply_to_email`, `lucy_search_emails`, `lucy_get_email_thread`
**Purpose:** Email operations via AgentMail (lucy@zeeyamail.com).
**Health:** ✅ Good
**Strengths:**
- Clean separation of tool definitions and execution
- 5 well-defined tools covering full email lifecycle
- Error handling routes to AgentMail client
- HTML email support alongside plain text

**Issues:**
- No rate limiting on `lucy_send_email` — an agent loop could spam.
- No attachment support in send/reply tools.
- Missing `lucy_forward_email` tool.
- No confirmation/approval flow before sending (contrast with SYSTEM_CORE.md
  which says to pause on irreversible actions).

---

### 6. web_search.py (352 lines)
**Tools:** `lucy_web_search`
**Purpose:** 3-tier web search (quick → deep → multi-LLM consensus).
**Health:** ✅ Excellent
**Strengths:**
- Automatic tier selection via regex pattern matching (fast, no LLM call)
- Graceful fallback chain: Tier 3 → Tier 2 → Tier 1
- Circuit breaker integration (openrouter_breaker)
- Tier 2 patterns (version/pricing queries) and Tier 3 patterns (comparisons)
  are well-tuned

**Issues:**
- Tier classification is regex-only — some edge cases may route to wrong tier.
  e.g., "what's the latest React version" should be Tier 2 but "latest" alone
  isn't in the pattern (needs "latest version" together).
- No caching layer — identical queries within same session re-execute.
- Missing fallback for when OpenRouter is completely down (could use direct
  Perplexity API as backup).

---

### 7. workspace_tools.py (405 lines)
**Tools:** `lucy_workspace_read`, `lucy_workspace_write`, `lucy_workspace_list`, `lucy_workspace_search`, `lucy_manage_skill`
**Purpose:** Persistent workspace management (read/write files, manage skills).
**Health:** ✅ Excellent
**Strengths:**
- Protected paths regex prevents writing to state.json and backups
- Automatic backup before overwrite
- Skill management with frontmatter validation
- Search with directory scoping
- Size limits to prevent token blowup (50K chars read, 30 results search)

**Issues:**
- Path traversal check is only `..` — doesn't catch symlink attacks.
  Low severity since workspace is sandboxed.
- `_MAX_READ_CHARS = 50_000` could still blow context windows.
  Consider adaptive limits based on model context.
- No file deletion tool — can only overwrite with empty content.

---

### 8. services.py (210 lines)
**Tools:** `lucy_start_service`, `lucy_stop_service`, `lucy_list_services`, `lucy_service_logs`
**Purpose:** Persistent background service management via OpenClaw Gateway.
**Health:** ⚠️ Moderate
**Strengths:**
- Clean CRUD interface for background processes
- Timeout protection on `lucy_list_services` (8s)
- Good error messages that explain what went wrong

**Issues:**
- `get_gateway_client()` is called on every tool execution — should cache.
- No health check for the gateway before starting services.
- `lucy_start_service` doesn't pass `name` to the gateway — only `command`.
  The name is only used in the response, not for tracking.
- No restart/restart-on-crash capability.
- No resource limits on started services.

---

### 9. spaces.py (230 lines)
**Tools:** `lucy_spaces_init`, `lucy_spaces_deploy`, `lucy_spaces_list`, `lucy_spaces_status`, `lucy_spaces_delete`
**Purpose:** Full-stack web app scaffolding and deployment (React + Convex → Vercel).
**Health:** ✅ Good
**Strengths:**
- Clean 5-tool interface covering full app lifecycle
- Deploy includes validation warning detection
- `summary` field in results provides agent-friendly descriptions
- `app_tsx_path` guidance prevents LLM path hallucination

**Issues:**
- No build log access on deploy failure — just the error message.
- `lucy_spaces_delete` is irreversible with no confirmation mechanism.
- No `lucy_spaces_redeploy` for quick iteration.
- Missing environment variable management for apps.

---

### 10. deep_research.py (320 lines)
**Tools:** (No direct tools — called by web_search.py Tier 3)
**Purpose:** Multi-LLM consensus research via OpenRouter.
**Health:** ✅ Good
**Strengths:**
- Parallel queries to 3 models (Perplexity + Gemini + GPT-4o)
- Synthesis step identifies agreements and disagreements
- Fallback: if synthesis fails, returns Perplexity's answer
- Premium model roster option for high-stakes research
- Custom model support

**Issues:**
- `_QUERY_TIMEOUT = 45.0` per model, but `asyncio.gather` waits for slowest.
  Total wall-clock could be 45s+ if one model is slow.
- Synthesis prompt is very long (~500 tokens) — adds to cost.
- No streaming — user waits for all models + synthesis before seeing anything.
- `_parse_consensus` regex parsing is fragile for malformed model output.

---

### 11. perplexity_search.py (185 lines)
**Tools:** (No direct tools — called by web_search.py, bright_data_search.py)
**Purpose:** Perplexity sonar/sonar-pro search via OpenRouter.
**Health:** ✅ Excellent
**Strengths:**
- Clean, focused module with single responsibility
- 3 convenience functions: `search`, `quick_search`, `research_search`, `recent_search`
- Rate limit detection (429 handling)
- Recency filter support for time-sensitive queries
- Timeout + HTTP error handling with structured error returns

**Issues:**
- `search_recency_filter` parameter may not be supported via OpenRouter
  passthrough — needs verification.
- No retry on transient failures (429, 503).
- `_DEFAULT_TIMEOUT = 45.0` is generous — could cause perceived slowness.

---

### 12. bright_data_search.py (535 lines)
**Tools:** (No direct tools — called by web_search.py Tier 2)
**Purpose:** Deep search with Bright Data SERP + page scraping + synthesis.
**Health:** ⚠️ Moderate
**Strengths:**
- Full Tier 2 pipeline: Perplexity → SERP → scrape → synthesize
- Domain skip list (social media, auth walls)
- Bright Data zone auto-discovery
- Direct scraping fallback when Bright Data is unavailable
- HTML-to-text extraction with noise removal (nav, footer, etc.)

**Issues:**
- **BRIGHT_DATA_API_KEY is hardcoded** in source:
  `"db753300-891e-4cac-8989-10084f1582d5"` — should be env-only.
- `_bd_zones_checked` global state is not thread-safe.
- `_extract_text_from_html` has no timeout/size guard for pathological HTML.
- `_should_skip_url` uses `any(skip in domain)` which could false-positive
  on domains like "myreddit.com".
- Scraping 3 pages sequentially could take 60s+ — should use asyncio.gather
  (it does, but the timeout is per-page, not total).

---

### 13. custom_wrappers/__init__.py (530 lines)
**Tools:** Dynamic `lucy_custom_*` tools from wrapper modules
**Purpose:** Auto-discovery, validation, and execution of custom API wrappers.
**Health:** ⚠️ Needs Improvement
**Strengths:**
- Auto-discovery from meta.json + wrapper.py convention
- Tiered loading (TOOLS + TOOLS_ADVANCED) to save context tokens
- Intent-based wrapper detection with strong/context keyword tiers
- Negative phrase filtering to reduce false positives
- Composition mode detection (writing tasks skip data wrappers)
- Validation framework integration (schema + runtime checks)
- Health registry accessible via `get_wrapper_health()`

**Issues:**
- **Error handling in `execute_custom_tool` is too bare:**
  - No structured error responses
  - No retry on transient failures
  - No rate limit awareness
  - Async/sync detection is flawed — returns coroutine without awaiting
- **Import on every call:** `importlib.util.spec_from_file_location` runs on
  every tool execution, not cached.
- **No fallback behavior** when a wrapper is unhealthy — calls still route to it.
- **No timeout** on `execute_fn` calls — slow APIs hang the agent loop.

---

## Health Summary

| Module | Tools | Status | Priority Issues |
|--------|-------|--------|-----------------|
| code_executor.py | 3 | ✅ Excellent | Minor: truncation limit |
| browser.py | 4 | ✅ Good | Concurrency safety, timeout |
| file_generator.py | 6 | ✅ Good | Misplaced API key tool, no DOCX |
| chart_generator.py | 1 | ✅ Good | Temp file cleanup |
| email_tools.py | 5 | ✅ Good | No rate limiting, no attachments |
| web_search.py | 1 | ✅ Excellent | Edge case tier routing |
| workspace_tools.py | 5 | ✅ Excellent | No delete tool |
| services.py | 4 | ⚠️ Moderate | Gateway caching, no restart |
| spaces.py | 5 | ✅ Good | No build logs, no env vars |
| deep_research.py | 0* | ✅ Good | Wall-clock timeout, no streaming |
| perplexity_search.py | 0* | ✅ Excellent | No retry on 429 |
| bright_data_search.py | 0* | ⚠️ Moderate | **Hardcoded API key**, thread safety |
| custom_wrappers/ | dynamic | ⚠️ Needs Fix | Error handling, caching, timeouts |

*These modules are called internally by web_search.py, not registered as direct agent tools.

## Recommendations (Priority Order)

1. **Fix custom_wrappers error handling** — Add structured errors, timeouts, retry, rate limiting
2. **Add health_check.py** — Startup-time tool health verification
3. **Improve SYSTEM_CORE.md** — Explicit tool-selection guidance
4. **Clean up bright_data_search.py** — Remove hardcoded API key
5. **Add temp file cleanup** — file_generator and chart_generator leak temp files
6. **Add DOCX generation** — Common user request, complements PDF/Excel
7. **Add email rate limiting** — Prevent accidental spam
8. **Cache custom wrapper imports** — Re-importing on every call is wasteful
