---
name: pdf-signing
description: Adds digital signatures to PDFs. Use when signing PDFs or adding signatures to documents.
---

# PDF Signing

Add digital signatures or signature images to PDF documents.

## Approach

Use `COMPOSIO_REMOTE_WORKBENCH` to run signing scripts:
- **pyHanko** — cryptographic PDF signatures (X.509 certificates)
- **Pillow + reportlab** — visual signature overlay (image-based)

## Workflow

1. Determine signing requirements (visual signature vs cryptographic)
2. For visual: overlay a signature image at the specified position
3. For cryptographic: use certificate-based signing
4. Verify the signature after applying
5. Share the signed document

## Important Notes

- Cryptographic signatures require proper certificates
- Visual signature overlays are not legally binding digital signatures
- Always confirm with the user what type of signing they need
