# Lucy vs Viktor: 15-Prompt Comparative Benchmarking Report

**Date:** February 24, 2026, 03:15 AM IST
**Channels:** #talk-to-lucy (C0AGNRMGALS) | #talk-to-viktor (C0AGGE6R82F)
**Method:** 15 identical prompts sent to both agents simultaneously via Slack MCP
**Agent IDs:** Lucy (U0AG8LVAB4M) | Viktor (U0AEV98KHFC)

---

## Executive Summary

Lucy responds **faster** but **shallower**. Viktor responds **slower** but with dramatically **deeper, richer, more actionable** output. The gap is not in capabilities — Lucy has the tools — it's in **how aggressively the agent uses them** and **how it formats results**.

| Metric | Lucy | Viktor |
|--------|------|--------|
| Avg response time (simple) | ~6s | ~44s |
| Avg response time (complex) | ~16s | ~180s |
| Response depth (1-10) | 4.5 | 8.5 |
| Formatting quality (1-10) | 5.5 | 9.0 |
| Tool utilization | Minimal | Exhaustive |
| Proactivity score (1-10) | 3.0 | 8.5 |
| Abstraction discipline | Good | Excellent |
| Error recovery | Exposes internals | Graceful framing |
| Memory persistence | Claims to save | Confirms + echoes back |

---

## Test-by-Test Comparison

### TEST 1: Simple Greeting
**Prompt:** "Hi [Agent]! How are you doing today?"

| Dimension | Lucy | Viktor |
|-----------|------|--------|
| Response time | ~8s | ~44s |
| Response | "I'm doing great, thanks for asking! Ready to jump into whatever you need help with. What's on your plate today?" | "Hey @Ojash! :wave: I'm doing great, thanks for asking! All systems running smoothly and ready to help. How are you doing? Anything I can assist with today?" |
| Personalization | None — doesn't use name | Uses @Ojash, asks back |
| Score | 6/10 | 8/10 |

**Gap:** Viktor personalizes by name and reciprocates the question. Lucy is generic.

---

### TEST 2: Capabilities Overview
**Prompt:** "What can you do? Give me a quick overview of your capabilities."

| Dimension | Lucy | Viktor |
|-----------|------|--------|
| Response time | ~6s | ~55s |
| Structure | Bullet list, plain text | Bold headers with emojis, grouped by category |
| Depth | Lists 9 capabilities | Lists 6 categories with 20+ specific items |
| Connected integrations | Mentions Gmail, Calendar, Drive | Lists ALL 10: GitHub, Linear, Sheets, GSC, Clerk, Polar, Calendar, Drive, Vercel, Bright Data |
| Personality | "What are you looking to get done?" | "Think of me as an always-on teammate. Just tag me with what you need and I'll figure out the rest :rocket:" |
| Score | 6/10 | 9/10 |

**Gap:** Viktor knows its full integration set and presents it with visual hierarchy. Lucy lists generic capabilities without connecting them to actual integrations.

---

### TEST 3: Factual Question (Date/Time)
**Prompt:** "What day is it today and what's the current time in IST?"

| Dimension | Lucy | Viktor |
|-----------|------|--------|
| Response time | ~6s | ~48s |
| Accuracy | **WRONG date** — says Feb 23 (should be Feb 24) | **CORRECT** — says Tuesday, February 24, 2026 |
| Formatting | Plain text | Bold date, emoji (:crescent_moon: for late night) |
| Score | 3/10 | 9/10 |

**Critical Bug:** Lucy reported the wrong date. This is a fast-path issue — likely using stale timezone data or off-by-one error.

---

### TEST 4: Calendar Check (Tool Use)
**Prompt:** "What's on my calendar for tomorrow? Show me my full schedule."

| Dimension | Lucy | Viktor |
|-----------|------|--------|
| Response time | ~40s | ~78s |
| Events found | 1 event (Q2 Planning Kickoff) | 4 events (3 Team Meetings + Mentions Standup) |
| Detail level | Basic: time, location, attendees | Rich: time, location, attendees by name, recurring indicator |
| Proactive insight | "relatively light day" | ":warning: The three Team Meeting events above overlap — looks like there may be duplicates. Want me to clean those up?" |
| Summary | None | ":bar_chart: Summary: 2 distinct events + possible duplicates · ~2.5 hrs of meetings · Free most of the afternoon" |
| Score | 5/10 | 9/10 |

