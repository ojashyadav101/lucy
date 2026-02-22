"""
Daily Mentions Revenue Report for #mentions channel.
Runs at 9 AM IST Mon-Fri.
Monday includes weekly recap.
All values in USD ($).
Read-only Polar API access.
"""
import asyncio
import json
import csv
import io
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path("/work/data/polar_snapshots")
DATA_DIR.mkdir(parents=True, exist_ok=True)

MRR_TARGET = 50_000  # $50,000 MRR target
MENTIONS_CHANNEL = "C08QW134RKN"


async def fetch_subscriptions(max_retries=3, delay=10):
    from sdk.tools.custom_api_vvpjfwhokmnwxs2d5xgqpm import custom_api_polar_get
    for attempt in range(1, max_retries + 1):
        try:
            result = await custom_api_polar_get("/v1/subscriptions/export")
            content = json.loads(result.get('content', '{}'))
            if content.get('status_code') != 200:
                raise Exception(f"Polar API error: {content}")
            body = content.get('body', '')
            return list(csv.DictReader(io.StringIO(body)))
        except Exception as e:
            if attempt < max_retries:
                print(f"⚠️ Attempt {attempt}/{max_retries} failed: {e}. Retrying in {delay}s...")
                await asyncio.sleep(delay)
                delay *= 2  # exponential backoff
            else:
                raise Exception(f"All {max_retries} attempts failed. Last error: {e}")


def compute_metrics(rows):
    total_mrr = 0
    active_count = 0
    monthly_count = 0
    yearly_count = 0
    by_plan = {}

    for row in rows:
        if row.get('Active', '').lower() != 'true':
            continue
        active_count += 1
        price = float(row.get('Price', 0))
        interval = row.get('Interval', '').lower()
        product = row.get('Product', 'Unknown')

        if interval == 'year':
            mrr_contribution = price / 12
            yearly_count += 1
        else:
            mrr_contribution = price
            monthly_count += 1

        total_mrr += mrr_contribution
        if product not in by_plan:
            by_plan[product] = {'count': 0, 'mrr': 0.0}
        by_plan[product]['count'] += 1
        by_plan[product]['mrr'] += mrr_contribution

    return {
        'total_mrr': total_mrr,
        'active_count': active_count,
        'monthly_count': monthly_count,
        'yearly_count': yearly_count,
        'by_plan': by_plan,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }


def save_snapshot(metrics, date_str):
    with open(DATA_DIR / f"{date_str}.json", 'w') as f:
        json.dump(metrics, f, indent=2)


def load_snapshot(date_str):
    path = DATA_DIR / f"{date_str}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


def build_plan_table(by_plan, prev_by_plan=None):
    lines = []
    for plan, data in sorted(by_plan.items(), key=lambda x: -x[1]['mrr']):
        if data['mrr'] == 0 and data['count'] <= 1:
            continue
        delta = ""
        if prev_by_plan:
            old = prev_by_plan.get(plan, {'mrr': 0, 'count': 0})
            md = data['mrr'] - old['mrr']
            cd = data['count'] - old['count']
            parts = []
            if md != 0:
                parts.append(f"{'+'if md>0 else ''}${md:,.0f}")
            if cd != 0:
                parts.append(f"{'+'if cd>0 else ''}{cd}")
            if parts:
                delta = f"  ({', '.join(parts)})"
        name = plan[:22].ljust(22)
        lines.append(f"{name} {data['count']:>3} subs  ${data['mrr']:>10,.2f}/mo{delta}")
    return "\n".join(lines)


