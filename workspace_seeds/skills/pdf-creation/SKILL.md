---
name: pdf-creation
description: Creates PDF documents from HTML/CSS. Use when creating PDFs, reports, invoices, or formatted documents.
---

# PDF Creation

Generate professional PDF documents using Python libraries in the code execution sandbox.

## Approach

Use `COMPOSIO_REMOTE_WORKBENCH` to run Python scripts that generate PDFs.

### Recommended Libraries
- **WeasyPrint** — HTML/CSS to PDF (best for styled documents)
- **reportlab** — Programmatic PDF generation (best for data-heavy reports)
- **fpdf2** — Simple PDF generation (lightweight)

## Workflow

1. **Understand the requirements**: What content, layout, and styling are needed?
2. **Write the HTML/CSS template**: Design the document structure
3. **Generate the PDF**: Run the script via code execution
4. **Share the result**: Upload to Slack or save to a connected service

## Best Practices

- Use CSS for styling — it's more maintainable than programmatic styling
- Include page numbers and headers/footers for multi-page documents
- Test with real data before sharing the final version
- For branded documents, ask for or reference the company's style guide
- Handle long content gracefully — test page breaks
