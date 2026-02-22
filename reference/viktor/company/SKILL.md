---
name: company
description: Company overview, what they do, key context about Serprisingly and the team.
---

# Company: Serprisingly

**Website:** https://serprisingly.com
**Industry:** Digital Marketing / SEO Agency
**Focus:** AI Search Optimization for B2B SaaS companies

## What They Do

Serprisingly is an AI Search Optimization Agency that helps B2B SaaS companies grow through:
- **Search Everywhere Optimization** — Google, AI (ChatGPT, Gemini, Perplexity), and Reddit
- **Technical SEO** — site speed, Core Web Vitals, indexing, schema, internal linking
- **Content Generation** — AI-optimized blogs, programmatic templates
- **Targeted Link Building** — backlinks, PR, shaping how AI models reference brands
- **Reddit Growth & Narrative Control**

## Business Model

- Premium agency model starting at $15,000+/month
- Selective client base ("not a volume agency")
- Work in bi-weekly sprints
- Full-stack growth team covering research, content, design, SEO, AEO, and Reddit

## Key Context

- Ojash (founder) comes from a background of scaling high-traffic, large-scale websites (ojash.com)
- The team uses ClickUp for project management (based on #clickup-notification channel)
- They have a technical product with a prompt queue system (based on #prompt-queue-worker-v2-prod-alerts channel)
- The #partner-serprisingly channel suggests partnerships or client collaboration

## Connected Integrations

- **Linear** — project/issue management
- **GitHub** — code repositories
- **Google Sheets** — spreadsheets, reporting
- **Clerk** — user authentication/management (likely for their SaaS product)
- **Bright Data** — web scraping, SERP data, competitor research
- **Google Search Console** — search performance analytics, indexing, sitemaps (⚠️ auth issue as of Feb 2026 — may need reconnection)
- **Polar** — payment/subscription platform for the Mentions product (MoR)

## Slack Channels

| Channel | Purpose |
|---------|---------|
| #general | General team communication |
| #clickup-notification | ClickUp task/project notifications |
| #prompt-queue-worker-v2-prod-alerts | Production alerts for their prompt queue system |
| #partner-serprisingly | Partner/client collaboration |

## Products

- **Mentions** — appears to be their SaaS product (separate from the agency). Has MRR targets of $50k and $100k. Uses Polar for payments and Clerk for auth. Shashwat is the "backend master" for this product.

## Revenue Tracking

- **MRR Target:** ₹50,000 (next milestone), then ₹100,000
- **Billing Platform:** Polar (api.polar.sh)
- **Daily Revenue Report:** Scheduled cron at `/reports/daily-revenue` — 9 AM IST Mon-Fri in #mentions
- **Historical Data:** Stored in `/work/data/revenue/` for delta calculations

## Notes

- Fresh workspace install (Feb 2026) — limited Slack history available
- Team is India-based (Asia/Kolkata timezone context)
- Polar integration is connected but has auth issues (as of Feb 2026) — needs reconnection
- Will be enriched as Viktor observes more conversations
