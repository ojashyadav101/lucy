"""File generation tools for Lucy.

Generates PDF, Excel, and CSV files from structured data,
then uploads them to Slack for sharing.

Architecture:
    - PDF: HTML → WeasyPrint (pip install weasyprint)
    - Excel: openpyxl (pip install openpyxl)
    - CSV: stdlib csv module
    - Upload: Slack files_upload_v2 API

All files are generated in a temp directory, uploaded, then cleaned up.
The agent sees these as internal tools (lucy_* prefix).

Dependencies (add to pyproject.toml):
    weasyprint >= 62.0
    openpyxl >= 3.1.0
"""

from __future__ import annotations

import csv
import io
import json
import tempfile
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

# ═══════════════════════════════════════════════════════════════════════════
# PDF Generation
# ═══════════════════════════════════════════════════════════════════════════

_PDF_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<style>
  @page {{ size: A4; margin: 2cm; }}
  body {{ font-family: 'Helvetica Neue', Arial, sans-serif; line-height: 1.6;
         color: #1a1a1a; font-size: 10pt; }}
  h1 {{ color: #1e3a5f; font-size: 20pt; border-bottom: 2px solid #1e3a5f;
       padding-bottom: 6px; }}
  h2 {{ color: #2a5a8f; font-size: 14pt; margin-top: 20px; }}
  h3 {{ color: #333; font-size: 11pt; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0; }}
  th {{ background: #f0f2f5; padding: 8px; text-align: left; font-weight: 600;
       border-bottom: 2px solid #ddd; }}
  td {{ padding: 8px; border-bottom: 1px solid #eee; }}
  code {{ background: #f0f2f5; padding: 2px 6px; border-radius: 3px;
         font-family: monospace; font-size: 9pt; }}
  pre {{ background: #1e1e2e; color: #cdd6f4; padding: 14px;
        border-radius: 6px; font-size: 9pt; overflow-x: auto; }}
  .header {{ text-align: center; margin-bottom: 20px; }}
  .footer {{ text-align: center; margin-top: 30px; color: #888;
            font-size: 8pt; }}
</style>
</head>
<body>
{content}
</body>
</html>"""


async def generate_pdf(
    title: str,
    content_html: str,
    filename: str | None = None,
) -> Path:
    """Generate a PDF from HTML content.

    Args:
        title: Document title (used in filename if none provided).
        content_html: HTML body content (can include h1, h2, tables, etc).
        filename: Output filename. Auto-generated from title if None.

    Returns:
        Path to the generated PDF file.
    """
    try:
        from weasyprint import HTML
    except ImportError:
        raise RuntimeError(
            "weasyprint not installed. Add to pyproject.toml: "
            "weasyprint >= 62.0"
        )

    full_html = _PDF_TEMPLATE.format(content=content_html)

    if not filename:
        safe_title = "".join(
            c if c.isalnum() or c in " -_" else ""
            for c in title
        ).strip().replace(" ", "_")[:50]
        filename = f"{safe_title}.pdf"

    output_path = Path(tempfile.mkdtemp()) / filename
    HTML(string=full_html).write_pdf(str(output_path))

    logger.info(
        "pdf_generated",
        title=title,
        path=str(output_path),
        size_kb=round(output_path.stat().st_size / 1024, 1),
    )
    return output_path


# ═══════════════════════════════════════════════════════════════════════════
# Excel Generation
# ═══════════════════════════════════════════════════════════════════════════

async def generate_excel(
    title: str,
    sheets: dict[str, list[list[Any]]],
    filename: str | None = None,
) -> Path:
    """Generate an Excel file from tabular data.

    Args:
        title: Workbook title (used in filename).
        sheets: Dict of sheet_name → list of rows (first row = headers).
            Example: {"Revenue": [["Month", "MRR"], ["Jan", 18000], ...]}
        filename: Output filename. Auto-generated if None.

    Returns:
        Path to the generated .xlsx file.
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    except ImportError:
        raise RuntimeError(
            "openpyxl not installed. Add to pyproject.toml: "
            "openpyxl >= 3.1.0"
        )

    wb = Workbook()

    header_font = Font(bold=True, size=11)
    header_fill = PatternFill(
        start_color="F0F2F5", end_color="F0F2F5", fill_type="solid",
    )
    thin_border = Border(
        bottom=Side(style="thin", color="DDDDDD"),
    )

    for i, (sheet_name, rows) in enumerate(sheets.items()):
        if i == 0:
            ws = wb.active
            ws.title = sheet_name
        else:
            ws = wb.create_sheet(title=sheet_name)

        for row_idx, row in enumerate(rows):
            for col_idx, value in enumerate(row):
                cell = ws.cell(
                    row=row_idx + 1, column=col_idx + 1, value=value,
                )
                if row_idx == 0:
                    cell.font = header_font
                    cell.fill = header_fill
                cell.border = thin_border

        for col_idx in range(len(rows[0]) if rows else 0):
            max_length = 0
            col_letter = ws.cell(row=1, column=col_idx + 1).column_letter
            for row in rows:
                if col_idx < len(row):
                    max_length = max(max_length, len(str(row[col_idx])))
            ws.column_dimensions[col_letter].width = min(max_length + 4, 50)

    if not filename:
        safe_title = "".join(
            c if c.isalnum() or c in " -_" else ""
            for c in title
        ).strip().replace(" ", "_")[:50]
        filename = f"{safe_title}.xlsx"

    output_path = Path(tempfile.mkdtemp()) / filename
    wb.save(str(output_path))

    logger.info(
        "excel_generated",
        title=title,
        sheets=list(sheets.keys()),
        path=str(output_path),
    )
    return output_path


# ═══════════════════════════════════════════════════════════════════════════
# CSV Generation
# ═══════════════════════════════════════════════════════════════════════════

async def generate_csv(
    title: str,
    rows: list[list[Any]],
    filename: str | None = None,
) -> Path:
    """Generate a CSV file from tabular data.

    Args:
        title: Used in filename if none provided.
        rows: List of rows (first row = headers).
        filename: Output filename. Auto-generated if None.

    Returns:
        Path to the generated .csv file.
    """
    if not filename:
        safe_title = "".join(
            c if c.isalnum() or c in " -_" else ""
            for c in title
        ).strip().replace(" ", "_")[:50]
        filename = f"{safe_title}.csv"

    output_path = Path(tempfile.mkdtemp()) / filename

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for row in rows:
            writer.writerow(row)

    logger.info("csv_generated", title=title, rows=len(rows))
    return output_path


# ═══════════════════════════════════════════════════════════════════════════
# Slack Upload
# ═══════════════════════════════════════════════════════════════════════════

async def upload_file_to_slack(
    slack_client: Any,
    file_path: Path,
    channel_id: str,
    thread_ts: str | None = None,
    title: str | None = None,
    comment: str | None = None,
) -> dict[str, Any]:
    """Upload a file to Slack and post it to a channel/thread.

    Args:
        slack_client: Slack Bolt async client.
        file_path: Path to the file to upload.
        channel_id: Target channel.
        thread_ts: Thread to post in (optional).
        title: Display title for the file.
        comment: Message text to accompany the file.

    Returns:
        Slack API response dict.
    """
    kwargs: dict[str, Any] = {
        "channel": channel_id,
        "file": str(file_path),
        "filename": file_path.name,
    }
    if title:
        kwargs["title"] = title
    if comment:
        kwargs["initial_comment"] = comment
    if thread_ts:
        kwargs["thread_ts"] = thread_ts

    try:
        result = await slack_client.files_upload_v2(**kwargs)
        logger.info(
            "file_uploaded_to_slack",
            filename=file_path.name,
            channel=channel_id,
        )
        return result
    except Exception as e:
        logger.error(
            "slack_file_upload_failed",
            filename=file_path.name,
            error=str(e),
        )
        try:
            result = await slack_client.files_upload(
                channels=channel_id,
                file=str(file_path),
                filename=file_path.name,
                title=title or file_path.name,
                initial_comment=comment or "",
                thread_ts=thread_ts,
            )
            return result
        except Exception as e2:
            logger.error("slack_file_upload_v1_failed", error=str(e2))
            raise


# ═══════════════════════════════════════════════════════════════════════════
# Tool Definitions (for agent integration)
# ═══════════════════════════════════════════════════════════════════════════

def get_file_tool_definitions() -> list[dict[str, Any]]:
    """Return OpenAI-format tool definitions for file generation.

    Registered as internal tools alongside Slack history search.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "lucy_generate_pdf",
                "description": (
                    "Generate a formatted PDF document. Use when the user asks "
                    "for a report, document, analysis, or any output that would "
                    "be better as a downloadable file than a Slack message. "
                    "Provide HTML content for the body (h1, h2, p, table, ul, etc)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Document title. Appears in filename.",
                        },
                        "content_html": {
                            "type": "string",
                            "description": (
                                "HTML body content. Use h1/h2 for headers, "
                                "table/tr/td for data, p for paragraphs, "
                                "ul/li for lists, code/pre for code."
                            ),
                        },
                    },
                    "required": ["title", "content_html"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_generate_excel",
                "description": (
                    "Generate an Excel spreadsheet. Use when the user asks for "
                    "data in a spreadsheet, or when tabular data would be more "
                    "useful as a downloadable file."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Workbook title. Used in filename.",
                        },
                        "sheets": {
                            "type": "object",
                            "description": (
                                "Object mapping sheet names to row arrays. "
                                "First row in each sheet is the header row. "
                                'Example: {"Revenue": [["Month", "MRR"], '
                                '["Jan", 18000]]}'
                            ),
                            "additionalProperties": {
                                "type": "array",
                                "items": {
                                    "type": "array",
                                    "items": {},
                                },
                            },
                        },
                    },
                    "required": ["title", "sheets"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_generate_csv",
                "description": (
                    "Generate a CSV file from tabular data. Use for simple "
                    "data exports that don't need Excel formatting."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Used in filename.",
                        },
                        "rows": {
                            "type": "array",
                            "description": (
                                "Array of row arrays. First row is headers. "
                                'Example: [["Name", "Email"], '
                                '["Alice", "a@b.com"]]'
                            ),
                            "items": {
                                "type": "array",
                                "items": {},
                            },
                        },
                    },
                    "required": ["title", "rows"],
                },
            },
        },
    ]


async def execute_file_tool(
    tool_name: str,
    parameters: dict[str, Any],
    slack_client: Any | None = None,
    channel_id: str | None = None,
    thread_ts: str | None = None,
) -> dict[str, Any]:
    """Execute a file generation tool and optionally upload to Slack.

    Returns:
        {"result": "...", "file_path": "/tmp/.../file.pdf"} on success.
        {"error": "..."} on failure.
    """
    try:
        if tool_name == "lucy_generate_pdf":
            path = await generate_pdf(
                title=parameters["title"],
                content_html=parameters["content_html"],
            )
        elif tool_name == "lucy_generate_excel":
            sheets = parameters.get("sheets", {})
            if isinstance(sheets, str):
                sheets = json.loads(sheets)
            path = await generate_excel(
                title=parameters["title"],
                sheets=sheets,
            )
        elif tool_name == "lucy_generate_csv":
            rows = parameters.get("rows", [])
            if isinstance(rows, str):
                rows = json.loads(rows)
            path = await generate_csv(
                title=parameters["title"],
                rows=rows,
            )
        else:
            return {"error": f"Unknown file tool: {tool_name}"}

        upload_result = None
        if slack_client and channel_id:
            upload_result = await upload_file_to_slack(
                slack_client=slack_client,
                file_path=path,
                channel_id=channel_id,
                thread_ts=thread_ts,
                title=parameters.get("title", path.name),
            )

        result = {
            "result": f"Generated {path.name} ({path.stat().st_size} bytes)",
            "file_path": str(path),
            "filename": path.name,
        }
        if upload_result:
            result["uploaded"] = True
            result["result"] += " — uploaded to Slack."

        return result

    except Exception as e:
        logger.error("file_tool_failed", tool=tool_name, error=str(e))
        return {"error": str(e)}
