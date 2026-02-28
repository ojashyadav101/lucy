# Skills, Seeds & Templates — Deep Dive

> Platform skills shipped with every workspace, default cron configurations,
> the lucy-spaces web app template, and the onboarding seed system.

---

## How Seeds Work

When a new Slack workspace interacts with Lucy for the first time,
`onboard_workspace()` copies seed files from `workspace_seeds/` into the
workspace directory:

```
workspace_seeds/                    workspaces/{workspace_id}/
├── skills/                   →     ├── skills/
│   ├── spaces/SKILL.md             │   ├── spaces/SKILL.md
│   ├── browser/SKILL.md            │   ├── browser/SKILL.md
│   └── ... (17 skills)             │   └── ...
├── crons/                    →     ├── crons/
│   ├── heartbeat/task.json         │   ├── heartbeat/task.json
│   └── ... (4 crons)               │   └── ...
└── HEARTBEAT.md              →     └── HEARTBEAT.md
```

After copying, the scheduler reloads to register default crons.

---

## Platform Skills (18 total)

Every workspace gets these skills. Each is a `SKILL.md` file with YAML
frontmatter (name, description, triggers) and a Markdown body.

### Core Capabilities

| # | Skill | Triggers | What It Teaches Lucy |
|---|-------|----------|---------------------|
| 1 | **spaces** | build, create, deploy, web app, dashboard | 3-step web app workflow: `lucy_spaces_init` → write React code → `lucy_spaces_deploy`. Uses React 19 + Tailwind + shadcn/ui. |
| 2 | **browser** | scrape, browse, fill form, automate web | CamoFox anti-detection browser: create tab → navigate → snapshot (accessibility tree) → interact (click/type/fill) → close. 14 search macros. |
| 3 | **codebase-engineering** | code, script, PR, deploy, repository | Write Python scripts, review PRs via Git, manage repos. Test before sharing, save to `scripts/`, include error handling. |
| 4 | **async-workflows** | monitor, wait for, track, alert when | Multi-step workflows spanning hours/days: immediate action → heartbeat cron → report back. Interval selection by urgency. |

### Integration & Administration

| # | Skill | Triggers | What It Teaches Lucy |
|---|-------|----------|---------------------|
| 5 | **integrations** | connect, integrate, OAuth, setup | Composio meta-tools: check connection → OAuth if needed → discover tools → execute. Per-user OAuth handling. |
| 6 | **slack-admin** | users, channels, post, team admin | Slack workspace operations: reading (list users/channels, history, search), messaging (send/reply/react), channel management, file operations. |
| 7 | **scheduled-crons** | recurring, schedule, automate, daily | Cron CRUD: `lucy_create_cron`, `lucy_modify_cron`, `lucy_delete_cron`. Cron expressions, timezone, delivery routing, LEARNINGS.md. |
| 8 | **general-tools** | search, email, calendar, image, task | Discovery pattern for Composio tools: web search, email, calendar, file ops, image gen, project management, CRM. |

### Document Generation

| # | Skill | Triggers | What It Teaches Lucy |
|---|-------|----------|---------------------|
| 9 | **pdf-creation** | PDF, report, invoice, document | Two-track: WeasyPrint (HTML/CSS → PDF, ~335ms) for styled docs; Typst (template → PDF, ~106ms) for data reports. CSS design system included. |
| 10 | **excel-editing** | Excel, spreadsheet, XLSX | Excel creation/editing with formatting using openpyxl. |
| 11 | **pptx-editing** | PowerPoint, slides, presentation, deck | `python-pptx` with template-first approach. 29 chart types, tables, images, speaker notes. Design guidelines included. |
| 12 | **docx-editing** | Word, document, DOCX, proposal | `python-docx` + `docxtpl` (Jinja2 templates). Named styles over inline formatting. Headers/footers, tables, images. |
| 13 | **pdf-signing** | sign PDF, digital signature | pyHanko (cryptographic X.509) or Pillow + reportlab (visual overlay). Notes on legal binding. |
| 14 | **pdf-form-filling** | fill form, PDF form, application | pdfrw, PyPDF2, or fillpdf. Workflow: discover fields → map data → fill → save. |

