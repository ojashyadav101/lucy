"""Monitor for Viktor's PR 10-13 delivery via GitHub PRs and Downloads folder."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

DOWNLOADS = Path(os.path.expanduser("~/Downloads"))
REPO_DIR = Path(__file__).resolve().parent.parent
POLL_INTERVAL = 30  # seconds
KNOWN_PATCHES = {
    "0001-feat-prompt-deep-rewrite-of-system-prompt-and-SOUL.m.patch",
    "0001-feat-agent-intent-based-skill-loading-for-context-en.patch",
    "0001-feat-router-thread-aware-model-selection-action-verb.patch",
    "0001-refactor-handlers-remove-90-lines-of-dead-code.patch",
    "viktor_vs_lucy_comparison.pdf",
}
KNOWN_FOLDERS = {"Victor round 2", "Viktor Docs", "viktor_workspace_export"}

START_TIME = time.time()
MAX_WAIT = 25 * 60  # 25 minutes max


def check_github_prs() -> list[dict]:
    """Check for new open PRs on the repo."""
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--json", "number,title,author,createdAt,url"],
            capture_output=True, text=True, cwd=REPO_DIR, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except Exception as e:
        print(f"  [warn] GitHub check failed: {e}")
    return []


def check_new_downloads() -> list[Path]:
    """Check for new patch files or folders in Downloads."""
    new_items = []
    for item in DOWNLOADS.iterdir():
        if item.name.startswith("."):
            continue
        if item.name in KNOWN_PATCHES or item.name in KNOWN_FOLDERS:
            continue
        if item.suffix in (".patch", ".pdf", ".md") or item.is_dir():
            mtime = item.stat().st_mtime
            if mtime > START_TIME - 60:
                new_items.append(item)
    return sorted(new_items, key=lambda p: p.stat().st_mtime, reverse=True)


def check_slack_bot_messages() -> list[dict]:
    """Check if any new messages appeared in channels the bot can access."""
    try:
        keys = json.loads((REPO_DIR / "keys.json").read_text())
        bot_token = keys["slack"]["bot_token"]
        import httpx
        resp = httpx.get(
            "https://slack.com/api/conversations.history",
            params={"channel": "C0AEZ241C3V", "limit": 5},
            headers={"Authorization": f"Bearer {bot_token}"},
        ).json()
        if resp.get("ok"):
            return resp.get("messages", [])
    except Exception as e:
        print(f"  [warn] Slack check failed: {e}")
    return []


def main():
    print(f"[monitor] Starting Viktor delivery monitor at {time.strftime('%H:%M:%S')}")
    print(f"[monitor] Watching: GitHub PRs, Downloads folder, Slack #lucy-my-ai")
    print(f"[monitor] Will poll every {POLL_INTERVAL}s for up to {MAX_WAIT // 60} minutes")
    print()

    cycle = 0
    while time.time() - START_TIME < MAX_WAIT:
        cycle += 1
        print(f"[poll #{cycle}] {time.strftime('%H:%M:%S')} — checking...")

        prs = check_github_prs()
        if prs:
            print(f"  >>> GITHUB PRs FOUND: {len(prs)} open PRs!")
            for pr in prs:
                print(f"      PR #{pr['number']}: {pr['title']} by {pr['author']['login']}")
                print(f"      URL: {pr['url']}")
            print("\n  !!! VIKTOR DELIVERY DETECTED VIA GITHUB !!!")
            return "github", prs

        new_downloads = check_new_downloads()
        patches = [f for f in new_downloads if f.suffix == ".patch"]
        if patches:
            print(f"  >>> NEW PATCHES IN DOWNLOADS: {len(patches)} files!")
            for p in patches:
                print(f"      {p.name} ({p.stat().st_size} bytes)")
            new_folders = [f for f in new_downloads if f.is_dir()]
            if new_folders:
                for d in new_folders:
                    print(f"      [folder] {d.name}/")
            print("\n  !!! VIKTOR DELIVERY DETECTED VIA DOWNLOADS !!!")
            return "downloads", [str(p) for p in new_downloads]

        print(f"  No new deliveries. Waiting {POLL_INTERVAL}s...")
        time.sleep(POLL_INTERVAL)

    print(f"\n[monitor] Timed out after {MAX_WAIT // 60} minutes.")
    return "timeout", []


if __name__ == "__main__":
    source, items = main()
    print(f"\nResult: source={source}, items={items}")