def build_report(today, yesterday, date_str, is_monday=False, last_monday=None):
    blocks = []
    day_label = datetime.strptime(date_str, "%Y-%m-%d").strftime("%A, %b %d %Y")

    # Header
    blocks.append({"type": "header", "text": {"type": "plain_text", "text": f"📊 Mentions Daily Revenue — {day_label}"}})

    # MRR + deltas
    mrr = today['total_mrr']
    remaining = MRR_TARGET - mrr
    pct = (mrr / MRR_TARGET) * 100

    text = f"*MRR: ${mrr:,.2f}*\n"
    if yesterday:
        d_mrr = mrr - yesterday['total_mrr']
        d_subs = today['active_count'] - yesterday['active_count']
        arrow_m = ":chart_with_upwards_trend:" if d_mrr >= 0 else ":chart_with_downwards_trend:"
        arrow_s = ":arrow_up:" if d_subs >= 0 else ":arrow_down:"
        text += f"{arrow_m} {'+'if d_mrr>=0 else ''}${d_mrr:,.2f} vs yesterday\n"
        text += f"{arrow_s} {'+'if d_subs>=0 else ''}{d_subs} subscribers ({today['active_count']} total)\n"
    else:
        text += f"{today['active_count']} active subscribers ({today['monthly_count']} monthly, {today['yearly_count']} yearly)\n"

    text += f"\n:dart: *${remaining:,.2f} to $50k target* ({pct:.1f}%)"
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})

    blocks.append({"type": "divider"})

    # Plan breakdown
    prev_plans = yesterday['by_plan'] if yesterday else None
    table = build_plan_table(today['by_plan'], prev_plans)
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Revenue by plan:*\n```\n{table}\n```"}})

    # Monday weekly recap
    if is_monday and last_monday:
        blocks.append({"type": "divider"})
        w_mrr = mrr - last_monday['total_mrr']
        w_subs = today['active_count'] - last_monday['active_count']
        arrow_wm = ":chart_with_upwards_trend:" if w_mrr >= 0 else ":chart_with_downwards_trend:"
        arrow_ws = ":arrow_up:" if w_subs >= 0 else ":arrow_down:"

        recap = (
            f":calendar: *Weekly Recap (vs last Monday):*\n"
            f"{arrow_wm} MRR: {'+'if w_mrr>=0 else ''}${w_mrr:,.2f}\n"
            f"{arrow_ws} Subscribers: {'+'if w_subs>=0 else ''}{w_subs}\n"
        )

        changes = []
        all_plans = set(list(today['by_plan'].keys()) + list(last_monday.get('by_plan', {}).keys()))
        for plan in sorted(all_plans):
            cur = today['by_plan'].get(plan, {'mrr': 0, 'count': 0})
            old = last_monday.get('by_plan', {}).get(plan, {'mrr': 0, 'count': 0})
            dm = cur['mrr'] - old['mrr']
            dc = cur['count'] - old['count']
            if dm != 0 or dc != 0:
                changes.append((plan, dm, dc))

        if changes:
            recap += "\n*Plan changes:*\n"
            for plan, dm, dc in sorted(changes, key=lambda x: -abs(x[1])):
                recap += f"• {plan}: {'+'if dm>=0 else ''}${dm:,.0f}, {'+'if dc>=0 else ''}{dc} subs\n"

        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": recap}})

    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"Polar API • Read-only • {date_str}"}]})
    return blocks


async def main():
    from sdk.tools.default_tools import coworker_send_slack_message

    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    today_str = now_ist.strftime("%Y-%m-%d")
    weekday = now_ist.weekday()  # 0=Monday
    is_monday = weekday == 0

    # Fetch data
    rows = await fetch_subscriptions()
    today = compute_metrics(rows)
    save_snapshot(today, today_str)

    # Yesterday (skip weekends on Monday)
    days_back = 3 if is_monday else 1
    yesterday_str = (now_ist - timedelta(days=days_back)).strftime("%Y-%m-%d")
    yesterday = load_snapshot(yesterday_str)

    # Last Monday for weekly recap
    last_monday = None
    if is_monday:
        last_monday = load_snapshot((now_ist - timedelta(days=7)).strftime("%Y-%m-%d"))

    blocks = build_report(today, yesterday, today_str, is_monday, last_monday)

    await coworker_send_slack_message(
        channel_id=MENTIONS_CHANNEL,
        blocks=blocks,
        reflection="Daily revenue report from Polar API. All USD. Read-only.",
        do_send=True,
    )
    print(f"✅ Report sent for {today_str}. MRR: ${today['total_mrr']:,.2f}")


if __name__ == "__main__":
    asyncio.run(main())