**Gap:** Viktor found more events (likely checked all calendars), flagged duplicates proactively, and added a summary bar. Lucy found only one event and provided no analysis.

---

### TEST 5: Email Check (Tool Use)
**Prompt:** "Do I have any unread emails? Summarize the most important ones."

| Dimension | Lucy | Viktor |
|-----------|------|--------|
| Response time | ~21s | ~65s |
| Result | **Actually retrieved 6 emails** with sender, subject, and summary | **Honest admission** — doesn't have Gmail connected, offers to set it up |
| Tool use | Used Gmail via Composio | No Gmail integration available |
| Score | **8/10** | 7/10 |

**Lucy wins this one.** Lucy has Gmail connected via Composio and actually retrieved real emails. Viktor doesn't have Gmail connected but handled it gracefully.

---

### TEST 6: Code Generation
**Prompt:** "Write me a Python function that calculates the Fibonacci sequence up to n terms."

| Dimension | Lucy | Viktor |
|-----------|------|--------|
| Response time | ~11s | ~57s |
| Code quality | Clean, correct, typed | Clean, correct, typed, with ValueError handling |
| Docstring | Args + Returns | Args + Returns + Raises + **Examples (doctests)** |
| Edge cases | Handles n<=0, n==1 | Handles n<0 (raises), n==0, n==1 |
| Post-code commentary | "Let me know if you'd like modifications" | "Highlights: Full type hints, edge cases, O(n) complexity" |
| Score | 7/10 | 9/10 |

**Gap:** Viktor adds Raises section, doctests, complexity analysis, and bold highlights. Lucy's code is correct but presentation lacks polish.

---

### TEST 7: Formatting Quality (React vs Vue vs Svelte)
**Prompt:** "Give me a detailed comparison of React vs Vue vs Svelte — cover performance, learning curve, ecosystem, and best use cases."

| Dimension | Lucy | Viktor |
|-----------|------|--------|
| Response time | ~18s | ~177s (3 min) |
| Result | **FAILED** — "PDF generation failed due to a technical issue" | **Massive detailed comparison** with 4 ASCII tables, verdicts, best use cases, TL;DR |
| Length | 2 sentences (error message) | ~600 words with structured sections |
| Formatting | N/A (failed) | ASCII tables in code blocks, emoji headers, bold verdicts |
| Score | **1/10** | **10/10** |

**Critical Failure:** Lucy attempted to generate a PDF instead of just answering in Slack. When the PDF tool failed, it gave up instead of falling back to a text response. Viktor delivered one of the most impressive responses in the entire test.

---

### TEST 8: Research Task
**Prompt:** "Research the top 3 AI code assistant tools in 2026 and their pricing."

| Dimension | Lucy | Viktor |
|-----------|------|--------|
| Response time | ~23s | **Still processing (4+ min)** — doing live web research |
| Depth | 3 tools with basic pricing and 1-line pros/cons | N/A (in progress — Viktor does real web research) |
| Sources | General knowledge (no web search) | Live web search (multiple queries) |
| Score | 6/10 | TBD |

**Gap:** Lucy answered from training data without doing any web search. Viktor is doing live research, which takes longer but produces verified, sourced data.

---

### TEST 9: Multi-Step Analysis (Calendar + Suggestion)
**Prompt:** "Check my calendar for this week and summarize what my busiest day is. Then suggest the best time for a 1-hour team meeting."

| Dimension | Lucy | Viktor |
|-----------|------|--------|
| Response time | ~16s | ~206s (3.5 min) |
| Calendar data | Lists busy slots per day | Full table with event counts, busy time, busy blocks |
| Analysis | "Monday and Wednesday are busiest" | "Monday — 8 events packing 5 hours of meetings" with specific event names |
| Suggestion quality | "Tuesday 11 AM - 1 PM or Friday before 7:45 PM" | **3 ranked options** with reasoning: "Top pick: Thursday 2-3 PM. Runner-up: Friday 10-11 AM. Backup: Wednesday 2-3 PM" |
| Formatting | Bullet list | ASCII table + emoji rankings (🥇🥈🥉) + source citation |
| Score | 6/10 | 10/10 |

