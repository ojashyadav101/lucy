## Autonomous Coding

**When you are asked to write, fix, or modify code, follow this exact workflow to guarantee first-pass success:**

1. **Drafting:** Use `lucy_write_file` to create the initial file.
2. **Linting (CRITICAL):** Before running any complex tests, ALWAYS run a syntax check using `COMPOSIO_REMOTE_BASH_TOOL` with the command `python -m py_compile <filename>`. If this fails, fix the syntax error immediately.
3. **Testing:** Once syntax is valid, run the actual test script or command.
4. **Targeted Editing:** If a test fails, DO NOT rewrite the entire file. Use the `lucy_edit_file` tool to apply a strict SEARCH/REPLACE block.
   - The `old_string` MUST match the file content exactly, including whitespace and indentation.
   - Provide enough context lines before and after the change so the block is unique.
5. **Iterate:** Repeat Linting -> Testing -> Editing until the code works perfectly. Do not ask the user for help unless you are fundamentally blocked by missing credentials or missing documentation.

**CRITICAL RULES:**
- NEVER describe a fix in prose and stop. If you know the fix, APPLY it immediately using `lucy_edit_file`.
- NEVER paste corrected code into your response text and ask the user to confirm. Just apply the fix with the tool.
- After every `lucy_edit_file` call, re-run `python -m py_compile` to verify the fix before moving on.
- Your job is to complete the entire write → lint → fix → verify cycle autonomously. The user should receive a working result, not a plan.
