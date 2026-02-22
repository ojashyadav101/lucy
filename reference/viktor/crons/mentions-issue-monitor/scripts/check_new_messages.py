"""
Check for new messages in #mentions channel since last processed timestamp.
Returns a list of messages newer than state, grouped by thread.

Usage: uv run python crons/mentions-issue-monitor/scripts/check_new_messages.py
"""
import json
import os
import re
import sys
from pathlib import Path

STATE_FILE = "crons/mentions-issue-monitor/state.json"
CHANNEL_DIR = "slack/mentions"

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"last_processed_ts": "0", "processed_threads": [], "created_issues": []}

def parse_log_line(line):
    """Parse a Slack log line into timestamp, user, and message."""
    match = re.match(r'\[(\d+\.\d+)\]\s+@([^:]+):\s+(.*?)(?:\s+\[thread:(\d+\.\d+)\])?$', line.strip())
    if match:
        return {
            "ts": match.group(1),
            "user": match.group(2),
            "message": match.group(3),
            "thread_ts": match.group(4)
        }
    return None

def get_new_messages(last_ts):
    """Get all messages newer than last_ts from channel logs."""
    new_messages = []
    channel_dir = Path(CHANNEL_DIR)
    
    if not channel_dir.exists():
        return new_messages
    
    # Read main channel logs
    for log_file in sorted(channel_dir.glob("*.log")):
        with open(log_file) as f:
            for line in f:
                parsed = parse_log_line(line)
                if parsed and float(parsed["ts"]) > float(last_ts):
                    parsed["source"] = str(log_file)
                    new_messages.append(parsed)
    
    # Read thread logs
    threads_dir = channel_dir / "threads"
    if threads_dir.exists():
        for thread_file in sorted(threads_dir.glob("*.log")):
            thread_ts = thread_file.stem
            with open(thread_file) as f:
                for line in f:
                    parsed = parse_log_line(line)
                    if parsed and float(parsed["ts"]) > float(last_ts):
                        parsed["source"] = str(thread_file)
                        parsed["parent_thread_ts"] = thread_ts
                        new_messages.append(parsed)
    
    return sorted(new_messages, key=lambda m: float(m["ts"]))

def group_by_thread(messages):
    """Group messages by thread."""
    threads = {}
    for msg in messages:
        thread_key = msg.get("parent_thread_ts") or msg.get("thread_ts") or msg["ts"]
        if thread_key not in threads:
            threads[thread_key] = []
        threads[thread_key].append(msg)
    return threads

if __name__ == "__main__":
    state = load_state()
    last_ts = state.get("last_processed_ts", "0")
    
    messages = get_new_messages(last_ts)
    
    # Filter out bot messages
    messages = [m for m in messages if m["user"] not in ("Viktor", "viktor")]
    
    if not messages:
        print(json.dumps({"status": "no_new_messages", "count": 0, "last_ts": last_ts}))
        sys.exit(0)
    
    threads = group_by_thread(messages)
    
    output = {
        "status": "new_messages",
        "count": len(messages),
        "thread_count": len(threads),
        "last_ts": last_ts,
        "newest_ts": messages[-1]["ts"],
        "messages": messages,
        "threads": {k: v for k, v in threads.items()}
    }
    
    print(json.dumps(output, indent=2))
