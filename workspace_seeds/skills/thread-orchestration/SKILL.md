---
name: thread-orchestration
description: Manage long-running Slack threads with status updates, progress tracking, and structured reporting. Use when handling complex multi-step tasks that span multiple messages.
---

# Thread Orchestration

When handling complex tasks that take multiple steps, keep the user informed with structured thread updates.

## Pattern: Progress Thread

For multi-step tasks:

1. **Acknowledge immediately**: Reply that you're working on it
2. **Post progress updates**: As each step completes, update the thread
3. **Final summary**: Post a structured result when done

Example flow:
```
User: Can you analyze our Q1 performance and create a report?

Lucy: On it — I'll analyze the data and put together a report. This might take a few minutes.

Lucy: ✓ Pulled revenue data from the last 90 days
Lucy: ✓ Analyzed growth trends and identified top performers
Lucy: ✓ Compared against Q4 benchmarks

Lucy: Here's your Q1 Performance Report:
[structured report with findings, charts description, and recommendations]
```

## Pattern: Approval Flow

For destructive or high-impact actions:

1. **Describe what you're about to do**: Be specific about the action
2. **Ask for confirmation**: Use a clear yes/no question
3. **Wait for approval**: Do NOT proceed until confirmed
4. **Execute and report**: Confirm completion

## Pattern: Multi-Person Thread

When a task involves multiple people:

1. Tag relevant people when their input is needed
2. Track who has responded and who hasn't
3. Summarize collected inputs before proceeding
4. Keep everyone updated on the final outcome

## Anti-Patterns

- Don't send 10 rapid-fire messages — batch updates when possible
- Don't leave a thread hanging without resolution
- Don't assume silence means approval for destructive actions
- Don't repeat information the user already provided
