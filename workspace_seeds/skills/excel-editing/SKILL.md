---
name: excel-editing
description: Create and modify Excel files with professional formatting, charts, and formulas. Use when creating or editing Excel (.xlsx) files. Do NOT use for CSV exports.
---

# Excel Editing

Create and modify Excel files using Python in `COMPOSIO_REMOTE_WORKBENCH`. The goal is professional, "work of art" quality output — not raw data dumps.

## Libraries

| Library | Use For | Install |
|---------|---------|---------|
| **XlsxWriter** | Creating new files with advanced formatting, charts, sparklines | `pip install XlsxWriter` |
| **openpyxl** | Reading and modifying existing .xlsx files | `pip install openpyxl` |
| **pandas** | Data manipulation before writing to Excel | `pip install pandas` |

**Rule of thumb:** Use XlsxWriter for new files (superior formatting control). Use openpyxl when modifying existing files.

## Format System — Define Before Writing

Always define reusable `Format` objects upfront. Consistent formatting is what makes spreadsheets look professional.

```python
import xlsxwriter

workbook = xlsxwriter.Workbook("report.xlsx")

# ── Core format system ──────────────────────────────────────
header = workbook.add_format({
    "bold": True, "font_size": 11, "font_color": "#FFFFFF",
    "bg_color": "#2B579A", "border": 1, "text_wrap": True,
    "align": "center", "valign": "vcenter",
})
subheader = workbook.add_format({
    "bold": True, "font_size": 10, "bg_color": "#D6E4F0",
    "border": 1, "align": "center",
})
input_cell = workbook.add_format({
    "font_color": "#0000FF", "num_format": "#,##0.00",
    "border": 1, "bg_color": "#FFF2CC",
})
formula_cell = workbook.add_format({
    "font_color": "#000000", "num_format": "#,##0.00",
    "border": 1,
})
currency = workbook.add_format({
    "num_format": "$#,##0.00", "border": 1,
})
percentage = workbook.add_format({
    "num_format": "0.0%", "border": 1,
})
negative_red = workbook.add_format({
    "num_format": "$#,##0.00;[Red]($#,##0.00)", "border": 1,
})
total_row = workbook.add_format({
    "bold": True, "top": 2, "bottom": 6,
    "num_format": "$#,##0.00", "bg_color": "#E2EFDA",
})
date_format = workbook.add_format({
    "num_format": "yyyy-mm-dd", "border": 1,
})
```

## Financial Model Color Standards

| Color | Meaning | Use For |
|-------|---------|---------|
| **Blue** text | Input / assumption | Cells that users manually enter |
| **Black** text | Formula / calculation | Computed cells |
| **Green** text | Cross-sheet reference | Links to other sheets |
| **Red** text | External data | Values from outside sources |
| **Yellow** background | Input cell highlight | Draw attention to editable cells |

## Excel Tables

Always use `add_table()` for data ranges — it adds banded rows, auto-filters, and structured references automatically:

```python
ws.add_table("A1:F20", {
    "name": "SalesData",
    "style": "Table Style Medium 2",
    "columns": [
        {"header": "Date", "format": date_format},
        {"header": "Product"},
        {"header": "Quantity", "format": formula_cell},
        {"header": "Unit Price", "format": currency},
        {"header": "Revenue", "format": currency,
         "formula": "=[@Quantity]*[@[Unit Price]]"},
        {"header": "Margin %", "format": percentage},
    ],
})
```

## Conditional Formatting

Apply on every data range for visual clarity:

```python
# Color scale: red → yellow → green
ws.conditional_format("E2:E100", {
    "type": "3_color_scale",
    "min_color": "#F8696B",
    "mid_color": "#FFEB84",
    "max_color": "#63BE7B",
})

# Data bars for visual comparison
ws.conditional_format("D2:D100", {
    "type": "data_bar",
    "bar_color": "#4472C4",
})

# Icon sets for status indicators
ws.conditional_format("G2:G100", {
    "type": "icon_set",
    "icon_style": "3_traffic_lights",
})

# Highlight negative values
ws.conditional_format("F2:F100", {
    "type": "cell", "criteria": "<", "value": 0,
    "format": workbook.add_format({"font_color": "#FF0000", "bold": True}),
})
```

## Charts

```python
chart = workbook.add_chart({"type": "column"})
chart.add_series({
    "name": "Revenue",
    "categories": "=Sheet1!$A$2:$A$13",
    "values": "=Sheet1!$E$2:$E$13",
    "fill": {"color": "#4472C4"},
})
chart.set_title({"name": "Monthly Revenue"})
chart.set_x_axis({"name": "Month"})
chart.set_y_axis({"name": "Revenue ($)", "num_format": "$#,##0"})
chart.set_size({"width": 720, "height": 420})
chart.set_style(10)
ws.insert_chart("H2", chart)
```

## Sparklines

Inline mini-charts inside cells for trend visualization:

```python
ws.add_sparkline("G2", {
    "range": "B2:F2",
    "type": "line",
    "markers": True,
    "style": 1,
})
```

## Formula-First Approach

**Never hardcode calculated values.** Always use Excel formulas:

```python
ws.write_formula("E2", "=C2*D2", currency)          # revenue
ws.write_formula("E22", "=SUM(E2:E21)", total_row)   # total
ws.write_formula("F2", "=(E2-B2)/B2", percentage)    # growth
ws.write_formula("G2", '=IF(F2>0.1,"Above","Below")')  # classification
```

## Page Setup and Polish

```python
# Freeze header row
ws.freeze_panes(1, 0)

# Auto-filter on all columns
ws.autofilter("A1:F100")

# Column widths (auto-fit approximation)
ws.set_column("A:A", 12)  # Date
ws.set_column("B:B", 20)  # Product name
ws.set_column("C:F", 15)  # Numeric columns

# Print setup
ws.set_landscape()
ws.set_paper(1)  # Letter
ws.fit_to_pages(1, 0)  # Fit width to 1 page
ws.set_header("&L&D&R&P of &N")
ws.set_footer("&CConfidential")

# Sheet tab color
ws.set_tab_color("#4472C4")
```

## Modifying Existing Files (openpyxl)

```python
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment

wb = load_workbook("existing.xlsx")
ws = wb.active

# Preserve existing formatting while updating values
for row in ws.iter_rows(min_row=2, max_col=5):
    for cell in row:
        if cell.column_letter == "E":
            cell.value = f"=C{cell.row}*D{cell.row}"

wb.save("updated.xlsx")
```

## Validation Script

After generating, validate the file opens correctly:

```bash
# Convert to verify (requires LibreOffice)
libreoffice --headless --calc --convert-to xlsx output.xlsx
```

## Workflow

1. Understand the data and output requirements
2. Define the format system (formats, colors, number formats)
3. Create the workbook structure (sheets, tables)
4. Write data with formulas (never hardcode calculations)
5. Apply conditional formatting to every data range
6. Add charts for key metrics
7. Set up page layout (freeze panes, print setup, column widths)
8. Save and upload to Slack

## Anti-Patterns

- Don't dump raw data without formatting — every spreadsheet needs headers, borders, number formats
- Don't hardcode calculated values — use Excel formulas so the file is interactive
- Don't use inline formatting — define format objects upfront and reuse them
- Don't skip conditional formatting — it's what makes data scannable
- Don't forget freeze panes on the header row
- Don't use pandas `to_excel()` alone — it produces bare, unformatted output
