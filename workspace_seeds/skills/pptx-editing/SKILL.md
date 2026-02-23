---
name: pptx-editing
description: Create and edit PowerPoint presentations (.pptx) with professional layouts, charts, and consistent styling. Use when creating slide decks, presentations, or pitch decks.
---

# PPTX Editing

Create and modify PowerPoint presentations using Python in `COMPOSIO_REMOTE_WORKBENCH`. Start from templates — the template IS the theme.

## Library

| Library | Use For | Install |
|---------|---------|---------|
| **python-pptx** | Full control over slides, layouts, shapes, charts, tables, speaker notes | `pip install python-pptx` |

## Template-First Approach

**Always start from a branded `.pptx` template.** The template defines:
- Slide master with fonts, colors, and background
- Slide layouts with pre-positioned placeholders
- Color theme and font theme

```python
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

prs = Presentation("template.pptx")
```

## Slide Layouts

Use the template's built-in layouts — don't create blank slides:

| Layout Index | Typical Layout | Use For |
|-------------|---------------|---------|
| 0 | Title Slide | Opening slide |
| 1 | Title + Content | Most content slides |
| 2 | Section Header | Chapter dividers |
| 3 | Two Content | Side-by-side comparison |
| 4 | Comparison | Before/after, pros/cons |
| 5 | Title Only | Custom layouts with shapes |
| 6 | Blank | Full-custom slides |

```python
# Title slide
slide = prs.slides.add_slide(prs.slide_layouts[0])
slide.shapes.title.text = "Q1 2026 Business Review"
slide.placeholders[1].text = "Prepared by Lucy — February 2026"

# Content slide
slide = prs.slides.add_slide(prs.slide_layouts[1])
slide.shapes.title.text = "Revenue Overview"
body = slide.placeholders[1]
tf = body.text_frame
tf.text = "Key highlights from this quarter:"

# Add bullet points
for point in ["Revenue up 15% MoM", "3 new enterprise clients", "Churn reduced to 2.1%"]:
    p = tf.add_paragraph()
    p.text = point
    p.level = 1
    p.font.size = Pt(18)
```

## Charts (29 Types Available)

```python
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE

# Column chart
chart_data = CategoryChartData()
chart_data.categories = ["Jan", "Feb", "Mar", "Apr"]
chart_data.add_series("Revenue", (120, 135, 158, 172))
chart_data.add_series("Target", (130, 130, 150, 160))

chart_frame = slide.shapes.add_chart(
    XL_CHART_TYPE.COLUMN_CLUSTERED,
    Inches(1), Inches(2), Inches(8), Inches(4.5),
    chart_data,
)
chart = chart_frame.chart
chart.has_legend = True

# Style the series
series = chart.series[0]
series.format.fill.solid()
series.format.fill.fore_color.rgb = RGBColor(0x44, 0x72, 0xC4)

series2 = chart.series[1]
series2.format.fill.solid()
series2.format.fill.fore_color.rgb = RGBColor(0xED, 0x7D, 0x31)

# Pie chart
pie_data = CategoryChartData()
pie_data.categories = ["Enterprise", "Growth", "Starter"]
pie_data.add_series("Mix", (55, 30, 15))

pie_frame = slide.shapes.add_chart(
    XL_CHART_TYPE.PIE,
    Inches(1), Inches(2), Inches(5), Inches(4),
    pie_data,
)
pie_chart = pie_frame.chart
pie_chart.has_legend = True
plot = pie_chart.plots[0]
plot.has_data_labels = True
data_labels = plot.data_labels
data_labels.number_format = '0%'
data_labels.show_percentage = True

# Line chart for trends
line_data = CategoryChartData()
line_data.categories = ["W1", "W2", "W3", "W4", "W5", "W6", "W7", "W8"]
line_data.add_series("Active Users", (120, 135, 142, 158, 167, 172, 189, 201))

line_frame = slide.shapes.add_chart(
    XL_CHART_TYPE.LINE_MARKERS,
    Inches(1), Inches(2), Inches(8), Inches(4),
    line_data,
)
```

