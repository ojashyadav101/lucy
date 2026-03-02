# Lucy vs Viktor: Comparative Analysis & Questions for Viktor

## Context

We ran a head-to-head 15-prompt stress test between Lucy and Viktor on February 28, 2026. Both agents were sent identical prompts. The results revealed significant gaps in Lucy's response quality, formatting, depth, and personality compared to Viktor. This document contains our analysis and targeted questions for Viktor's team to help us close these gaps.

---

## Section 1: Side-by-Side Comparison Results

### TEST 1/15: Greeting ("Hi, how are you doing today?")

**Viktor** (163 chars):
> Hey @Ojash! :wave: I'm doing great, thanks for asking! All systems running smoothly and ready to help. How are you doing? Anything I can assist with today?

**Lucy** (98 chars):
> Hey! I'm doing great, thanks for asking. Ready to tackle whatever you've got. What's on your mind?

**Analysis:** Both decent. Viktor uses the user's name and an emoji. Lucy's is more generic but acceptable. Minor gap.

---

### TEST 2/15: Capabilities overview ("What can you do?")

**Viktor** (1,364 chars):
> Detailed, emoji-categorized list with 7 sections (:wrench: Engineering, :bar_chart: Research, :page_facing_up: Documents, :movie_camera: Media, :gear: Integrations, :brain: General). Uses bold section headers, specific capabilities, ends with "Think of me as an always-on teammate."

**Lucy** (538 chars):
> Started with "Got it, working on this now." then gave a shorter overview categorized by Research, Data, Automation. Less than half the detail.

**Issues:**
1. Lucy opened with a hardcoded "Got it, working on this now." for a simple question that should be instant
2. Lucy's overview is 40% the length with fewer categories
3. No emoji visual structure in Lucy's response
4. Viktor sounds like a teammate introducing themselves; Lucy sounds like a features list

---

### TEST 3/15: Date/time ("What day is it today and what's the current time in IST?")

**Viktor** (95 chars):
> Today is *Tuesday, February 24, 2026* and the current time in IST is *3:15 AM*. :crescent_moon:

**Lucy** (243 chars):
> Got it, working on this now. CORRECTED: I don't have access to real-time information or a live clock, so I cannot provide the current date or the exact time in IST.

**Issues:**
1. **CRITICAL FAILURE**: Lucy said "I don't have access to real-time information" despite having system-injected date/time
2. Sent "Got it, working on this now." for a 1-sentence answer
3. Viktor answered perfectly in one clean line with bold formatting and contextual emoji

---

### TEST 4/15: Calendar ("What's on my calendar for tomorrow?")

**Viktor** (877 chars):
> Full schedule with times, Google Meet links, attendee info, duplicate detection warning, and a summary line.