### Intelligence & Meta

| # | Skill | Triggers | What It Teaches Lucy |
|---|-------|----------|---------------------|
| 15 | **workflow-discovery** | help the team, automation, pain points | 6-phase process: audit integrations → investigate per person (read Slack extensively) → brainstorm ideas → craft personalized DM proposals → track in discovery.md → follow up. Target: 2-3 ideas/person. |
| 16 | **thread-orchestration** | long task, multi-step, progress | Patterns: progress thread (ack → updates → summary), approval flow (describe → confirm → execute), multi-person thread (tag → track → summarize). Anti-patterns listed. |
| 17 | **lucy-account** | what can you do, how do you work | Lucy's self-awareness: reactive capabilities, proactive capabilities (heartbeat, crons, discovery), how she learns, limitations. |
| 18 | **skill-creation** | create skill, new workflow | Skill file format: YAML frontmatter + Markdown body. Directory structure, naming rules, body organization, keeping under 500 lines. |

---

## Default Crons (4 total)

Registered automatically when a workspace is onboarded.

### 1. Proactive Heartbeat

| Field | Value |
|-------|-------|
| **Path** | `/heartbeat` |
| **Schedule** | `*/30 8-22 * * *` (every 30 min, 8 AM - 10 PM UTC) |
| **Type** | `agent` |
| **Delivery** | `channel` |
| **Instruction** | Read `HEARTBEAT.md` checklist. Check unanswered questions, task follow-ups, pending items, team awareness. If nothing actionable → `HEARTBEAT_OK`. |

### 2. Workflow Discovery

| Field | Value |
|-------|-------|
| **Path** | `/workflow-discovery` |
| **Schedule** | `0 10 * * 2,4` (Tuesdays and Thursdays at 10 AM UTC) |
| **Type** | `agent` |
| **Delivery** | `dm` |
| **Instruction** | Follow `skills/workflow-discovery/SKILL.md`. Investigate one team member's Slack history, analyze pain points, brainstorm 3 automations, DM personalized proposal. Log to `discovery.md`. |

### 3. Channel Introductions

| Field | Value |
|-------|-------|
| **Path** | `/channel-introductions` |
| **Schedule** | `0 10 * * 1-5` (Weekdays at 10 AM UTC) |
| **Type** | `agent` |
| **Delivery** | `channel` |
| **Max Runs** | 3 (self-deletes after 3 introductions) |
| **Instruction** | Scan public channels. Pick one active channel Lucy hasn't introduced herself to. Send short, contextual intro. Log channel to `execution.log`. |

### 4. Slack Sync

| Field | Value |
|-------|-------|
| **Path** | `slack-sync` |
| **Schedule** | `*/10 * * * *` (every 10 minutes) |
| **Type** | `script` |
| **Instruction** | Sync recent Slack messages to workspace filesystem. Provides grep-accessible history without rate-limited API calls. Run silently. |

---

## Lucy Spaces Template

**Location:** `templates/lucy-spaces/`

The full-stack web application template used when a user asks Lucy to build
a web app.

### Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 19, TypeScript, Vite |
| Styling | Tailwind CSS v4 |
| Components | shadcn/ui (53 components) |
| Backend | Convex (serverless functions + database) |
| Auth | Email/password with OTP verification |
| Deployment | Vercel (static hosting) |
| Domain | `{app-name}.zeeya.app` |
| Package Manager | Bun |
| Linting | Biome |

### Template Structure

