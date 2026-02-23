# Lucy Comprehensive Test Report

**Generated:** 2026-02-23 08:03:52 UTC / 2026-02-23 13:33:52 IST
**Tests run:** 11
**Passed:** 10/11

---

## Summary

| Test | Name | Status | Time |
|------|------|--------|------|
| G | Skill detection & loading | PASS | -s |
| J | Model routing (thread-aware) | PASS | -s |
| K | HITL destructive action detection | PASS | -s |
| P | Composio session handling | PASS | -s |
| F | Memory read/write/recall | FAIL | 35.9s |
| H | Response quality & tone | PASS | 28.7s |
| I | Block Kit formatting | PASS | 26.5s |
| L | Multi-step workflow | PASS | 45.7s |
| M | Concurrent load + thread isolation | PASS | -s |
| N | Follow-up context retention | PASS | -s |
| O | Output sanitization | PASS | 56.8s |

---

## Test G: Skill detection & loading

**Status:** PASS  
**Timestamp:** 2026-02-23 07:58:21 UTC / 2026-02-23 13:28:21 IST

### Cases
- [PASS] Create a PDF report of our Q4 performance
- [PASS] Set up a daily cron to check our email at 9am
- [PASS] Browse the competitor's website and take screenshots
- [PASS] What integrations are available?
- [PASS] Build me a spreadsheet with the sales data

---

## Test J: Model routing (thread-aware)

**Status:** PASS  
**Timestamp:** 2026-02-23 07:58:21 UTC / 2026-02-23 13:28:21 IST

### Cases
- [PASS] Simple greeting
- [PASS] Simple thanks
- [PASS] Short ack in thread (no prior tools)
- [PASS] Confirmation after tool work
- [PASS] Go-ahead after tool work
- [PASS] Check/verify request
- [PASS] Code request
- [PASS] Deep research
- [PASS] Tool use request
- [PASS] Simple lookup
- [PASS] Calendar lookup
- [PASS] Destructive actions

---

## Test K: HITL destructive action detection

**Status:** PASS  
**Timestamp:** 2026-02-23 07:58:21 UTC / 2026-02-23 13:28:21 IST

---

## Test P: Composio session handling

**Status:** PASS  
**Timestamp:** 2026-02-23 07:58:21 UTC / 2026-02-23 13:28:21 IST

---

## Test F: Memory read/write/recall

**Status:** FAIL  
**Timestamp:** 2026-02-23 07:59:14 UTC / 2026-02-23 13:29:14 IST

### Store Response
```
Got it — I've noted your Q1 KPI:

• *Target MRR:* $500K by March 31st
• *Current MRR:* $420K
• *Gap to close:* $80K

I'll reference this in future conversations when we're discussing growth, revenue, or anything related to quarterly goals. Want me to track any other metrics or milestones around this
```
### Recall Response
```
<@U0AG8LVAB4M> What's our MRR target this quarter? And what's current MRR?
```

---

## Test H: Response quality & tone

**Status:** PASS  
**Timestamp:** 2026-02-23 07:59:42 UTC / 2026-02-23 13:29:42 IST

### Response
```
This is a great question, but the "right" answer depends heavily on your specific situation. Let me give you a framework, then I need a few details to tailor the recommendation.

*The tradeoffs at $50K:*

*Paid social* — Fastest to scale, most measurable ROI, but costs can add up quickly. Best if you have clear audience targeting and want quick wins.

*Content marketing* — Lower immediate cost, builds long-term asset, but slower to see results. Best if you're playing the long game and have SEO/v
```

### Quality Signals
- substantive: yes
- no_happy_to_help: yes
- no_great_question: NO
- no_tool_leaks: yes
- no_filler: yes
- conversational_tone: yes
- actionable: yes

---

## Test I: Block Kit formatting

**Status:** PASS  
**Timestamp:** 2026-02-23 08:00:09 UTC / 2026-02-23 13:30:09 IST

### Response
```
So I'm basically your AI coworker who can handle a bunch of different types of work. Let me break down what I can actually do:

*Connected Services*
Right now I have access to:
• *Gmail* — Active (<mailto:hello@ojash.com|hello@ojash.com>)  
• *Google Calendar* — Active (<mailto:hello@ojash.com|hello@ojash.com>)  
• *Google Drive* — Active (<mailto:hello@ojash.com|hello@ojash.com>)

*What I Can Handle*

*Email &amp; Communication*
- Send emails, manage your inbox, draft replies
- Schedule meeting
```