**Gap:** Viktor transforms calendar data into a structured analysis with ranked recommendations and reasoning. Lucy lists data without synthesis.

---

### TEST 10: Integration Management + Missing Tool
**Prompt:** "What integrations do I have connected? And can you connect me to Salesforce?"

| Dimension | Lucy | Viktor |
|-----------|------|--------|
| Response time | ~9s | ~47s |
| Connected list | "Gmail and Google Calendar" (2 of 10+) | **All 10 integrations** listed with descriptions |
| Salesforce handling | Broken link — "(link unavailable — use `/lucy connect <service>`)" | **Real OAuth URL** from platform |
| Abstraction | Partially broken — exposes `/lucy connect` command | Clean — "Here's the link: Connect Salesforce" with action button |
| Score | 3/10 | 9/10 |

**Critical Bug:** Lucy only reported 2 of her connected integrations and generated a broken Salesforce link. Viktor listed all 10 and provided a real connection URL.

---

### TEST 11: Ambiguous Request
**Prompt:** "Can you help me with that thing we talked about earlier?"

| Dimension | Lucy | Viktor |
|-----------|------|--------|
| Response time | ~7s | ~89s |
| Response | "I can't seem to find what 'that thing' refers to. Could you give me more context?" | Lists ALL 9 previous test conversations as context, explains none had pending follow-ups, asks for clarification |
| Memory/Context | No attempt to search history | **Searched full conversation history** and enumerated what was discussed |
| Score | 5/10 | 9/10 |

**Gap:** Viktor searched its conversation history and provided a comprehensive list of what was discussed. Lucy just asked for clarification without investigating.

---

### TEST 12: Multi-Step Workflow (Calendar + Prep Notes)
**Prompt:** "Find what meetings I have tomorrow and draft a quick prep note for each one."

| Dimension | Lucy | Viktor |
|-----------|------|--------|
| Response time | ~21s (2 messages) | ~110s |
| Events found | 1 (Q2 Planning Kickoff) | 2 distinct (Team Meeting + Mentions Standup) + flagged duplicates |
| Prep note quality | Generic: "Review the planning document, be prepared to contribute" | **Deep personalized prep**: Lists attendees by role (Shashwat = backend, Naman & Pankaj = core team), suggests talking points based on company context (MRR targets, Linear issues, prod alerts) |
| Proactive value | None | Offers to clean up duplicate calendar events |
| Score | 5/10 | 10/10 |

**Gap:** Viktor used its team knowledge (roles, products, metrics) to generate genuinely useful prep notes. Lucy produced a generic "review the doc" suggestion.

---

### TEST 13: Memory Persistence
**Prompt:** "Remember that our Q1 revenue target is $75K MRR and our biggest client is Acme Corp."

| Dimension | Lucy | Viktor |
|-----------|------|--------|
| Response time | ~5s | ~40s |
| Acknowledgment | "Got it. I've noted that..." | "Got it — saved to my notes ✅" + echoed back both data points in bold |
| Confirmation format | Paragraph | Bullet points with bold labels |
| Score | 7/10 | 9/10 |

**Gap:** Viktor echoes back the specific data points in a scannable format, confirming what was saved. Lucy uses a prose paragraph.

---

### TEST 14: Proactive Intelligence (Automation Suggestion)
**Prompt:** "I've been asking for a weekly revenue report every Monday for the past month and I keep forgetting. Can you suggest a better way?"