```
templates/lucy-spaces/
├── convex/                    # Backend
│   ├── schema.ts              # Database schema
│   ├── auth.ts                # Auth configuration
│   ├── users.ts               # User management functions
│   └── http.ts                # HTTP routes
├── src/                       # Frontend
│   ├── App.tsx                # Root component
│   ├── main.tsx               # Entry point
│   ├── components/            # UI components
│   │   └── ui/                # 53 shadcn components
│   ├── pages/                 # Page components
│   ├── contexts/              # React contexts
│   ├── hooks/                 # Custom hooks
│   └── lib/                   # Utilities
├── biome.json                 # Linting config
├── components.json            # shadcn CLI config
├── package.json               # Dependencies
├── vite.config.ts             # Build config
└── tailwind.config.ts         # Styling config
```

### 53 shadcn/ui Components

The template includes pre-installed shadcn/ui components: accordion, alert,
alert-dialog, aspect-ratio, avatar, badge, breadcrumb, button, calendar,
card, carousel, chart, checkbox, collapsible, command, context-menu,
data-table, dialog, drawer, dropdown-menu, form, hover-card, input,
input-otp, label, menubar, navigation-menu, pagination, popover, progress,
radio-group, resizable, scroll-area, select, separator, sheet, sidebar,
skeleton, slider, sonner, switch, table, tabs, textarea, toast, toggle,
toggle-group, tooltip, and more.

### Auth System

- Email/password registration with OTP verification
- Session management via Convex auth
- Protected routes via auth context
- User profile management

### Deployment Flow

```
lucy_spaces_init(name, description)
    │
    ├── Copy template to workspace
    ├── Create Convex project + deployment
    ├── Create Vercel project + custom domain
    ├── Generate secrets (.env.local)
    └── Save project.json

Agent writes code to src/App.tsx (and other files)
    │
    └── Uses COMPOSIO_REMOTE_WORKBENCH to edit files

lucy_spaces_deploy(name)
    │
    ├── bun install
    ├── vite build
    ├── Upload dist/ to Vercel
    ├── Wait for deployment readiness
    └── Validate deployed URL

Result: "Your app is live at {name}.zeeya.app"
```

---

## Skill File Format

Every skill follows this structure:

```markdown
---
name: skill-name
description: One-line description of what this skill does
triggers: keyword1, keyword2, keyword3
---

## Overview
Brief explanation of the capability.

## Workflow
Step-by-step instructions for Lucy.

## Examples
Concrete examples of when to use this skill.

## Edge Cases
Common pitfalls and how to handle them.

## Anti-Patterns
Things Lucy should NOT do.
```

### Frontmatter Fields

| Field | Required | Purpose |
|-------|----------|---------|
| `name` | Yes | Display name (lowercase, alphanumeric + hyphens) |
| `description` | Yes | One-line summary for prompt injection |
| `triggers` | No | Comma-separated keywords for regex matching |

### How Skills Are Discovered

```
User message arrives
    │
    ├── detect_relevant_skills(message)
    │     Regex matches message against trigger keywords
    │     Returns up to 3 skills sorted by match count
    │
    ├── load_relevant_skill_content(ws, message)
    │     Loads full SKILL.md content for matched skills
    │     Max 8000 chars total
    │
    └── Injected into dynamic suffix of system prompt
```

### Writing Effective Skills

1. Keep under 500 lines — move details to `references/` subdirectory
2. Write instructions as commands ("Do X", "Check Y"), not descriptions
3. Include concrete examples with expected tool calls
4. List anti-patterns explicitly
5. Bundle scripts for deterministic validation/extraction
6. Match the trigger keywords to how users actually phrase requests

---

## Cross-System Effects

| If You Change... | Also Check... |
|-----------------|---------------|
| Skill frontmatter format | `parse_frontmatter()` in `workspace/skills.py` |
| Skill trigger keywords | `detect_relevant_skills()` regex matching |
| Default cron task.json | `CronConfig` dataclass in `crons/scheduler.py` |
| Seed directory structure | `onboard_workspace()` in `workspace/onboarding.py` |
| Template files | `init_app_project()` in `spaces/platform.py` |
| Skill content > 8000 chars | `load_relevant_skill_content()` truncation |
| Adding new skills | Must also add to `workspace_seeds/skills/` for new workspaces |
