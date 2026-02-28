---
name: skill-creation
description: Create reusable skills with proper structure and frontmatter. Use when creating or editing a skill or workflow.
---

When you develop a reusable workflow, save it as a skill within the relevant domain/topic folder.

## Skill Directory Structure

```
{domain}/{skill-name}/
├── SKILL.md     # Required: What it does, when/how to use, best practices
├── scripts/     # Optional: Utility scripts for the skill
├── references/  # Optional: Additional documentation (loaded on demand)
└── assets/      # Optional: Templates, images, data files
```

## SKILL.md Format

Every SKILL.md file must contain YAML frontmatter followed by Markdown content:

```markdown
---
name: skill-name
description: [What it does]. Use when [trigger conditions].
---

Instructions and best practices go here...
```

### Frontmatter Fields

| Field         | Required | Description                                                               |
| ------------- | -------- | ------------------------------------------------------------------------- |
| `name`        | Yes      | Lowercase letters, numbers, and hyphens only. Must match the folder name. |
| `description` | Yes      | What the skill does + when the agent should use it.                       |

### Name Field Rules

- Only lowercase alphanumeric characters and hyphens (`a-z`, `0-9`, `-`)
- Must not start or end with `-`
- Must not contain consecutive hyphens (`--`)
- Must match the parent directory name exactly

### Description Field Guidelines

The description must include **what** the skill does and **when** to use it. Keep it under 1024 characters.

Structure: `[What it does]. Use when [trigger phrases].`

Good examples:
```yaml
description: Creates PDF documents from HTML/CSS. Use when creating PDFs, reports, or formatted documents.
description: Validates and modifies Excel files. Use when editing or modifying Excel (.xlsx) files.
```

To prevent over-triggering, add negative triggers when the skill could be confused with similar skills:
```yaml
description: Validates and modifies Excel files. Use when editing .xlsx files. Do NOT use for CSV exports (use data-export skill instead).
```

## Body Content

After the frontmatter, structure the body as:

1. **Instructions** — step-by-step workflow, ordered by execution sequence
2. **Examples** — concrete scenarios showing input, actions, and expected result
3. **Edge cases** — common pitfalls and how to handle them
4. **Troubleshooting** (optional) — Error > Cause > Solution

Keep SKILL.md under 500 lines. Move detailed reference material to `references/` and link to it explicitly:
```markdown
Before generating the document, consult `references/style-guide.md` for brand colors and typography.
```

## Bundling Scripts

When a workflow step requires deterministic validation or data extraction, bundle a script in `scripts/` instead of relying on natural language instructions. Code is deterministic; language interpretation isn't.

Good candidates for scripts:
- **Validation** — checking file structure, formulas, required fields
- **Data extraction** — parsing styles from websites, extracting metadata
- **Transformation** — format conversion, data normalization