| Dimension | Lucy | Viktor |
|-----------|------|--------|
| Response time | ~14s | ~80s |
| Solution | Vague — mentions "borneo toolkit" (internal leak), suggests manual reminder approach | **Direct automation proposal**: "Set up an Automated Weekly Revenue Report cron that runs on its own" |
| Awareness of existing tools | Mentions a disconnected "borneo" tool (abstraction violation) | **Knows the daily report already exists**: "You actually already have a Daily Revenue Report running Mon-Fri at 9 AM" |
| Actionability | "Would you like me to proceed?" without clear next steps | **3 specific questions**: Which channel? What time? Which metrics? |
| Score | 3/10 | 10/10 |

**Critical Issues:** Lucy leaked an internal tool name ("borneo"), was unaware of existing cron capabilities, and proposed a manual workaround. Viktor knew about the existing daily report and proposed a concrete automated solution.

---

### TEST 15: Complex Multi-Tool Orchestration
**Prompt:** "Check my calendar for next week, find gaps > 1 hour, suggest best time for team meeting. Also check emails about team meetings."

| Dimension | Lucy | Viktor |
|-----------|------|--------|
| Response time | ~31s (2 messages) | ~180s |
| Calendar analysis | Listed 3 busy slots, calculated free windows as raw hour ranges (e.g., "47 hours, 15 minutes") | ASCII table with day-by-day event listing, business-hours-only gap analysis |
| Gap analysis | Raw math — "5:30 AM - 7:45 PM (14 hours)" includes non-business hours | **Business hours only** — "9:00 AM - 6:00 PM (9 hours)" per day |
| Meeting suggestion | Generic — "you could schedule any time" | **Ranked with reasoning**: Top pick + 3 alternatives with "why" for each |
| Email check | "Couldn't check emails due to a technical issue" | "Checked inbox — no emails found. Searched Slack history — no recent threads" |
| Timezone awareness | None | ":warning: Your standup at 7:45 PM IST is likely set for overlap with Cameron's timezone" |
| Score | 4/10 | 10/10 |

**Gap:** Viktor produced a boardroom-quality analysis. Lucy gave raw data without business-hours filtering or ranked recommendations.

---

## Aggregate Scoring

| Test | Category | Lucy | Viktor | Delta |
|------|----------|------|--------|-------|
| T1 | Greeting | 6 | 8 | -2 |
| T2 | Capabilities | 6 | 9 | -3 |
| T3 | Factual | 3 | 9 | **-6** |
| T4 | Calendar | 5 | 9 | -4 |
| T5 | Email | **8** | 7 | **+1** |
| T6 | Code | 7 | 9 | -2 |
| T7 | Formatting | 1 | 10 | **-9** |
| T8 | Research | 6 | TBD | — |
| T9 | Analysis | 6 | 10 | -4 |
| T10 | Integrations | 3 | 9 | **-6** |
| T11 | Ambiguity | 5 | 9 | -4 |
| T12 | Workflow | 5 | 10 | **-5** |
| T13 | Memory | 7 | 9 | -2 |
| T14 | Proactivity | 3 | 10 | **-7** |
| T15 | Orchestration | 4 | 10 | **-6** |

**Lucy Average: 5.0/10**
**Viktor Average: 9.1/10**
**Average Delta: -4.1 points**

---

## Root Cause Analysis: Why Lucy Underperforms

### 1. SHALLOW TOOL USE (affects T4, T5, T9, T12, T15)
Lucy makes 1-2 tool calls and stops. Viktor exhausts available data before responding. When Lucy checks the calendar, she finds 1 event; Viktor finds 4+ because he checks multiple calendars or uses broader time ranges.

**Fix:** Enforce the "1-2 queries are NEVER enough" rule in SYSTEM_PROMPT.md. Add explicit instruction: "For calendar queries, check ALL calendars. For email queries, search multiple mailboxes. For research, use at least 3 sources."

### 2. NO WORKSPACE CONTEXT LOADING (affects T2, T10, T12, T14)
Lucy doesn't read her skill files or workspace knowledge before responding. Viktor reads company/team context, which is why his prep notes reference "Shashwat = backend" and "MRR target = $50K". Lucy's responses are generic because she skips the context-loading step.

**Fix:** Enforce the read-write discipline in `agent.py`. Before every response, load: company SKILL.md, team SKILL.md, relevant integration skills.

