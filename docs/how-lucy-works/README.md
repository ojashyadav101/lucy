# How Lucy Works

> **Practical, in-depth explanations of Lucy's core systems.**
>
> These documents go deeper than the high-level architecture overview —
> they explain the *why* behind each design decision, the failure modes
> each system was built to prevent, and exactly how things connect in code.

---

## Documents in This Folder

| Document | What It Covers |
|----------|----------------|
| [TEST-FIX-RETRY-FRAMEWORK.md](./TEST-FIX-RETRY-FRAMEWORK.md) | The universal testing system: why hallucinated completion happens, how the 7-stage escalation ladder works, script validation, pass/fail detection, and the full test coverage reference |

---

## How This Differs from `docs/`

The main `docs/` folder contains system reference documentation — what each
module does, what parameters it accepts, what constants are in use.

This folder focuses on **operational understanding**:

- Why a system exists (the failure mode it prevents)
- How it interacts with the agent loop
- What the exact code path looks like
- What tests confirm it's working
- How to reason about it when debugging

---

## Related Reference Documents

| Document | What It Covers |
|----------|----------------|
| [docs/AGENT_LOOP.md](../AGENT_LOOP.md) | Full agent loop mechanics, supervisor checkpoints, sub-agents, model escalation |
| [docs/ARCHITECTURE.md](../ARCHITECTURE.md) | System overview, package layout, model tier strategy |
| [docs/CRONS_HEARTBEAT.md](../CRONS_HEARTBEAT.md) | Cron scheduler, heartbeat monitors, job lifecycle |
| [docs/BEHAVIOR_GUIDE.md](../BEHAVIOR_GUIDE.md) | Decision logic: emojis, progress messages, personality |
