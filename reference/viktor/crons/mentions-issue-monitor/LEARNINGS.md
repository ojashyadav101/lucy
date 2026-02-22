# Mentions Issue Monitor — Learnings

## Setup (2026-02-14)
- Created by Ojash's request to monitor #mentions channel for issues
- Channel ID: C08QW134RKN (private channel)
- Cron runs every 2 minutes (changed from 10 min on Feb 15)
- State tracked in state.json to avoid duplicate processing

## Linear Workspace
- Team: "Mentions App" (85af8f8d-4910-44e1-a166-18ab63a1fd26)
- Key labels: Bug, Feature, Enhancement, Improvement, Task
- Default status: Backlog
- Issue identifiers follow MEN-XXX pattern

## Workflow
1. Read new messages from slack/mentions/ logs
2. Analyze if conversations describe actionable issues
3. Post in #mentions asking for approval to create Linear ticket
4. If approved, create issue with details + screenshots
5. Update state file

## Key Decisions
- Post approval requests in #mentions channel (not DMs) so the whole team sees
- Be conservative — only flag clear issues, not casual discussion
- Include screenshots as Linear attachments when available
- If an issue was already created by another bot (e.g., @Linear bot), don't duplicate — just update state

## Scripts
- `scripts/check_new_messages.py` — Parses slack/mentions/ logs, filters messages newer than state.json timestamp, groups by thread, excludes bot messages. Returns JSON with status, count, and message details. Use this as the first step in every run.

## Key Patterns
- When a user @mentions Viktor directly in a thread asking to create a ticket, the mention handler (separate agent) processes it. The cron should NOT duplicate that work — just update state to mark those messages as processed.
- Messages appear in both the main channel log AND thread logs — the script may return duplicates. Group by thread_ts/parent_thread_ts and deduplicate.
- Thread logs live at `slack/mentions/threads/{thread_ts}.log` and contain the full conversation context including the original message.
- The team also uses @Linear bot to create tickets directly. If a ticket was already created via Linear bot (look for "Created issue <link>" messages from `@bot:B090Q95BRML`), don't create a duplicate.
- Casual banter, jokes, and internal tooling discussions (e.g., which account to use for cursor) are NOT actionable issues for the Mentions App.

## Bot Users to Ignore
- `Viktor` / `viktor` — our own bot messages
- `bot:B090Q95BRML` — Linear bot (creates issues via @Linear mentions)

## Run History Summary
- **2026-02-14 12:20–12:40**: First runs. Auto cleanup issue discussed (thread 1770983993.681649). Ojash asked Viktor to create ticket (handled by mention handler). Shashwat created MEN-628 via @Linear bot. No duplicate action needed.
- **2026-02-14 14:20–14:50**: Casual activity — Shashwat's 1-year anniversary, Viktor rap song, party jokes. No issues.
- **2026-02-14 15:20–15:50**: MRR discussion, anniversary thread. No issues.
- **2026-02-14 18:45–18:50**: Polar API token discussion, rap rating banter. No Mentions App issues.
- **2026-02-15 00:XX–08:XX**: Casual banter — daily revenue report, crypto discussion, AI vs humans jokes. No issues.
- **2026-02-15 08:XX–2026-02-16 04:XX**: Channel quiet through Sunday night into early Monday. One casual message from Naman at ~4am IST asking "agent is live on prod?" — not actionable. State at ts 1771194838.849699.

## Activity Patterns
- Team is most active during Indian business hours (10am-7pm IST)
- Evenings/weekends: mostly casual banter, rarely actionable issues
- Late night/early morning (11pm-8am IST): channel typically quiet
- **No actionable Mentions App issues detected since monitoring started**
- **2026-02-16 05:05**: Only Viktor bot messages (daily revenue report + reply to Naman's "agent is live on prod?" thread). State advanced to 1771218138.984499.

## Script Note
- `check_new_messages.py` only filters out `Viktor`/`viktor` from main results. Thread-only replies from Viktor (not in main channel log) are found via thread log scanning but filtered correctly. The script works as expected — any misses come from needing to also check thread logs manually (which the script does).
