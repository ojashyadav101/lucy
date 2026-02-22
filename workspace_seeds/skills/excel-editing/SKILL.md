---
name: excel-editing
description: Validates and modifies Excel files. Use when editing or modifying Excel (.xlsx) files. Do NOT use for CSV exports.
---

# Excel Editing

Create and modify Excel files using Python in the code execution sandbox.

## Recommended Libraries
- **openpyxl** — read/write .xlsx files with formatting
- **pandas** — data manipulation and export to Excel
- **xlsxwriter** — create new Excel files with advanced formatting

## Workflow

1. Read the existing file (if modifying) to understand structure
2. Make the required changes
3. Validate formulas and formatting
4. Save and share the result

## Best Practices

- Always preserve existing formatting when modifying files
- Validate formulas after modification — use a validation script if available
- Handle merged cells carefully — they can break when rows are inserted/deleted
- For large datasets, prefer pandas for data operations, then openpyxl for formatting
- Test with real data before sharing the final version
