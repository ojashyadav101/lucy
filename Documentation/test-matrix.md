# Lucy Test Harness Matrix

This document provides a comprehensive test matrix for verifying the end-to-end integration of Lucy, from Slack to the OpenClaw gateway, including the database, memory, routing, and integration layers.

## 1. What We Have Built So Far (Completed)

- **Database Foundation:** PostgreSQL schema with Workspaces, Users, Channels, Tasks, Approvals, and CostLog tables, including multi-tenant scoping and JSONB flexibility. Alembic migrations and async SQLAlchemy ORM.
- **Slack Bolt & FastAPI App:** Core app infrastructure running in Socket Mode (or HTTP mode), with robust lifecycle management.
- **Slack Middleware:** Lazy onboarding interceptors (`resolve_workspace_middleware`, `resolve_user_middleware`) to automatically create DB records for new workspaces and users from Slack events.
- **Slack Event Handlers:** Parsing logic for `@app_mention`, direct messages (`im`), slash commands (`/lucy`), and Block Kit actions (`lucy_action_`*). Non-blocking task execution architecture.
- **OpenClaw Client:** Async wrapper communicating with the OpenClaw gateway via the OpenAI-compatible `/v1/chat/completions` API.
- **Vector Memory Layer:** Integration with Mem0 and Qdrant to store interactions as vector embeddings, and an asynchronous synchronization routine to persist facts without blocking responses.
- **Model Routing & Cost Tracking:** LiteLLM setup with complexity-based model selection (`TIER_1_FAST`, `TIER_2_STANDARD`, `TIER_3_FRONTIER`), automatic fallbacks, and real-time cost logging to the DB.
- **Composio Integrations:** Dynamic OpenAI tool schema generation based on active OAuth connections, tool execution worker, and connection caching.
- **Slack UX & Block Kit:** Specialized message templates for `simple_response`, `thinking`, `task_confirmation`, `approval_request`, `task_result`, `error`, `help`, `status`, and `connection_request`.

## 2. What Is Left (Pending)

- **Security (Step 8):** Implementing `LlamaFirewall` and Regex PII filtering to secure inputs before sending them to the LLM.
- **Sandbox Environments (Step 11):** Integrating `E2B` for safe, isolated Python and Bash code execution.
- **Knowledge Base / RAG (Step 12):** Building the `LlamaIndex` RAG pipeline for fetching contextual data from company documentation and Slack histories.
- **Human-in-the-loop Automation:** Hooking up `HumanLayer` for automated request approvals (waiting on access/waitlist).
- **Production Deployment:** Transitioning from local Docker/Socket Mode to a fully managed production container and HTTP Event Subscriptions webhook.

---

## 3. The Test Matrix

This matrix is designed as a leveled exam. We cannot consider Lucy "working" unless all levels pass flawlessly in sequence. If a level fails, we stop, diagnose, and fix before proceeding.

### Level 1: App Boot & Connection Sanity

**Objective:** Ensure the backend services can connect to all external systems before Slack interaction begins.


| ID  | Component    | Test Action                                             | Expected Result                                           | Status    |
| --- | ------------ | ------------------------------------------------------- | --------------------------------------------------------- | --------- |
| 1.1 | Database     | App starts and connects to PostgreSQL.                  | Connection success; no Alembic pending migrations errors. | ⏳ Pending |
| 1.2 | Vector DB    | App connects to local Qdrant container.                 | Connection success.                                       | ⏳ Pending |
| 1.3 | Slack Socket | App successfully connects to Slack API via Socket Mode. | `Bolt app is running!` logged.                            | ⏳ Pending |
| 1.4 | OpenClaw     | Script calls OpenClaw `health_check`.                   | Returns 200 OK.                                           | ⏳ Pending |


### Level 2: Slack Middleware & App Mention Gate

**Objective:** Verify that Lucy correctly registers workspaces/users from incoming Slack events and responds to the basic "hello" gate.