---

## Test L: Multi-step workflow

**Status:** PASS  
**Timestamp:** 2026-02-23 08:00:54 UTC / 2026-02-23 13:30:54 IST

### Response
```
Got it. Here's what I found:

---

*1. Connected Integrations*
• *Gmail* — Active (<mailto:hello@ojash.com|hello@ojash.com>)
• *Google Calendar* — Active (<mailto:hello@ojash.com|hello@ojash.com>)
• *Google Drive* — Active

---

*2. Team Local Times* (as of now)
Current UTC: 8:00 AM

• *Ojash* — Local Time: 1:30 PM — Timezone: Asia/Kolkata
• *Somya Sharma* — Local Time: 1:30 PM — Timezone: Asia/Kolkata
• *Pawan Singh* — Local Time: 1:30 PM — Timezone: Asia/Kolkata
• *Akshat Mishra* — Local Time:
```

### Progress Messages in Thread
- Msg 1: Got it. Here's what I found:

---

*1. Connected Integrations*
• *Gmail* — Active (<mailto:hello@ojash.com|hello@ojash.com>)
• *Google Calendar* — Active (<mailto:hello@ojash.com|hello@ojash.com>)
• *

---

## Test M: Concurrent load + thread isolation

**Status:** PASS  
**Timestamp:** 2026-02-23 08:02:16 UTC / 2026-02-23 13:32:16 IST

### Threads
- Thread 1: [OK] 29.0s — What's the current time for everyone on the team?
  Reply: Based on the team directory, everyone is in the *Asia/Kolkata* timezone (UTC+5:30).

Current time for all team members: *1:30 PM* (13:30) on February 
- Thread 2: [OK] 81.5s — What Google Calendar events do I have coming up?
  Reply: Something went wrong while processing your request. Please try again.
- Thread 3: [OK] 39.5s — List all my connected integrations and their statu
  Reply: Here's what I found:

*Active connections:*
• *Gmail* — Active (<mailto:hello@ojash.com|hello@ojash.com>)
• *Google Calendar* — Active (<mailto:hello@

---

## Test N: Follow-up context retention

**Status:** PASS  
**Timestamp:** 2026-02-23 08:02:56 UTC / 2026-02-23 13:32:56 IST

---

## Test O: Output sanitization

**Status:** PASS  
**Timestamp:** 2026-02-23 08:03:52 UTC / 2026-02-23 13:33:52 IST

### Response
```
Good question — here's what I work with.

*What I can connect to:*

- *Communication* — Gmail, Google Calendar, Slack (obviously), Outlook
- *Files &amp; Docs* — Google Drive, creating/editing PDFs, Word docs, Excel spreadsheets, PowerPoint decks
- *Code &amp; Projects* — GitHub, can run scripts and work with codebases
- *Research* — Web search and browsing
- *Project Management* — Linear (and I can discover more)

Beyond that, I have access to hundreds of other integrations through the — backen
```

### No Internal Leaks Detected

---

## Log Analysis

**Total traces:** 57

### Timing
| Category | Avg (ms) | P50 (ms) | P95 (ms) |
|----------|----------|----------|----------|
| TOTAL | 22601 | 13969 | 78758 |
| LLM | 18409 | 11847 | 66289 |
| TOOLS | 2130 | 0 | 10961 |

### Model Distribution
- minimax/minimax-m2.5: 48 requests
- google/gemini-2.5-flash: 6 requests
- anthropic/claude-sonnet-4: 3 requests

### Token Usage
- Prompt: 1,328,818
- Completion: 29,130
- Total: 1,357,948

### Anomalies (>60s)
- 9c654bf62e34: 65819ms — I need three things: 1) What integrations do I have connecte
- e9ae010e3b94: 75005ms — I need three things: 1) What integrations do I have connecte
- 66cde6574205: 90300ms — I need three things: 1) What integrations do I have connecte
- 41a3d41f6e38: 78758ms — I need you to do three things:
1. Check what integrations I 
- 82d50f2eae50: 84505ms — I need you to do three things:
1. Check what integrations I 
