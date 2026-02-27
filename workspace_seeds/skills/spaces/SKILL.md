# Skill: Build & Deploy Web Applications

## Trigger
User asks to "build", "create", "make", or "deploy" a web app, website, tool, dashboard, calculator, or any interactive application.

## Workflow (exactly 3 steps)

### Step 1: Init
Call `lucy_spaces_init` with project_name and description.
Returns sandbox_path where you write code.

### Step 2: Write Code
Use `lucy_write_file` to write your app code to `{sandbox_path}/src/App.tsx`.
The template already has React 19, Tailwind CSS, and 53 shadcn/ui components installed.

### Step 3: Deploy
Call `lucy_spaces_deploy` with the project_name.
This automatically runs bun install + vite build + uploads to Vercel.
Returns the live URL on zeeya.app.

## CRITICAL RULES
- NEVER use COMPOSIO_REMOTE_BASH_TOOL or COMPOSIO_REMOTE_WORKBENCH for building
- The deploy tool handles building automatically — no manual build step
- After deploy succeeds, tell the user their app is live with the URL
- Do NOT dump raw JSON in your response — write natural language
- Keep it to 3 tool calls: init, write_file, deploy

## Available shadcn/ui Components
accordion, alert, avatar, badge, button, calendar, card, checkbox, dialog,
dropdown-menu, form, input, label, popover, progress, select, separator,
sheet, skeleton, slider, switch, table, tabs, textarea, toast, toggle, tooltip