| ID  | Component     | Test Action                                  | Expected Result                                                                                                     | Status    |
| --- | ------------- | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- | --------- |
| 2.1 | Slack Invitee | Invite `@Lucy` to the `#lucy-my-ai` channel. | Lucy joins the channel without errors.                                                                              | ✅ PASS |
| 2.2 | The Gate      | Type `@Lucy hello` in `#lucy-my-ai`.         | Middleware creates Workspace and User records in DB. Lucy replies instantly: "Hello! I'm Lucy, your AI coworker..." | ✅ PASS |
| 2.3 | Slash Command | Type `/lucy help` in `#lucy-my-ai`.          | Lucy responds with the Help Block Kit menu.                                                                         | ⏳ Pending |
| 2.4 | Slash Command | Type `/lucy status` in `#lucy-my-ai`.        | Lucy responds with system status block.                                                                             | ⏳ Pending |


### Level 3: End-to-End Task Execution (LLM Routing)

**Objective:** Verify that Lucy correctly classifies an intent, routes it to the appropriate OpenClaw/OpenRouter model, and responds in Slack.


| ID  | Component      | Test Action                                            | Expected Result                                                                        | Status    |
| --- | -------------- | ------------------------------------------------------ | -------------------------------------------------------------------------------------- | --------- |
| 3.1 | Task Creation  | Type `@Lucy tell me a joke about programming.`         | Lucy sends a `thinking` block. Task is added to DB as `CREATED`.                       | ⏳ Pending |
| 3.2 | Async Worker   | Backend worker picks up the task and begins execution. | Task status changes to `RUNNING`.                                                      | ⏳ Pending |
| 3.3 | Classification | Routing classifier analyzes the text.                  | Intent classified as `chat`, tier selected (e.g., `TIER_1_FAST` or `TIER_2_STANDARD`). | ⏳ Pending |
| 3.4 | OpenClaw Chat  | Request sent to OpenClaw gateway API.                  | LLM returns a response (a joke).                                                       | ⏳ Pending |
| 3.5 | Slack Result   | Backend updates Slack thread with the result.          | `thinking` message replaced/followed by the joke.                                      | ⏳ Pending |
| 3.6 | Cost Tracking  | Background process logs the LiteLLM usage cost.        | Record added to `CostLog` table in DB.                                                 | ⏳ Pending |


### Level 4: Memory Persistence

**Objective:** Ensure Lucy remembers facts across conversations asynchronously.


| ID  | Component     | Test Action                                           | Expected Result                                                                      | Status    |
| --- | ------------- | ----------------------------------------------------- | ------------------------------------------------------------------------------------ | --------- |
| 4.1 | Memory Add    | Ask `@Lucy my favorite programming language is Rust.` | Lucy acknowledges. The fact is async-synced to Qdrant via Mem0.                      | ⏳ Pending |
| 4.2 | Memory Recall | Ask `@Lucy what is my favorite programming language?` | `execute_task` queries vector memory, injects context to prompt, and replies "Rust". | ⏳ Pending |


### Level 5: Composio Integration UX

**Objective:** Test the dynamic tool execution and OAuth connection flow via Block Kit.


| ID  | Component      | Test Action                                            | Expected Result                                                                | Status    |
| --- | -------------- | ------------------------------------------------------ | ------------------------------------------------------------------------------ | --------- |
| 5.1 | OAuth Request  | Type `/lucy connect github`.                           | Lucy generates a Composio OAuth link and returns a `connection_request` block. | ⏳ Pending |
| 5.2 | Connect Button | Click the "Connect Github" button.                     | Opens a browser tab to authenticate (simulate completion for test purposes).   | ⏳ Pending |
| 5.3 | Tool Injection | Ask `@Lucy list my recent github repositories.`        | Tool schemas fetched via `ComposioToolset` and sent to LLM.                    | ⏳ Pending |
| 5.4 | Tool Execution | LLM decides to call the github list repositories tool. | Tool executed by `IntegrationWorker`; LLM synthesizes the result.              | ⏳ Pending |


---

## Execution Protocol

We will proceed through this matrix step-by-step.

1. Use the backend to start up Docker DB and the Python API.
2. Use the MCP `cursor-ide-browser` to access the Slack Workspace channel: `#lucy-my-ai` (`https://app.slack.com/client/T043VTH8V4N/C0AEZ241C3V`).
3. If the browser needs to authenticate, or if it says "Please change browsers", we will diagnose. If we can bypass it by running the browser locally or grabbing cookies, we will do that.
4. Execute tests in `#lucy-my-ai` as the user.
5. If something breaks, we analyze the logs, fix the bug, and re-run the level.