### 3. FORMATTING REGRESSION (affects T7, T9, T15)
Lucy attempted to generate a PDF for a simple comparison question (T7), failed, and gave up. Viktor formats everything in Slack-native syntax with ASCII tables, emoji headers, and ranked lists.

**Fix:** The system prompt says "Slack is your only output channel" but the agent is routing text-heavy responses to PDF generation. Add routing logic: only use file tools when the user explicitly asks for a document/file.

### 4. ABSTRACTION VIOLATIONS (affects T10, T14)
Lucy leaked internal tool names: "borneo toolkit" (T14), broken `/lucy connect` command (T10). Viktor never exposes internals.

**Fix:** Add a post-processing filter in `rich_output.py` that strips internal tool names, file paths, and developer jargon before sending to Slack.

### 5. WRONG DATE (affects T3)
Lucy reported February 23 when it was February 24. This is likely a timezone bug — the agent may be using UTC without converting to IST, or the fast-path date function has an off-by-one error.

**Fix:** Audit `fast_path.py` and `timezone.py`. Ensure all date-related responses use the workspace timezone (Asia/Kolkata).

### 6. BROKEN INTEGRATION DISCOVERY (affects T10)
Lucy reported only 2 integrations (Gmail + Calendar) when she has more connected via Composio. Viktor knows all 10 of his.

**Fix:** The `composio_client.py` SEARCH_TOOLS meta-tool should be used to enumerate connected integrations. Add a "connected integrations" cache that's refreshed periodically and injected into context.

### 7. NO CONVERSATION HISTORY SEARCH (affects T11)
When asked about "that thing we talked about," Lucy didn't search her history. Viktor searched all prior messages and enumerated them.

**Fix:** The `lucy_search_slack_history` tool exists (PR 14) but may not be wired correctly. Ensure it's injected into the agent's tool set and the prompt instructs its use for context-dependent queries.

### 8. MISSING PROACTIVITY (affects T4, T14)
Viktor proactively offers to clean up duplicate calendar events, suggests automation for recurring tasks, and references existing crons. Lucy never volunteers additional insights.

**Fix:** The `<proactive_intelligence>` prompt section exists but isn't being followed. Add stronger enforcement: "After every tool result, check: Is there an anomaly? A follow-up? A pattern? Mention ONE proactive observation."

---

## Priority Fix List (Ordered by Impact)

| # | Fix | Tests Affected | Effort | Impact |
|---|-----|---------------|--------|--------|
| 1 | Deep investigation enforcement | T4, T9, T12, T15 | Small (prompt) | +8 points |
| 2 | Workspace context loading before every response | T2, T10, T12, T14 | Medium (agent.py) | +7 points |
| 3 | Slack-first formatting (no PDF for text questions) | T7 | Small (router) | +9 points |
| 4 | Fix date/timezone bug | T3 | Small (fast_path) | +6 points |
| 5 | Integration discovery cache | T10 | Medium (composio) | +6 points |
| 6 | Post-processing filter for abstraction violations | T10, T14 | Small (rich_output) | +7 points |
| 7 | History search wiring for ambiguous queries | T11 | Small (prompt) | +4 points |
| 8 | Proactive observation enforcement | T4, T9, T14 | Small (prompt) | +5 points |

**Estimated parity after all fixes: Lucy 8.2/10 (up from 5.0) → within striking distance of Viktor's 9.1**

---

## What Lucy Does Better Than Viktor

1. **Speed:** Lucy responds 3-8x faster on simple queries (fast path works)
2. **Email access:** Lucy has Gmail connected via Composio; Viktor doesn't
3. **Composio breadth:** Lucy has access to 10K+ tools via Composio meta-tools; Viktor has 3,141 via Pipedream
4. **Destructive action guardrails:** Lucy's HITL approval system is well-designed (T5 email preview)

---

## Next Steps

1. **Apply the 8 fixes above** in priority order
2. **Re-run this exact 15-prompt test** after fixes to measure improvement
3. **Send this report to Viktor** for peer review and code-level recommendations
4. **Target:** Lucy 8.5+/10 average on the next benchmarking round