## Tables

```python
rows, cols = 5, 4
table_shape = slide.shapes.add_table(rows, cols, Inches(1), Inches(2), Inches(8), Inches(3))
table = table_shape.table

# Headers
headers = ["Metric", "Q1", "Q2", "Q3"]
for i, header in enumerate(headers):
    cell = table.cell(0, i)
    cell.text = header
    for paragraph in cell.text_frame.paragraphs:
        paragraph.font.bold = True
        paragraph.font.size = Pt(14)
        paragraph.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    cell.fill.solid()
    cell.fill.fore_color.rgb = RGBColor(0x2B, 0x57, 0x9A)

# Data rows
data = [
    ["Revenue", "$312K", "$358K", "$401K"],
    ["Users", "1,200", "1,450", "1,680"],
    ["NPS", "72", "75", "78"],
    ["Churn", "3.2%", "2.8%", "2.1%"],
]
for r, row_data in enumerate(data, start=1):
    for c, value in enumerate(row_data):
        cell = table.cell(r, c)
        cell.text = value
        for paragraph in cell.text_frame.paragraphs:
            paragraph.font.size = Pt(12)
            if c > 0:
                paragraph.alignment = PP_ALIGN.CENTER
```

## Images and Shapes

```python
# Add an image
slide.shapes.add_picture("chart.png", Inches(1), Inches(2), width=Inches(6))

# Add a shape with text
from pptx.enum.shapes import MSO_SHAPE

shape = slide.shapes.add_shape(
    MSO_SHAPE.ROUNDED_RECTANGLE,
    Inches(1), Inches(5), Inches(3), Inches(1)
)
shape.fill.solid()
shape.fill.fore_color.rgb = RGBColor(0x2B, 0x57, 0x9A)
shape.text = "Key Takeaway"
shape.text_frame.paragraphs[0].font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
shape.text_frame.paragraphs[0].font.size = Pt(16)
shape.text_frame.paragraphs[0].font.bold = True
```

## Speaker Notes

```python
notes_slide = slide.notes_slide
notes_tf = notes_slide.notes_text_frame
notes_tf.text = "Key talking points:\n- Revenue grew 15% driven by enterprise\n- Churn reduction from product improvements"
```

## Cleanup Empty Placeholders

After adding slides, remove unfilled placeholders that show as "Click to add text":

```python
for slide in prs.slides:
    for shape in list(slide.shapes):
        if shape.has_text_frame and not shape.text_frame.text.strip():
            if shape.placeholder_format is not None:
                sp = shape._element
                sp.getparent().remove(sp)
```

## Design Guidelines

1. **One idea per slide** — don't cram multiple topics
2. **Max 5-6 bullet points** — less text, more visual
3. **Charts over tables** for numerical data — easier to scan
4. **Consistent colors** — use the template's color theme, don't introduce random colors
5. **Speaker notes for detail** — keep slides clean, put detail in notes
6. **Title every slide** — the title should summarize the slide's point
7. **16:9 aspect ratio** — standard for modern presentations

## Workflow

1. **Start from template** — load the branded .pptx
2. **Plan slide structure** — outline before building (title, agenda, content slides, summary)
3. **Add slides using layouts** — use `prs.slide_layouts[N]` not blank slides
4. **Fill content** — text, charts, tables, images
5. **Add speaker notes** — talking points for each slide
6. **Clean up** — remove empty placeholders
7. **Save and upload to Slack**

## Anti-Patterns

- Don't use blank slides when a layout with placeholders is available
- Don't add more than 6 bullet points per slide — split into two slides
- Don't use tables for data that should be a chart
- Don't hardcode colors — use the template's theme colors
- Don't skip speaker notes — they're essential for the presenter
- Don't forget to clean up empty "Click to add text" placeholders
- Don't create presentations without a clear narrative arc (problem → data → insight → action)