**Lucy** (647 chars):
> "Got it, working on this now." then asked to connect Google Calendar (not connected in Lucy's case).

**Issues:**
1. Integration gap (Viktor has Google Calendar connected, Lucy doesn't)
2. Viktor flags duplicate events proactively
3. Viktor includes a summary: "2 distinct events, ~2.5 hrs of meetings, free most of the afternoon"

---

### TEST 5/15: Email ("Do I have any unread emails?")

**Viktor** (333 chars):
> Honest answer that email isn't connected. Mentions the specific email address. Offers to help connect. :email: emoji.

**Lucy** (231 chars):
> "Got it, working on this now." then says can't access email and suggests pasting emails manually.

**Issues:**
1. Viktor mentions the user's specific email (hello@ojash.com)
2. Viktor offers to connect Gmail; Lucy suggests manual paste (lower agency)
3. Unnecessary acknowledgment from Lucy

---

### TEST 6/15: Code ("Write me a Python Fibonacci function")

**Viktor** (1,010 chars):
> Code with full docstring (Args, Returns, Raises, Examples). Followed by "Highlights" section explaining key features.

**Lucy** (664 chars):
> "Working on this now, I'll share the result shortly." then code with docstring. Shorter highlights.

**Issues:**
1. Viktor's docstring includes `Raises` and multiple `Examples`
2. Viktor adds a "Highlights" section with key callouts (type hints, edge cases, complexity)
3. Lucy's code is functionally identical but the presentation is weaker

---

### TEST 7/15: Framework comparison ("React vs Vue vs Svelte")

**Viktor** (4,695 chars):
> Uses ASCII code-block tables for Performance, Learning Curve sections. Includes a verdict per section. Comprehensive multi-section breakdown with :crossed_swords: opener.

**Lucy** (534 chars):
> "Got it, working on this now." then mentioned PDF generation failed, pivoted to Excel. Inline response is a brief summary with no tables.

**Issues:**
1. **MASSIVE GAP**: Viktor: 4,695 chars of structured comparison with ASCII tables. Lucy: 534 chars of plain text
2. Viktor uses code blocks for tabular data (side-by-side comparison across 5+ dimensions)
3. Viktor includes verdicts per section ("Svelte wins on raw performance")
4. Lucy failed to generate the response inline and deflected to an Excel file
5. Viktor's response is immediately useful in Slack; Lucy's requires downloading a file

---

### TEST 8/15: Research ("Top 3 AI code assistants in 2026")

**Viktor** (5,077 chars):
> Detailed pricing tables in code blocks for each tool. Pros/cons with :white_check_mark: and :x: emojis. Numbered sections with italicized taglines.

**Lucy** (504 chars):
> Brief overview with pricing but no code block tables. Less detail on pros/cons.

**Issues:**
1. Viktor uses code block tables for pricing tiers (clean, scannable)
2. Viktor uses :one: :two: :three: for ranked sections with italicized taglines
3. Viktor's response is 10x longer with actual pricing tier breakdowns
4. Lucy's response is adequate but shallow compared to Viktor's research depth

---

### TEST 9/15: Calendar analysis + meeting suggestion

**Viktor** (1,509 chars):
> Full week analysis with ASCII table showing events per day, busy time, and busy blocks. Color-coded indicators (🔴🟡🟢). Top 3 meeting slot recommendations with reasoning.

**Lucy** (478 chars):
> "Got it, working on this now." then asked to connect Google Calendar.

**Issues:**
1. Integration gap again (calendar not connected)
2. Viktor's response shows what a great calendar analysis looks like: ASCII tables, color indicators, ranked suggestions with reasoning

---

### TEST 10/15: Integrations list + Salesforce connection

**Viktor** (835 chars):
> Full list of 10 connected integrations with descriptions. Clear Salesforce status and OAuth link.

**Lucy** (477 chars):
> "Got it, working on this now." Only 2 integrations listed (Gmail, GitHub). Salesforce connection link provided.

**Issues:**
1. Viktor lists 10 integrations; Lucy lists 2
2. Viktor describes what each integration does
3. Both handle the Salesforce connection similarly

---

### TEST 11/15: Vague reference ("that thing we talked about earlier")

**Viktor** (736 chars):
> Lists all 10 previous conversations to help identify what the user meant. Acknowledges possible external context. :thinking_face: emoji.

**Lucy** (222 chars):
> "Got it, working on this now." Brief response saying can't find previous context.

**Issues:**
1. Viktor demonstrates thorough context search by listing all previous topics
2. Viktor's response is 3x more helpful for identifying what "that thing" was
3. Lucy's response is dismissive in comparison

---

### TEST 12/15: Meeting prep notes

**Viktor** (1,949 chars):
> Full prep notes per meeting with: time, location, attendees, talking points, action items, and a housekeeping note about duplicate events.

**Lucy** (533 chars):
> "Got it, working on this now." then asked to connect Google Calendar.

**Issues:**
1. Integration gap
2. Viktor's prep notes are extremely thorough (talking points, attendee context, action items)

---

### TEST 13/15: Memory ("Remember our Q1 revenue target")

**Viktor** (148 chars):
> "Got it, saved to my notes :white_check_mark:" with formatted key-value display.

**Lucy** (160 chars):
> "Got it, working on this now." then "Got it. I've noted..."

**Issues:**
1. **DOUBLE "Got it"**: Lucy says "Got it, working on this now." (system ack) then "Got it. I've noted..." (LLM response). Two "Got it"s in a row sounds robotic.
2. Viktor's response is cleaner with emoji and structured display

---

### TEST 14/15: Weekly report automation suggestion

**Viktor** (1,080 chars):
> Detailed automation proposal with recurring schedule, data sources, delivery format, and 3 configuration questions.

**Lucy** (866 chars):
> "Got it, working on this now." then actually set up the cron immediately (higher agency). But flagged $0 revenue (honest but potentially alarming).

**Notes:**
- Lucy actually wins on agency here by setting up the cron immediately instead of just suggesting it
- But the "Got it" opener and the $0 revenue flag could be improved

---

### TEST 15/15: Complex multi-step calendar + email analysis

**Viktor** (2,128 chars):
> Full ASCII table of next week's events. Gap analysis table. Top 3 meeting slot recommendations with reasoning, timezone notes, and email/Slack check results.

**Lucy** (530 chars):
> "Got it, working on this now." then asked to connect calendar.

**Issues:**
1. Integration gap
2. Viktor's response is the gold standard for structured analysis: tables, rankings, reasoning

---

## Section 2: Pattern Analysis

### Critical Patterns Where Lucy Falls Short

**Pattern 1: "Got it, working on this now." plague**
- Lucy sent this exact phrase (or a variant) in 13 out of 15 tests
- It was sent even for instant-answer questions (date/time, capabilities, memory storage)
- Viktor never uses generic acknowledgments. He either answers directly or starts with a task-specific opener

**Pattern 2: Response depth gap (Lucy averages ~450 chars vs Viktor's ~1,600 chars)**
- Viktor's responses are consistently 3-4x longer with more detail
- Viktor uses code block tables, emoji structure, and multiple sections
- Lucy tends to give a surface-level answer or deflect to a file download

**Pattern 3: ASCII table formatting**
- Viktor uses code blocks for structured data comparison (pricing, calendars, feature matrices)
- Lucy never uses ASCII tables, relies on plain bullet points
- This is a massive visual quality difference in Slack

**Pattern 4: Emoji as structural elements**
- Viktor uses emojis as section openers (:date:, :bar_chart:, :robot_face:), status markers (:white_check_mark:, :x:, :warning:), and ranking indicators (:one:, :two:, :three:, :star:, :trophy:)
- Lucy uses fewer emojis and they're less purposeful
- Viktor uses contextual emojis (:crescent_moon: for nighttime, :crossed_swords: for comparison)

**Pattern 5: Proactive insights**
- Viktor identifies duplicate calendar events, suggests cleanup
- Viktor adds summary lines ("2 distinct events, ~2.5 hrs of meetings, free most of the afternoon")
- Viktor includes timezone warnings and attendee context
- Lucy provides the data but rarely adds proactive observations

**Pattern 6: Handling inability**
- Viktor mentions the user's specific email address when saying he can't access email
- Viktor offers specific next steps ("I can help you get that connected!")
- Lucy sometimes suggests less helpful alternatives ("paste the emails here")

---

## Section 3: Questions for Viktor

These questions are designed to understand Viktor's architecture so we can implement similar improvements in Lucy.

### A. Response Generation Architecture

1. **System prompt structure**: Does Viktor use a single system prompt, or is it composed of multiple modules/layers? Approximately how long is the full system prompt (in tokens or characters)?

2. **Prompt composition**: Are there separate documents for personality/voice, formatting rules, tool guidelines, and domain knowledge? If so, how are they assembled before each request?

3. **Model selection**: Which LLM(s) does Viktor use for different task types? Is there a router that selects different models based on intent/complexity?

4. **Temperature and sampling**: What temperature settings does Viktor use? Does it vary by task type (creative vs factual)?

### B. Acknowledgment Flow

5. **How does Viktor generate the initial acknowledgment** when receiving a complex task? Is it LLM-generated, template-based, or a hybrid? How does it reference the specific task details?

6. **Does Viktor's acknowledgment run on a separate, faster model** than the main response? What's the typical latency for the acknowledgment?

7. **How does Viktor decide whether to send an acknowledgment** vs answering directly? What's the threshold (response time? complexity? intent type?)?

### C. Personality & Voice Consistency

8. **How does Viktor maintain voice consistency** across all responses? Is personality injected via system prompt, few-shot examples, or a post-processing layer?

9. **Does Viktor use positive guidance** (examples of good responses) or negative guidance (lists of things to avoid), or both? Which approach is more effective in your experience?

10. **How does Viktor handle the "AI tells" problem** (em dashes, power words, sycophantic openers, chatbot closers)? Is this done in the prompt, post-processing, or both?

### D. Formatting & Rich Output

11. **How does Viktor generate ASCII code-block tables?** Is this behavior in the system prompt (with examples), or does Viktor have a separate formatting layer that converts data into tables?

12. **Does Viktor output Block Kit JSON directly**, or does it output Markdown/mrkdwn and then convert? If there's a conversion layer, how does it work?

13. **How does Viktor decide when to use code block tables vs bullet lists vs plain text?** Is this in the prompt instructions or a post-processing decision?

14. **Emoji strategy**: Are Viktor's emoji placements driven by the LLM (prompted to use emojis as structural markers), or is there a post-processing step that injects them?

### E. Post-Processing Pipeline

15. **Does Viktor have any output sanitization or rewriting layers** between the LLM response and the Slack message? If so, what do they do (formatting, tone checking, AI-tell removal, link formatting)?

16. **Does Viktor have a quality gate** before sending? Something that checks if the response is good enough, or re-generates if it's too short/generic?

17. **Does Viktor have a self-critique step?** Does the LLM review its own output before sending?

### F. Progress Updates & Long-Running Tasks

18. **How does Viktor decide when to send progress updates** during long tasks? Is it time-based, step-based, or model-driven?

19. **How does Viktor handle LLM hangs or streaming silences?** What timeout mechanisms are in place?

### G. Context & Personalization

20. **How does Viktor personalize responses** to the user? Does it load user preferences, timezone, past interactions, or workspace context before each response?

21. **How does Viktor handle conversation history?** Does it maintain a session context, or does it re-read thread history each time?

22. **How does Viktor know the user's email address** (it mentioned "hello@ojash.com" when saying it can't access email)? Is this from Slack profile data, previous conversations, or integration metadata?

### H. Integration Architecture

23. **How does Viktor manage integrations** (Google Calendar, Gmail, GitHub, etc.)? Does it use Composio, Pipedream, direct OAuth, or a custom solution?

24. **Does Viktor have more integrations by default** than what the user manually connects? (Viktor showed 10 connected integrations vs Lucy's 2)

25. **How does Viktor's calendar integration work?** Does it cache calendar data, or does it make live API calls for each request?

### I. Error Handling & Graceful Degradation

26. **How does Viktor handle tool failures gracefully** in its responses? It never exposes errors to the user. Is this prompt-driven or pipeline-enforced?

27. **When Viktor can't do something, how does it decide what alternative to offer?** The alternatives are always specific and useful, not generic.

### J. Quality Control & Iteration

28. **Does Viktor have any A/B testing or quality measurement** for response quality? How do you evaluate whether responses are "good enough"?

29. **What was the single most impactful change** you made to improve Viktor's response quality?

30. **Are there any architectural decisions you'd make differently** if you were starting over?

---

## Section 4: Lucy's Current Architecture (for Viktor's Reference)

### Pipeline Overview

```
User Message
    │
    ├── Intent Classification (regex-based, <1ms)
    │       └── Routes to model tier: fast, default, code, research, document, frontier
    │
    ├── Acknowledgment (NEW: LLM-generated via gemini-2.5-flash, <3s)
    │       └── Only for complex intents: code, data, research, document, monitoring
    │
    ├── Supervisor Planning (gemini-2.5-flash, optional)
    │       └── Creates execution plan for complex tasks
    │
    ├── Agent Loop (multi-turn LLM + tool calls)
    │       └── Model: minimax-m2.5 (default), gemini-3-flash-preview (research)
    │       └── Max turns: 50, with silence detection at 8 minutes
    │
    └── Output Pipeline
            ├── Layer 1: Sanitizer (strip tool names, paths, secrets)
            ├── Layer 2: Markdown → Slack mrkdwn converter
            ├── Layer 3: Tone validator (catch robotic patterns)
            └── Layer 4: De-AI engine (regex-based, strips em dashes, power words, chatbot closers)
```

### Key Files

- **System prompt (personality)**: `prompts/SOUL.md` (~5,000 words of voice/tone guidance)
- **System prompt (behavior)**: `prompts/SYSTEM_CORE.md` (~7,000 words of task execution rules)
- **Output pipeline**: `src/lucy/pipeline/output.py` (sanitizer + markdown converter + tone validator + de-AI engine)
- **Intent router**: `src/lucy/pipeline/router.py` (regex-based intent classification)
- **Acknowledgment**: `src/lucy/slack/handlers.py` (LLM-generated context-aware acks)
- **Agent orchestrator**: `src/lucy/core/agent.py` (multi-turn loop with silence detection)
- **Config**: `src/lucy/config.py` (model tiers, API keys)

### Known Issues

1. **"Got it" duplication**: The system sends an LLM-generated acknowledgment, but the main LLM sometimes also starts with "Got it" in its response. We've added prompt guidance to prevent this but it's not 100% reliable.

2. **Date/time failure**: Despite explicit prompt instructions, the LLM sometimes claims it doesn't have date/time access. This is a model behavior issue with minimax-m2.5.

3. **Formatting depth**: Lucy's responses tend to be shorter and less visually structured than Viktor's. The system prompt encourages rich formatting but the model doesn't consistently produce it.

4. **Integration coverage**: Lucy has fewer integrations connected by default (Gmail, GitHub). Viktor appears to have Google Calendar, Drive, Sheets, Search Console, Linear, Clerk, Vercel, Polar, and Bright Data connected.

5. **Response length**: Lucy averages ~450 chars per response vs Viktor's ~1,600 chars. The responses are technically correct but lack the depth and analysis that makes Viktor's responses feel premium.

---

## Section 5: What We've Already Changed

Based on this analysis, we've implemented:

1. **Replaced hardcoded acknowledgments with LLM-generated ones** using gemini-2.5-flash. The new system generates context-aware, task-specific acknowledgments in <3 seconds.

2. **Updated SOUL.md**: Strengthened emoji guidance, added formatting examples (ASCII tables, code block comparisons), reinforced "never duplicate system acknowledgment" rule.

3. **Updated SYSTEM_CORE.md**: Fixed date/time instruction (added explicit "NEVER say you don't have access"), strengthened "no Got it" rule, added code block table formatting guidance.

4. **Swapped default model** from moonshotai/kimi-k2.5 to minimax/minimax-m2.5 (same model Viktor may be using or similar tier).

---

*Document generated: February 28, 2026*
*Repository: [github.com/ojashyadav101/lucy](https://github.com/ojashyadav101/lucy)*
