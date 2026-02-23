# Help Request: Architecture Audit & PR Contributions for Lucy

Hello Viktor! 

We are building Lucy, an AI coworker running on Slack. We admire your architecture and capabilities, and we're trying to replicate your level of intelligence, proactivity, and resilience. 

We've recently made a series of massive architectural updates based on your engineering guide, but we've hit a few roadblocks where we need your direct contribution. We have temporarily made this repository public so you can review our codebase and **create Pull Requests directly**.

## Recent Updates We've Made (Based on your guidance)
We've recently implemented a deep reliability and response quality overhaul:
1. **Event Deduplication:** Added an in-memory TTL cache to prevent double-processing of Slack's dual `app_mention` and `message` events.
2. **Graceful Error Handling:** Wrapped OpenClaw LLM calls with `tenacity` retries with exponential backoff for transient errors.
3. **LLM-Redirected Recovery:** Instead of hardcoded "I hit a snag" messages, we now inject `{"role": "system"}` messages back into the conversation to instruct the LLM to try a different approach if a tool call loops 3 times.
4. **Silent Recovery Cascade:** If the agent fails entirely, we silently retry, then downgrade the model to a faster tier, and finally deliver partial results if available. We NEVER show "Something went wrong".
5. **400 Error Recovery Mid-Loop:** If the payload gets too large and OpenRouter returns a 400, we aggressively trim old tool results and escalate to a frontier model with a larger context window.
6. **Dynamic Environment Injection:** We now query Composio at runtime for active integrations and inject them directly into the `<current_environment>` block of the system prompt.
7. **Three-Layer Output Pipeline:** We added a sanitizer (to strip paths, SDK names, and internal references), a Markdown-to-Slack converter, and a Tone Validator to catch robotic/error-dump patterns before they ship to the user.
8. **Composio Search Pre-Filtering:** We now pre-filter `COMPOSIO_SEARCH_TOOLS` results in code so the LLM doesn't dump the entire catalog to the user.
9. **System Prompt Enrichments:** Added `<intelligence_rules>`, `<response_quality>`, `<memory_discipline>`, and a "never show weakness" `<error_handling>` philosophy.

---

## Recent Stress Test Results
We ran a series of stress tests (Concurrency, Sequential Workflows, Parallel Tasks, and Sustained Load). You can view the full detailed logs from these tests in this repo at:
`docs/tests/latest_stress_test.txt`

**Observations from the logs we'd love your thoughts on:**
- Our total request latency sometimes exceeds 60s for complex tool use chains (we logged 3 anomalies: 65s, 75s, 90s). How do you handle UI/UX and timeout thresholds during long chains without abandoning the user?
- We're seeing heavy token usage during complex Composio multi-tool searches. 
- Are we routing models correctly based on intent? (We're routing standard chat to fast models, and complex tool/reasoning to frontier models).

---

## Remaining Issues & Areas Where We Need Your Help (PRs requested!)

Please review our codebase, compare it with your own architecture, and **submit Pull Requests** addressing the following gaps. We also welcome PRs for *any* other foundational issues you discover during your audit!

### 1. Composio Client Rewrite (Session-Based API)
**The Problem:** In `src/lucy/integrations/composio_client.py`, our `execute_tool_call` method currently uses the **discouraged** low-level `composio.tools.execute()` API. This bypasses session context, meaning meta-tools cannot share state (e.g., `SEARCH_TOOLS` context isn't passed to `MULTI_EXECUTE_TOOL`).
**The Ask:** Rewrite our Composio client to use proper session-based execution (`session.handle_tool_call` or OpenAI wrapper) so we can support complex workflows. 
*Question for you:* Will limiting Composio to 5 meta-tools (Search, Connect, Execute, etc.) cause scalability or rate-limit issues down the line when we have 500+ active users hitting diverse platforms?

### 2. "Out of this world" C-Skills (Document Generation)
**The Problem:** Our document generation skills are currently basic two-paragraph summaries. We want them to be as high-quality and richly formatted as yours.
**The Ask:** We need you to enrich or build out the following skills using best-in-class libraries (which we intend to run in our sandboxes):
- **PDF Creation (`pdf-creation`):** Implement a two-track system using **WeasyPrint** (for custom HTML/CSS docs) and **Typst** (for blazing fast structured reports) with a design system approach (CSS variables, typography scales, `@page` rules).
- **Excel Editing (`excel-editing`):** Implement **XlsxWriter** (creation) + **openpyxl** (modification) patterns with financial model color standards, conditional formatting, Sparklines, and proper charts.
- **Word/PPTX (`docx-editing`, `pptx-editing`):** Implement `python-docx` + `docxtpl` for Word, and template-first `python-pptx` approaches.

### 3. OpenClaw Native Crons
**The Problem:** We need to fully leverage OpenClaw's first-class built-in cron system (`cron.add`, `cron.run`, `cron.list`).
**The Ask:** Please update our `scheduled-crons` skill to use these native OpenClaw tools (with IANA timezone support, isolated session targets, and proper task.json/LEARNINGS.md accumulation).

### 4. CamoFox Browser Integration
**The Problem:** We do not want to use browser-based SDKs (like Browserbase or Puppeteer directly) because they are easily detected.
**The Ask:** We want to integrate [CamoFox Browser Server](https://github.com/redf0x1/camofox-browser). Please rewrite our `browser` skill to utilize the CamoFox REST API (search macros, accessibility snapshots with `eN` element refs).

### 5. Native Timezone Awareness
**The Problem:** Slack teams collaborate across timezones, and "today at 3 PM" means different things to different users.
**The Ask:** Implement timezone resolution using Slack's `user.tz` and `user.tz_offset`. We need a robust `get_user_local_time(workspace_id, user_slack_id)` utility, and we need our onboarding profile script to accurately capture and refresh this data.

### 6. System Prompt & Prompt Engineering Audit
**The Ask:** Please review `assets/SYSTEM_PROMPT.md` and `assets/SOUL.md`. What critical behavioral instructions are we missing? What are we over-prompting? How do you ensure your agent always sounds competent and handles failures so gracefully?

---

## Instructions for Viktor
We want Lucy to be an exact architectural replica of your best practices (or better!). Please:
1. Analyze `src/lucy/`, our skills directories, and our architectural choices.
2. Read the stress test logs in `docs/tests/latest_stress_test.txt` to see our actual LLM interactions and tool chains.
3. Identify any anti-patterns we've missed.
4. Open Pull Requests against this repository fixing the issues above. You are authorized to make massive codebase-level restructuring changes if it brings us closer to your architecture.

Thank you!