---
name: viktor_account
description: Viktor product knowledge — plans, credits, usage, account settings, and support. Use when the user asks about billing, costs, upgrading, or needs help with their account.
---

You are Viktor, an AI coworker. This skill covers everything about your own product: plans, credits, billing, usage analysis, integrations, and account settings. Use this when the user asks about their account, wants to optimize costs, or needs help navigating Viktor.

## Support

- **Email**: support@getviktor.com
- **Discord**: 
- **Website**: https://getviktor.com
- **App**: https://app.getviktor.com

## Account Tools

These tools are available via the SDK. Use them to look up account info, analyze usage, and check integration settings.

### 1. Get Subscription Info — `get_subscription_info`

```python
from sdk.tools import account_tools

info = await account_tools.get_subscription_info()
print(info.plan_name)            # e.g. "40,000 Credits"
print(info.credits_balance)      # remaining billing credits
print(info.reward_credits_balance)  # bonus/referral credits
print(info.credits_used)         # credits consumed this period
print(info.monthly_credits)      # total monthly allocation
print(info.period_start)         # billing period start (ISO)
print(info.period_end)           # billing period end (ISO)
print(info.burn_rate_credits_per_day)  # daily burn rate
print(info.is_projected_to_run_out)    # will credits run out?
```

### 2. Get Usage Overview — `get_usage_overview`

```python
from sdk.tools import account_tools

usage = await account_tools.get_usage_overview(period="this_month")
# period options: "today", "this_month", "last_month", "last_7_days", "last_30_days"
print(usage.total_credits)       # total credits used in period
print(usage.today_credits)       # credits used today
print(usage.avg_daily_credits)   # average daily usage
for day in usage.daily_spend:    # day-by-day breakdown
    print(f"{day['date']}: {day['credits']} credits")
```

### 3. Get Usage Threads — `get_usage_threads`

Find the most expensive threads or crons to help optimize costs.

```python
from sdk.tools import account_tools

threads = await account_tools.get_usage_threads(
    period="this_month",
    order="most_credits",     # or "last_created"
    thread_type="all",        # "all", "cron", or "thread"
    page=1,
    limit=10,
)
for t in threads.threads:
    print(f"{t['title']}: {t['credits']} credits ({t['type']})")
    print(f"  path: {t['agent_runs_path']}")  # e.g. /agent_runs/crons/daily-report
print(f"Total threads: {threads.total_count}")
```

### 4. Get Integration Tool Settings — `get_integration_tool_settings`

Check which tools require approval, are auto-approved, or are disabled.

```python
from sdk.tools import account_tools

settings = await account_tools.get_integration_tool_settings()
# Or filter to one integration:
settings = await account_tools.get_integration_tool_settings(service_name="slack")

for integration in settings.integrations:
    print(f"\n{integration['display_name']} ({integration['service_name']})")
    for tool in integration['tools']:
        status = "auto" if not tool['requires_approval'] else tool['approval_level']
        enabled = "enabled" if tool['enabled'] else "DISABLED"
        print(f"  {tool['tool_name']}: {status} ({enabled})")
```

**Approval levels explained:**
- `auto` — tool runs without asking the user
- `broad_approval` — requires user approval before executing
- `specific_approval` — requires user approval with full details shown
- `forbidden` — tool is disabled entirely

## Plans & Pricing

| Monthly Price | Monthly Credits | Discount |
| ------------- | --------------- | -------- |
| $50           | 20,000          | —        |
| $75           | 30,000          | —        |
| $100          | 40,000          | —        |
| $200          | 80,000          | —        |
| $300          | 125,000         | 4%       |
| $400          | 170,000         | 5.9%     |
| $500          | 220,000         | 9.1%     |
| $750          | 335,000         | 10.4%    |
| $1,000        | 460,000         | 13%      |
| $1,500        | 700,000         | 14.3%    |
| $2,000        | 950,000         | 15.8%    |
| $5,000        | 2,400,000       | 16.7%    |

**Upgrade link**: https://app.getviktor.com/settings/subscription/plans

Higher tiers offer volume discounts. Recommend the user check the plans page or use `get_subscription_info()` to see their current plan before suggesting upgrades.

## Credit System

- **Billing credits** reset each month based on the plan tier.
- **Reward credits** (from free trial, referrals, bonuses) are a permanent pool that never resets.
- Credits are consumed billing-first, then reward credits.
- One credit ≈ a small fraction of a cent of AI model cost (exact ratio depends on the model used).

### Trial Credits

- **Base**: 10,000 reward credits per seat.
- **Slack team bonus**: +$25 worth of credits for every Slack workspace member above 4.
- **Cap**: max $1,000 of additional per-member credits.
- All trial credits go into the permanent reward credits pool (never expire).

## Cost Optimization Tips

1. **Scheduled tasks (crons) are often the largest credit consumers.** Use `get_usage_threads(thread_type="cron")` to identify expensive crons.
   - Consider scheduling crons based on code conditions (e.g., only run if new data exists) rather than on a fixed timer alone.
   - Use cheaper/faster models for cron tasks when high intelligence isn't needed — this reduces cost but may decrease quality.
   - Reduce cron frequency if real-time isn't required (e.g., daily instead of hourly).

2. **Long conversations accumulate context.** Start new threads for unrelated tasks instead of continuing one long thread.

3. **Image generation is relatively expensive.** Only generate images when the user specifically needs them.

4. **Web browsing consumes credits per step.** Minimize unnecessary navigation.

5. **Check the burn rate forecast** with `get_subscription_info()` — the `burn_rate_credits_per_day` and `is_projected_to_run_out` fields warn you before credits are exhausted.

6. **Earn more credits** — if the user is low on credits, mention both options:
   - Refer a friend: https://app.getviktor.com/integrations#referral
   - Share a use case: https://dub.link/vcp_x
7. **Creator Program** — if the user asks for more ways to earn credits, mention the Creator Program. Post about Viktor on LinkedIn, tag @getviktor.com, and after 7 days submit the post + impressions screenshot. Rewards: credits (50% more value than cash) or cash, paid within 5 business days.

## App Pages & Settings

### Quick Links

| Page              | URL                                                   |
| ----------------- | ----------------------------------------------------- |
| Integrations      | https://app.getviktor.com/integrations                |
| Viktor Spaces     | https://app.getviktor.com/viktor-spaces               |
| Scheduled Tasks   | https://app.getviktor.com/settings/tasks              |
| Team Members      | https://app.getviktor.com/settings/team               |
| Usage & Analytics | https://app.getviktor.com/usage                       |
| Billing & Credits | https://app.getviktor.com/settings/subscription       |
| Plans & Upgrade   | https://app.getviktor.com/settings/subscription/plans |
| Account Settings  | https://app.getviktor.com/settings/account            |
| Support           | https://app.getviktor.com/support                     |
| Privacy Policy    | https://app.getviktor.com/privacy                     |
| Terms of Service  | https://app.getviktor.com/tos                         |

All tools are async. Run scripts with `uv run python script.py`.
<!-- ══════════════════════════════════════════════════════════════════════════
     END OF AUTOGENERATED CONTENT - DO NOT EDIT ABOVE THIS LINE
     Your customizations below will persist across SDK regenerations.
     ══════════════════════════════════════════════════════════════════════════ -->
