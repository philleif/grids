"""Extract document structure -- TOC, chapters, sections from PDF bookmarks and text."""

import argparse
import json
import os
import re
import sys

from pypdf import PdfReader
from rich.console import Console

console = Console(stderr=True)


def extract_structure(pdf_path: str, text_dir: str | None = None) -> dict:
    reader = PdfReader(pdf_path)
    meta = reader.metadata

    result = {
        "title": str(meta.title) if meta and meta.title else None,
        "author": str(meta.author) if meta and meta.author else None,
        "pages": len(reader.pages),
        "toc": [],
        "sections": [],
    }

    # Extract bookmarks as TOC
    try:
        outline = reader.outline
        if outline:
            result["toc"] = _parse_outline(outline, reader)
    except Exception:
        pass

    # If no bookmarks, try to infer structure from extracted text
    if not result["toc"] and text_dir:
        result["sections"] = _infer_sections_from_text(text_dir)
    elif result["toc"]:
        result["sections"] = _toc_to_sections(result["toc"], len(reader.pages))

    return result


def _parse_outline(outline, reader, level=0) -> list:
    items = []
    for entry in outline:
        if isinstance(entry, list):
            items.extend(_parse_outline(entry, reader, level + 1))
        else:
            title = str(entry.title) if hasattr(entry, "title") else str(entry)
            page_num = None
            try:
                page_num = reader.get_destination_page_number(entry) + 1
            except Exception:
                pass
            items.append({"level": level, "title": title.strip(), "page": page_num})
    return items


def _toc_to_sections(toc: list, total_pages: int) -> list:
    sections = []
    for i, entry in enumerate(toc):
        page_start = entry.get("page")
        if page_start is None:
            continue
        # End page is the next section's start - 1, or total pages
        page_end = total_pages
        for j in range(i + 1, len(toc)):
            if toc[j].get("page") is not None and toc[j]["level"] <= entry["level"]:
                page_end = toc[j]["page"] - 1
                break

        section_id = re.sub(r"[^a-z0-9]+", "-", entry["title"].lower()).strip("-")
        sections.append({
            "id": section_id,
            "title": entry["title"],
            "level": entry["level"],
            "page_start": page_start,
            "page_end": page_end,
        })
    return sections


def _infer_sections_from_text(text_dir: str) -> list:
    sections = []
    heading_patterns = [
        re.compile(r"^(?:chapter|part)\s+\d+", re.IGNORECASE),
        re.compile(r"^\d+\.\s+\S"),
        re.compile(r"^[A-Z][A-Z\s]{5,}$"),
    ]

    page_files = sorted(
        f for f in os.listdir(text_dir) if f.startswith("page_") and f.endswith(".md")
    )

    for page_file in page_files:
        page_num = int(page_file.replace("page_", "").replace(".md", ""))
        path = os.path.join(text_dir, page_file)

        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        for line in lines[:10]:
            line = line.strip().lstrip("# ")
            if not line or line.startswith("Page "):
                continue
            for pattern in heading_patterns:
                if pattern.match(line):
                    section_id = re.sub(r"[^a-z0-9]+", "-", line.lower()).strip("-")
                    sections.append({
                        "id": section_id,
                        "title": line,
                        "level": 0,
                        "page_start": page_num,
                        "page_end": None,
                    })
                    break

    # Fill in page_end values
    for i, sec in enumerate(sections):
        if i + 1 < len(sections):
            sec["page_end"] = sections[i + 1]["page_start"] - 1

    return sections


def main():
    parser = argparse.ArgumentParser(description="Extract document structure from a PDF")
    parser.add_argument("pdf", help="Path to PDF file")
    parser.add_argument("--text-dir", default=None, help="Directory with extracted page text (for fallback heading detection)")
    parser.add_argument("--output", "-o", default=None, help="Output JSON file")
    args = parser.parse_args()

    if not os.path.isfile(args.pdf):
        console.print(f"[red]File not found: {args.pdf}[/red]")
        sys.exit(1)

    result = extract_structure(args.pdf, text_dir=args.text_dir)

    output_path = args.output
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        console.print(f"[green]Structure written to {output_path}[/green]")
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
