# Slack UX Implementation Summary

This document summarizes the integration of advanced Slack User Experience (UX) features into Lucy, focusing on Block Kit components for approvals and OAuth connection flows.

## Core Components (`src/lucy/slack/blocks.py`)

We utilize Slack's Block Kit framework to create interactive, rich messages. The `LucyMessage` class acts as a factory for these standardized block templates:

### 1. Simple Responses (`simple_response`, `error`, `help`, `status`)
-   Standard text responses, status reports, and error messages.
-   Provides consistent formatting and emoji usage across the bot.

### 2. Thinking State (`thinking`)
-   Displays a loading GIF and a custom "processing" message.
-   Provides immediate visual feedback while background tasks (e.g., OpenClaw execution) are running.

### 3. Task Confirmation & Results (`task_confirmation`, `task_result`)
-   **Confirmation:** Acknowledges complex tasks with an estimated time and a "View Details" action button.
-   **Result:** Summarizes the final output of a task, optionally including duration and follow-up action buttons.

### 4. Human-in-the-Loop Approvals (`approval_request`)
-   **Purpose:** Triggers when Lucy attempts a high-risk action (e.g., sending an email, modifying a database record) that requires human confirmation.
-   **Design:** Includes the action description, risk level (with visual indicators 🟢🟡🔴⚠️), requester name, and bold "Approve" (Primary/Green) and "Reject" (Danger/Red) buttons.
-   **Actions:** Sends `lucy_action_approve:[id]` or `lucy_action_reject:[id]` payloads back to the server.

### 5. OAuth Connection Flows (`connection_request`)
-   **Purpose:** Prompts the user to authenticate a third-party tool (e.g., GitHub, Linear) via Composio directly from Slack.
-   **Design:** Clear instructions that the connection is secure and isolated to the workspace, alongside a primary "Connect [Provider]" link button.
-   **Actions:** Triggers the OAuth flow via the provided URL.

## Event Handlers (`src/lucy/slack/handlers.py`)

The UX components are wired into the Slack Bolt event handlers:

### Interactive Actions (`@app.action`)
-   **Regex Matching:** Captures all interactions starting with `lucy_action_.*`.
-   **Approval Handling:** When an `Approve` or `Reject` button is clicked, it queries the `Approval` database table, updates its status (`APPROVED` or `REJECTED`), updates the associated `Task` status (e.g., changing from `PENDING_APPROVAL` to `RUNNING`), and sends a confirmation message to the user.

### Slash Commands (`@app.command("/lucy")`)
-   **Connection Trigger:** The `/lucy connect <provider>` command initiates the OAuth flow.
-   **Execution:** 
    1.  Acknowledges the command and sends a "thinking" message.
    2.  Calls `ComposioClient.create_connection_link(workspace_id, app)` to generate a secure, workspace-scoped OAuth URL.
    3.  Sends the `connection_request` Block Kit message with the generated URL.

## Architecture Highlights

1.  **Non-Blocking UX:** Slack commands and mentions instantly acknowledge with a `thinking` state using `asyncio.create_task`, preventing timeout errors from Slack's 3-second requirement while heavy LLM operations run in the background.
2.  **Stateless Interactions:** All action buttons include necessary context (e.g., `approval_id`) in their `value` payload, allowing the Slack handler to remain stateless and reconstruct the context from the database.
3.  **Unified Block Factory:** Centralizing all message layouts in `blocks.py` ensures a consistent visual language and simplifies updates across the application.
