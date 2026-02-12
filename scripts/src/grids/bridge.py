"""Bridge between Python orchestration and Rust layout engine.

Serializes agent layout decisions into the JSON format consumed by
the Rust grids-layout library (Grid, Page, Block, DecisionTree).
Also invokes the Rust binary for SVG/LaTeX rendering.
"""

import json
import os
import re
import subprocess
from typing import Any


def parse_layout_output(layout_text: str) -> list[dict]:
    """Extract page specifications from agent layout output.

    The layout agent outputs JSON page specs. This function
    extracts them from the (possibly markdown-wrapped) response.
    """
    pages = []

    # Try to find JSON arrays or objects in the text
    # Look for ```json blocks first
    json_blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", layout_text, re.DOTALL)
    for block in json_blocks:
        try:
            parsed = json.loads(block)
            if isinstance(parsed, list):
                pages.extend(parsed)
            elif isinstance(parsed, dict):
                if "pages" in parsed:
                    pages.extend(parsed["pages"])
                else:
                    pages.append(parsed)
        except json.JSONDecodeError:
            continue

    # If no JSON blocks found, try the whole text
    if not pages:
        try:
            parsed = json.loads(layout_text)
            if isinstance(parsed, list):
                pages = parsed
            elif isinstance(parsed, dict) and "pages" in parsed:
                pages = parsed["pages"]
        except json.JSONDecodeError:
            pass

    return pages


def layout_to_rust_json(pages: list[dict], project_id: str = "session") -> dict:
    """Convert parsed layout specs into the Rust-compatible JSON format.

    Matches the serde format of:
    - Page { number, size, grid, blocks }
    - Block { id, col, row, col_span, row_span, content, decision_ids }
    - BlockContent::Text / BlockContent::Image / BlockContent::Empty
    """
    rust_pages = []
    block_counter = 0

    for i, page_spec in enumerate(pages):
        page_num = page_spec.get("number", i + 1)

        size = page_spec.get("size", "HalfLetter")
        if isinstance(size, str):
            size_json = size
        elif isinstance(size, dict):
            size_json = {"Custom": {"width": size.get("width", 396.0), "height": size.get("height", 612.0)}}
        else:
            size_json = "HalfLetter"

        columns = page_spec.get("columns", 6)
        rows = page_spec.get("rows", 8)

        blocks = []
        for block_spec in page_spec.get("blocks", []):
            block_counter += 1
            block_id = block_spec.get("id", f"block-{block_counter:04d}")

            content_type = block_spec.get("content_type", block_spec.get("type", "empty"))
            content = _build_block_content(content_type, block_spec)

            blocks.append({
                "id": block_id,
                "col": block_spec.get("col", 0),
                "row": block_spec.get("row", 0),
                "col_span": block_spec.get("col_span", 1),
                "row_span": block_spec.get("row_span", 1),
                "content": content,
                "decision_ids": block_spec.get("decision_ids", []),
            })

        rust_pages.append({
            "number": page_num,
            "size": size_json,
            "grid": {
                "columns": columns,
                "rows": rows,
            },
            "blocks": blocks,
        })

    return {
        "project_id": project_id,
        "pages": rust_pages,
    }


def _build_block_content(content_type: str, spec: dict) -> dict:
    """Build Rust-compatible BlockContent JSON."""
    ct = content_type.lower()
    if ct in ("text", "heading", "body", "caption"):
        return {
            "type": "Text",
            "body": spec.get("content", spec.get("body", spec.get("text", ""))),
            "style": {
                "font_size": spec.get("font_size", 10.0),
                "font_family": spec.get("font_family", "Helvetica"),
                "line_height": spec.get("line_height", 1.4),
                "weight": spec.get("weight", "bold" if ct == "heading" else "normal"),
            },
        }
    elif ct in ("image", "figure", "illustration"):
        return {
            "type": "Image",
            "path": spec.get("path", spec.get("image", "")),
            "alt": spec.get("alt", spec.get("description", "")),
        }
    else:
        return {"type": "Empty"}


def save_layout_json(rust_json: dict, output_path: str):
    """Write the Rust-compatible layout JSON to disk."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(rust_json, f, indent=2)
    return output_path
