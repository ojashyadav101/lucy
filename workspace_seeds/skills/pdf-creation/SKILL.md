---
name: pdf-creation
description: Creates professional PDF documents using a two-track system (WeasyPrint for styled documents, Typst for data reports). Use when creating PDFs, reports, invoices, or formatted documents.
---

# PDF Creation — Two-Track System

Generate professional, "work of art" quality PDF documents using Python in `COMPOSIO_REMOTE_WORKBENCH`.

## Two Tracks

| Track | Tool | Best For | Speed |
|-------|------|----------|-------|
| **HTML/CSS → PDF** | WeasyPrint | Custom layouts, branded docs, marketing materials, invoices | ~335ms |
| **Template → PDF** | Typst | Structured reports, data tables, technical docs, financial statements | ~106ms |

Choose based on the document type. When in doubt, use WeasyPrint (more flexible).

## Track 1: WeasyPrint (Primary)

**Install:** `pip install weasyprint`
**System deps:** `libpango1.0-dev`, `libpangoft2-1.0-0` (pre-installed in sandbox)

The LLM generates HTML + CSS, WeasyPrint renders it to PDF. This gives you full CSS control over layout, typography, and styling.

### Design System — CSS Variables

Define a design system once and reuse across all documents:

```css
:root {
    /* Brand colors */
    --color-primary: #2B579A;
    --color-secondary: #4472C4;
    --color-accent: #ED7D31;
    --color-success: #70AD47;
    --color-danger: #FF4444;
    --color-text: #333333;
    --color-text-light: #666666;
    --color-bg-light: #F8F9FA;
    --color-border: #DEE2E6;

    /* Typography scale */
    --font-sans: 'Inter', 'Segoe UI', system-ui, sans-serif;
    --font-mono: 'JetBrains Mono', 'Fira Code', monospace;
    --text-xs: 0.75rem;
    --text-sm: 0.875rem;
    --text-base: 1rem;
    --text-lg: 1.125rem;
    --text-xl: 1.5rem;
    --text-2xl: 2rem;

    /* Spacing */
    --space-1: 0.25rem;
    --space-2: 0.5rem;
    --space-3: 1rem;
    --space-4: 1.5rem;
    --space-6: 2.5rem;
}
```

### Pre-built Component Classes

```css
/* Page setup */
@page {
    size: A4;
    margin: 2cm 2.5cm;
    @top-right { content: "Page " counter(page) " of " counter(pages); font-size: 9pt; color: #999; }
    @bottom-center { content: "Confidential"; font-size: 8pt; color: #CCC; }
}

/* Header banner */
.header-banner {
    background: linear-gradient(135deg, var(--color-primary), var(--color-secondary));
    color: white;
    padding: var(--space-4) var(--space-6);
    margin: -2cm -2.5cm 2cm -2.5cm;
    page-break-inside: avoid;
}
.header-banner h1 { font-size: var(--text-2xl); margin: 0; font-weight: 700; }
.header-banner .subtitle { font-size: var(--text-lg); opacity: 0.85; margin-top: var(--space-1); }

/* Data table */
.data-table {
    width: 100%;
    border-collapse: collapse;
    margin: var(--space-3) 0;
    font-size: var(--text-sm);
}
.data-table th {
    background: var(--color-primary);
    color: white;
    padding: 10px 12px;
    text-align: left;
    font-weight: 600;
}
.data-table td {
    padding: 8px 12px;
    border-bottom: 1px solid var(--color-border);
}
.data-table tr:nth-child(even) { background: var(--color-bg-light); }
.data-table tr:hover { background: #E8F0FE; }
.data-table .numeric { text-align: right; font-variant-numeric: tabular-nums; }

/* Metric boxes */
.metrics-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: var(--space-3);
    margin: var(--space-4) 0;
}
.metric-box {
    border: 1px solid var(--color-border);
    border-radius: 8px;
    padding: var(--space-3);
    text-align: center;
}
.metric-box .value {
    font-size: var(--text-2xl);
    font-weight: 700;
    color: var(--color-primary);
}
.metric-box .label {
    font-size: var(--text-sm);
    color: var(--color-text-light);
    margin-top: var(--space-1);
}

/* Cards */
.card {
    border: 1px solid var(--color-border);
    border-radius: 8px;
    padding: var(--space-4);
    margin: var(--space-3) 0;
    page-break-inside: avoid;
}
.card-title {
    font-size: var(--text-lg);
    font-weight: 600;
    color: var(--color-primary);
    margin-bottom: var(--space-2);
    border-bottom: 2px solid var(--color-secondary);
    padding-bottom: var(--space-1);
}

/* Status badges */
.badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 12px;
    font-size: var(--text-xs);
    font-weight: 600;
}
.badge-success { background: #E8F5E9; color: #2E7D32; }
.badge-warning { background: #FFF3E0; color: #E65100; }
.badge-danger { background: #FFEBEE; color: #C62828; }

/* Page breaks */
.page-break { page-break-before: always; }
```

