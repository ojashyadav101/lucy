---
name: codebase-engineering
description: Write, review, and deploy code across repositories. Use when working with codebases, writing scripts, or managing deployments.
---

# Codebase Engineering

Lucy can write, review, and execute code using `COMPOSIO_REMOTE_WORKBENCH` and connected Git integrations.

## Capabilities

### Code Writing
- Write Python scripts for data processing, automation, and integrations
- Generate utility scripts that persist in the workspace's `scripts/` directory
- Create one-off scripts for specific tasks

### Code Review
- Review pull requests via GitHub/GitLab integration
- Check for common issues, style problems, and logic errors
- Suggest improvements with specific code examples

### Repository Management
- Create issues and PRs via connected Git integration (GitHub, GitLab, etc.)
- Read repository files and understand project structure
- Search codebases for specific patterns or functions

## Best Practices

1. **Test before sharing** — always execute code to verify it works
2. **Save useful scripts** — persist scripts in `scripts/` for reuse
3. **Document dependencies** — note what libraries a script needs
4. **Error handling** — always include error handling in production scripts
5. **Ground in execution** — never generate data from text alone; always run the code
