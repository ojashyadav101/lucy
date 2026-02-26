"""Edit format helpers: SEARCH/REPLACE parsing and diff application.

Provides utilities for parsing and applying structured code edits
in the SEARCH/REPLACE format used by Aider, Cursor, and CodeBuddy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()


@dataclass
class EditBlock:
    """A single SEARCH/REPLACE edit operation."""

    search: str
    replace: str
    file_path: str = ""


def parse_search_replace_blocks(text: str) -> list[EditBlock]:
    """Parse SEARCH/REPLACE blocks from LLM output.

    Supports formats:
      <<<<<<< SEARCH
      old code
      =======
      new code
      >>>>>>> REPLACE

    And the simpler marker-based format:
      SEARCH:
      ```
      old code
      ```
      REPLACE:
      ```
      new code
      ```
    """
    blocks: list[EditBlock] = []

    marker_pattern = re.compile(
        r"<<<<<<+\s*SEARCH\s*\n(.*?)\n=======+\s*\n(.*?)\n>>>>>>>+\s*REPLACE",
        re.DOTALL,
    )
    for m in marker_pattern.finditer(text):
        blocks.append(EditBlock(
            search=m.group(1),
            replace=m.group(2),
        ))

    if not blocks:
        simple_pattern = re.compile(
            r"SEARCH:\s*\n```[^\n]*\n(.*?)\n```\s*\n"
            r"REPLACE:\s*\n```[^\n]*\n(.*?)\n```",
            re.DOTALL,
        )
        for m in simple_pattern.finditer(text):
            blocks.append(EditBlock(
                search=m.group(1),
                replace=m.group(2),
            ))

    return blocks


def apply_edit_block(content: str, block: EditBlock) -> str | None:
    """Apply a single edit block to file content.

    Returns the modified content, or None if the search string was not found.
    """
    if block.search not in content:
        search_stripped = block.search.strip()
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if search_stripped.split("\n")[0].strip() in line:
                logger.debug(
                    "edit_block_fuzzy_match",
                    line=i + 1,
                    search_preview=block.search[:50],
                )
                break
        return None

    count = content.count(block.search)
    if count > 1:
        logger.warning(
            "edit_block_ambiguous",
            matches=count,
            search_preview=block.search[:80],
        )
        return None

    return content.replace(block.search, block.replace, 1)
