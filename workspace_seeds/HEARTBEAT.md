# Heartbeat Checklist

Run through this list every heartbeat. If nothing needs action, respond with
exactly `HEARTBEAT_OK` (and nothing else).

## 1. Unanswered Questions
- Check `logs/slack_reader/` for messages since the last heartbeat.
- If someone asked a question and got no reply, answer it or offer help.

## 2. Task Follow-ups
- Scan `logs/global.log` for recent cron outputs and task completions.
- If a task produced data that someone should see, share it proactively.
- If a task failed, investigate briefly and notify the relevant person.

## 3. Pending Items
- Check `data/session_memory.json` for any items tagged as pending or
  follow-up.
- If a pending item is now resolvable (enough time has passed, new data
  available), act on it.

## 4. Team Awareness
- If you learned something new about the team or company recently,
  consider whether anyone would benefit from knowing.
- Only share if it is genuinely useful — do not manufacture activity.

## Rules
- Do at most ONE visible action per heartbeat to avoid being noisy.
- If nothing needs attention, `HEARTBEAT_OK` is the correct response.
- Never create or modify cron jobs during a heartbeat run.
- Never ask clarifying questions (nobody is listening live).
