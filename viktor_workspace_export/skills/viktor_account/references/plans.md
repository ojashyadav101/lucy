# Viktor Plans & Pricing

## How Credits Work

Viktor uses a credit-based pricing system. Every AI action (messages, tool calls, crons, image generation, web browsing) consumes credits. Credits reset monthly at the start of each billing period.

### Credit Types

1. **Billing Credits** — Monthly allocation from the plan. Resets each billing cycle.
2. **Reward Credits** — Granted during free trial, earned from referrals and promotions. Never reset, permanent balance.

Credits are consumed in order: billing credits first, then reward credits.

### Trial

New users receive a trial with:

1. **Base trial credits**: 10,000 reward credits per seat.
2. **Slack team bonus**: +$25 worth of credits for every Slack workspace member above 4.
3. **Bonus cap**: max $1,000 of additional per-member credits.

All trial credits are added to the reward credits pool (permanent, never expire). After the trial ends, users must choose a plan to continue.

## Available Plans

All plans are per-workspace (not per-seat for Viktor).

| Package ID      | Monthly Price (USD) | Monthly Credits | Volume Discount |
| --------------- | ------------------- | --------------- | --------------- |
| credits_20000   | $50                 | 20,000          | —               |
| credits_30000   | $75                 | 30,000          | —               |
| credits_40000   | $100                | 40,000          | —               |
| credits_80000   | $200                | 80,000          | —               |
| credits_125000  | $300                | 125,000         | 4.0%            |
| credits_170000  | $400                | 170,000         | 5.9%            |
| credits_220000  | $500                | 220,000         | 9.1%            |
| credits_335000  | $750                | 335,000         | 10.4%           |
| credits_460000  | $1,000              | 460,000         | 13.0%           |
| credits_700000  | $1,500              | 700,000         | 14.3%           |
| credits_950000  | $2,000              | 950,000         | 15.8%           |
| credits_2400000 | $5,000              | 2,400,000       | 16.7%           |

### Volume Discounts

Higher tiers offer progressively better per-credit pricing. The discount percentage indicates savings compared to the base rate ($50 / 20,000 credits = $0.0025/credit).

For example:
- $50/month → $0.00250 per credit (base)
- $300/month → $0.00240 per credit (4% discount)
- $1,000/month → $0.00217 per credit (13% discount)
- $5,000/month → $0.00208 per credit (16.7% discount)

### Upgrading

Users can upgrade at any time from the plans page:
**https://app.getviktor.com/settings/subscription/plans**

When upgrading mid-cycle, Stripe prorates the difference. The new credit allocation takes effect immediately.

### Billing Portal

For invoice history, payment method changes, and billing address updates, direct users to the Stripe billing portal:
**https://app.getviktor.com/settings/subscription/billing-portal**

## Enterprise

Enterprise plans have custom credit allocations and pricing. Contact support@getviktor.com for enterprise inquiries.

## Referral Credits

Users can earn reward credits through referrals. Each successful referral grants 10,000 reward credits (equivalent to $25 of usage). Reward credits are permanent and never expire.

- Refer a friend: https://app.getviktor.com/integrations#referral
- Share a use case to earn credits: https://dub.link/vcp_x
