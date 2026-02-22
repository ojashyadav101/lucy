---
name: pdf-form-filling
description: Fill out PDF forms programmatically. Use when completing PDF forms, applications, or templates with data.
---

# PDF Form Filling

Fill interactive PDF forms using Python in the code execution sandbox.

## Approach

Use `COMPOSIO_REMOTE_WORKBENCH` to run scripts with libraries like:
- **pdfrw** — read/write PDF form fields
- **PyPDF2** — merge and manipulate PDFs
- **fillpdf** — simple form filling

## Workflow

1. Read the PDF to discover form fields
2. Map data to form field names
3. Fill the form programmatically
4. Save and share the completed PDF

## Tips

- Always list form fields first before attempting to fill them
- Some PDFs have flattened forms (not fillable) — you may need to overlay text instead
- Test with a sample fill before processing bulk forms
