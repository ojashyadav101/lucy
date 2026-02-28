---
name: docx-editing
description: Create and edit Word documents (.docx) with professional formatting, templates, and rich content. Use when creating or modifying Word documents, proposals, or reports.
---

# DOCX Editing

Create and modify Word documents using Python in `COMPOSIO_REMOTE_WORKBENCH`. The goal is professional, polished output — not bare text dumps.

## Libraries

| Library | Use For | Install |
|---------|---------|---------|
| **python-docx** | Full control over document structure, styles, tables, images | `pip install python-docx` |
| **docxtpl** | Template-based generation with Jinja2 syntax inside Word | `pip install docxtpl` |

**Rule of thumb:** Use `docxtpl` for repeatable documents (invoices, reports, proposals). Use `python-docx` for one-off or programmatic generation.

## Template-First Approach

The best DOCX output starts from a pre-designed `.docx` template with styles, fonts, and colors already set. The code fills in content — it doesn't design the document from scratch.

### Using docxtpl (Jinja2 Templates)

Create a Word document with `{{ placeholder }}` markers, then fill programmatically:

```python
from docxtpl import DocxTemplate

doc = DocxTemplate("proposal_template.docx")
context = {
    "company_name": "Acme Corp",
    "date": "February 22, 2026",
    "items": [
        {"name": "Consulting", "hours": 40, "rate": 150, "total": 6000},
        {"name": "Development", "hours": 120, "rate": 200, "total": 24000},
    ],
    "grand_total": "$30,000",
    "contact_name": "Jane Smith",
}
doc.render(context)
doc.save("proposal_acme.docx")
```

Template supports:
- `{{ variable }}` — simple text replacement
- `{% for item in items %}...{% endfor %}` — loops (for tables, lists)
- `{% if condition %}...{% endif %}` — conditional sections
- `{{ image }}` — inline image insertion via `InlineImage`
- Rich text via `RichText` objects

### Using python-docx (Programmatic)

```python
from docx import Document
from docx.shared import Inches, Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT

doc = Document("template.docx")  # Start from template for styles
```

## Document Styles Over Inline Formatting

**Always use named styles** — they ensure consistency and allow the recipient to restyle the entire document by modifying the style definition.

```python
# Use built-in styles
doc.add_heading("Executive Summary", level=1)  # Uses 'Heading 1' style
doc.add_paragraph("Key findings...", style="Body Text")

# Modify style properties globally
style = doc.styles["Heading 1"]
style.font.size = Pt(24)
style.font.color.rgb = RGBColor(0x2B, 0x57, 0x9A)
style.font.bold = True
style.paragraph_format.space_after = Pt(12)

style = doc.styles["Body Text"]
style.font.size = Pt(11)
style.font.name = "Calibri"
style.paragraph_format.line_spacing = 1.15
```

## Rich Formatting Patterns

### Headers and Footers with Logo

```python
from docx.shared import Inches

section = doc.sections[0]

# Header
header = section.header
header_para = header.paragraphs[0]
run = header_para.add_run()
run.add_picture("logo.png", width=Inches(1.5))
header_para.alignment = WD_ALIGN_PARAGRAPH.RIGHT

# Footer with page numbers
footer = section.footer
footer_para = footer.paragraphs[0]
footer_para.text = "Confidential — Page "
# Page number field requires XML manipulation
from docx.oxml.ns import qn
fld = footer_para.runs[0]._element
fldChar1 = fld.makeelement(qn("w:fldChar"), {qn("w:fldCharType"): "begin"})
fld.append(fldChar1)
instrText = fld.makeelement(qn("w:instrText"), {})
instrText.text = " PAGE "
fld.append(instrText)
fldChar2 = fld.makeelement(qn("w:fldChar"), {qn("w:fldCharType"): "end"})
fld.append(fldChar2)
```

### Professional Tables

```python
table = doc.add_table(rows=1, cols=4, style="Light Grid Accent 1")
table.alignment = WD_TABLE_ALIGNMENT.CENTER

# Header row
header_cells = table.rows[0].cells
for i, text in enumerate(["Item", "Quantity", "Unit Price", "Total"]):
    header_cells[i].text = text
    for paragraph in header_cells[i].paragraphs:
        for run in paragraph.runs:
            run.font.bold = True
            run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

# Data rows
for item in data:
    row_cells = table.add_row().cells
    row_cells[0].text = item["name"]
    row_cells[1].text = str(item["qty"])
    row_cells[2].text = f"${item['price']:.2f}"
    row_cells[3].text = f"${item['total']:.2f}"

# Set column widths
from docx.shared import Cm
for row in table.rows:
    row.cells[0].width = Cm(6)
    row.cells[1].width = Cm(3)
    row.cells[2].width = Cm(3)
    row.cells[3].width = Cm(3)
```

### Page Setup

```python
section = doc.sections[0]
section.page_width = Cm(21)     # A4
section.page_height = Cm(29.7)  # A4
section.left_margin = Cm(2.5)
section.right_margin = Cm(2.5)
section.top_margin = Cm(2)
section.bottom_margin = Cm(2)
```

### Images

```python
doc.add_picture("chart.png", width=Inches(5.5))
last_paragraph = doc.paragraphs[-1]
last_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
```

## Run-Aware Text Replacement

When modifying existing documents, formatting is stored in "runs" (text segments with consistent formatting). Naive find-and-replace can break formatting.

```python
def replace_text_preserving_format(doc, old_text, new_text):
    for paragraph in doc.paragraphs:
        if old_text in paragraph.text:
            for run in paragraph.runs:
                if old_text in run.text:
                    run.text = run.text.replace(old_text, new_text)
```

## Workflow

1. **Start from a template** — always use a pre-designed .docx file as the base
2. **Use styles** — define heading, body, table styles; never inline-format everything
3. **Add content** — headings, paragraphs, tables, images in logical order
4. **Apply page setup** — margins, orientation, headers/footers
5. **Save and upload to Slack**

## Anti-Patterns

- Don't create documents from `Document()` (blank) when a template is available — blank docs have no styles
- Don't inline-format every paragraph — use document styles for consistency
- Don't forget headers/footers — they make documents look professional
- Don't use `paragraph.text = "..."` to replace content — it destroys formatting; use run-level replacement
- Don't skip table styles — bare tables look unprofessional
- Don't embed huge images without sizing — always set `width` in `add_picture()`
