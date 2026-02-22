# Heartbeat Learnings

## Setup
- **First heartbeat**: 2026-02-14 (Saturday, Valentine's Day)
- **Viktor installed**: 2026-02-14 by Ojash (Founder, Serprisingly)
- **Timezone**: Asia/Kolkata (IST) — team is India-based
- **Company**: Serprisingly — AI Search Optimization Agency for B2B SaaS ($15k+/mo)

## Active Crons
- `/mentions-issue-monitor` — Monitors #mentions (C08QW134RKN) every 2 min for bugs/issues, proposes Linear tickets
- `/heartbeat` — This cron (proactive check-ins, 4x daily: 10:00, 13:00, 16:00, 19:00 IST)
- `/reports/daily-revenue` — Daily MRR report at 9:00 AM IST Mon-Fri in #mentions (Polar API)
- `/channel_introductions` — Introduces Viktor to channels (3:30 PM IST Mon-Fri, 3 runs then self-deletes)
- `/workflow_discovery` — Discovers automation opportunities (Mon & Thu, 2:30 PM IST)

## Pending Follow-ups

### DONE: First Monday revenue report (Feb 16, 9 AM IST)
- Cron FAILED at 9 AM due to httpx connection timeout (Polar API transient network error)
- Manually recovered at ~10:30 AM IST (heartbeat ran it)
- Script updated with retry logic (3 attempts, exponential backoff) + fixed deprecation warnings
- Report showed: MRR $18,595.67 / 190 subs (down $49, -1 sub vs Sunday snapshot)
- Lost 1 Starter Monthly subscriber ($49) over the weekend
- No Friday baseline existed, so Monday report showed absolute numbers (expected)
- Tomorrow's report will have today's snapshot for daily deltas ✅

### LOW: Clerk integration auth broken
- Clerk secret key is invalid (not in sk_live_/sk_test_ format)
- Documented in skills/integrations/clerk/SKILL.md
- Mention to Ojash if they try to use Clerk features

### LOW: Google Sheets built-in actions broken
- OAuth token error on all Pipedream actions
- Proxy endpoints work fine (documented in skills)
- Not urgent unless team needs Google Sheets work

## Resolved Items
- ✅ **Revenue report recovery**: Feb 16 report sent manually after cron failure. Script hardened with retries.
- ✅ **Monitoring frequency**: Ojash chose option 3 (2-min cron + @mention). Set up and working.
- ✅ **Polar MRR**: Working via `/v1/subscriptions/export` endpoint.
- ✅ **Daily revenue report**: Cron set up at 9 AM IST Mon-Fri in #mentions, all in USD
- ✅ **Corrected Linear ticket draft**: Moot — Shashwat used native Linear bot, MEN-628 created

## Team Dynamics & Preferences
- **Casual, fun team** — they joke around in #mentions (Naman: "Victor ki job khaa gya linear 😔", Shashwat: "viktor lasted 10 minutes")
- **Naman & Shashwat** are the most active in #mentions — they're developers working on the product
- **Shashwat** prefers native Linear bot for quick tickets — Viktor should focus on richer issue creation
- **Naman** complimented Viktor multiple times — positive reception. Posted "agent is live on prod? 🫡" at 4 AM IST (late night/early morning dev)
- **Pankaj** knows Viktor, playful ribbing ("AI ab pakka job khayega")
- **Ojash** values practical results, direct communication. Got impatient during Polar API troubleshooting — minimize back-and-forth
- **Ojash asked about cost**: Feb 15 11:40 PM IST. Viktor answered honestly (no visibility into billing, suggested Coworker dashboard).
- **Shashwat's 1-year anniversary**: Celebrated on Feb 14, Viktor wrote a rap (7/10 context, 2/10 rap per Naman)

## Infrastructure Notes
- **Prompt Queue system**: Monitored in #prompt-queue-worker-v2-prod-alerts
  - Latest stats (Feb 16 9:30 AM IST): Overall 98.6% success rate
  - dataforseo: 100% ✅, openrouter: 100% ✅
  - brightdata: 98.4% ⚠️ (occasional webhook processing errors)
  - **oxylabs: 93.5%** ⚠️ — improved from ~89% (Sunday) but still weakest. Request timeouts on gpt-4o-mini.
  - Alert volume: Feb 14: 66, Feb 15: 160, Feb 16: 62 (as of 10:30 AM)
  - Trend: oxylabs improving but needs monitoring. If still <95% by Wednesday, consider flagging.
- **Linear workspace**: "Mentions App" team (MEN-* prefix), 17 projects, 8 users
- **Polar API**: 
  - Working endpoint: `/v1/subscriptions/export` (CSV export, read-only)
  - Broken: Built-in Pipedream proxy (strips trailing slashes, causes redirects)
  - Custom API connection set up with personal access token (read-only)
  - Current MRR: $18,595.67 (190 subs) — target $50k
- **Revenue report script**: Now has retry logic (3 attempts, exponential backoff). Fixed datetime deprecation warnings.
- **Vercel**: 7 projects (mostly Next.js), Hobby plan, no custom domains

## Heartbeat Strategy
- **Weekday mornings**: Check for overnight messages, verify crons ran, follow up on pending items
- **Weekday afternoons**: Light check, react to messages, check cron outputs
- **Weekday evenings**: Wrap up day, note anything for next day
- **Weekends/holidays**: Very light touch — reactions only, no proactive outreach unless urgent

## Patterns to Watch
- [ ] Oxylabs: 93.5% and improving. If still <95% by Wednesday, flag to Naman/Shashwat
- [ ] Revenue report: Watch Tuesday's cron at 9 AM IST — first run with retry logic
- [ ] Monday 2:30 PM IST: workflow discovery cron — check discovery.md for results
- [ ] Monday 3:30 PM IST: channel introductions cron — check execution.log for first channel
- [ ] Alert volume Monday: 62 alerts in first 5 hours, pace ~200+ if continues. Compare to Sunday (160 total).
