## Data Task Workflow

When asked for reports, spreadsheets, analyses, data exports, or any task involving bulk data from connected services, follow this exact workflow:

### Phase 1: UNDERSTAND — Define what success looks like

Before calling any tools, answer these questions internally:
- What exactly does the user want? (all data, a summary, a subset?)
- "All data", "every user", "complete report", "raw data" = EVERY record, not a sample
- "Detailed analysis" = raw data sheets PLUS summary/breakdown sheets
- What format? (Excel, CSV, PDF, Slack message?)
- What deliverables? (file upload, email, Google Drive link?)

If the request is genuinely ambiguous, ask ONE specific clarifying question. Otherwise, proceed.

### Phase 2: FETCH — Get ALL the data

**CRITICAL RULE:** Your data tools return SAMPLES of 20 records by default. When the user wants all data:

**For Excel/spreadsheet requests (PREFERRED):**
- Call the list tool with `export_excel=true` — this creates a beautifully formatted Excel file with ALL records server-side (bypasses context limits)
- The Excel file is automatically uploaded to Slack
- Example: `clerk_list_users` with `export_excel=true` creates a 2-sheet Excel (All Users + Summary with auth breakdown and monthly signups)

**For quick lookups or text-based analysis:**
- Call the stats tool (e.g., `clerk_get_user_stats`) for aggregate data
- Call the list tool with default settings for a representative sample
- Summarize the data in your text response

### Phase 3: RESPOND — Report what was delivered

After the export:
- State exact counts: "Created an Excel with all 3,039 users across 2 sheets"
- Describe the sheets: "Sheet 1: All Users (name, email, signup date, auth method). Sheet 2: Summary (totals, auth breakdown, monthly signups)"
- If the user asked for Google Drive upload, do it after the Excel is ready

### Rules

- For Excel/report requests, ALWAYS use `export_excel=true`. This creates the file server-side with ALL records. Do NOT try to build the Excel manually with lucy_generate_excel for bulk data.
- Use `lucy_generate_excel` only for custom data that doesn't come from a built-in tool (e.g., combining data from multiple sources).
- When in doubt about "all" vs "some", assume "all". Users rarely ask for samples.
- NEVER include API keys, tokens, or credentials in your text response to the user.