### Python Generation Example

```python
import weasyprint

html = f"""<!DOCTYPE html>
<html>
<head><style>{css_design_system}</style></head>
<body>
    <div class="header-banner">
        <h1>Monthly Revenue Report</h1>
        <div class="subtitle">February 2026</div>
    </div>

    <div class="metrics-grid">
        <div class="metric-box">
            <div class="value">$124.5K</div>
            <div class="label">Total Revenue</div>
        </div>
        <div class="metric-box">
            <div class="value">+12.3%</div>
            <div class="label">Growth MoM</div>
        </div>
        <div class="metric-box">
            <div class="value">342</div>
            <div class="label">Active Customers</div>
        </div>
    </div>

    <table class="data-table">
        <tr><th>Product</th><th class="numeric">Revenue</th><th class="numeric">Growth</th></tr>
        <tr><td>Enterprise</td><td class="numeric">$78,200</td><td class="numeric">+15.2%</td></tr>
        <tr><td>Starter</td><td class="numeric">$46,300</td><td class="numeric">+8.1%</td></tr>
    </table>
</body>
</html>"""

doc = weasyprint.HTML(string=html)
doc.write_pdf("report.pdf")
```

### Custom Fonts

```python
# Copy fonts to system path (in sandbox)
import shutil, os
os.makedirs("/usr/share/fonts/custom", exist_ok=True)
shutil.copy("Inter-Regular.ttf", "/usr/share/fonts/custom/")

# Then reference in CSS with @font-face
```

### Embedding Images and Charts

```python
import base64

# Generate chart with matplotlib
import matplotlib.pyplot as plt
fig, ax = plt.subplots()
ax.bar(months, revenue)
buf = io.BytesIO()
fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
chart_b64 = base64.b64encode(buf.getvalue()).decode()

# Embed in HTML
html += f'<img src="data:image/png;base64,{chart_b64}" style="width:100%">'
```

## Track 2: Typst (Secondary — Data-Heavy Documents)

**Install:** `pip install typst` (single 40MB binary, no system dependencies)

Typst is a modern typesetting system — blazing fast for structured, data-heavy documents.

```python
import typst

source = """
#set page(paper: "a4", margin: 2cm)
#set text(font: "Inter", size: 11pt)

= Quarterly Financial Report

#table(
  columns: (1fr, 1fr, 1fr, 1fr),
  [*Quarter*], [*Revenue*], [*Expenses*], [*Profit*],
  [Q1], [$312K], [$245K], [$67K],
  [Q2], [$358K], [$261K], [$97K],
  [Q3], [$401K], [$278K], [$123K],
)
"""

typst.compile(source, output="report.pdf")
```

## Workflow

1. **Understand requirements** — content, layout, branding, audience
2. **Choose track** — WeasyPrint for custom/branded, Typst for data-heavy
3. **Build the design system** — CSS variables or Typst set rules
4. **Generate content** — HTML with component classes, or Typst markup
5. **Embed charts/images** — generate with matplotlib/plotly, embed as base64
6. **Render PDF** — WeasyPrint or Typst compile
7. **Upload to Slack** — share the generated file

## Anti-Patterns

- Don't generate PDFs with bare unstyled HTML — always use the design system
- Don't use reportlab or fpdf2 for complex layouts — they lack CSS-level styling
- Don't forget `@page` rules — they control margins, page numbers, headers/footers
- Don't embed huge images without resizing — keep DPI reasonable (150 for screen, 300 for print)
- Don't skip page break management — long tables and sections need `page-break-inside: avoid`
- Don't generate a PDF without reviewing the HTML output first (render to string, check structure)
