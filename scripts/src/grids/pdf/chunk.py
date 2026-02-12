"""Semantic chunking -- splits extracted text into AI-ready chunks aligned to document structure."""

import argparse
import json
import os
import re
import sys

from rich.console import Console

console = Console(stderr=True)

DEFAULT_MAX_CHARS = 4000
DEFAULT_OVERLAP_CHARS = 200


def chunk_text(
    text_path: str,
    structure_path: str | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP_CHARS,
    source_name: str | None = None,
) -> list[dict]:
    with open(text_path, "r", encoding="utf-8") as f:
        full_text = f.read()

    source = source_name or os.path.basename(text_path)

    # Parse page boundaries from markdown headers
    pages = _parse_pages(full_text)

    # Load structure if available
    sections = []
    if structure_path and os.path.isfile(structure_path):
        with open(structure_path, "r", encoding="utf-8") as f:
            structure = json.load(f)
        sections = structure.get("sections", [])

    # Chunk by sections if we have them, otherwise by pages
    if sections:
        chunks = _chunk_by_sections(pages, sections, max_chars, overlap, source)
    else:
        chunks = _chunk_by_pages(pages, max_chars, overlap, source)

    return chunks


def _parse_pages(text: str) -> list[dict]:
    pages = []
    parts = re.split(r"^# Page (\d+)\s*$", text, flags=re.MULTILINE)
    # parts = [preamble, page_num, content, page_num, content, ...]
    for i in range(1, len(parts), 2):
        page_num = int(parts[i])
        content = parts[i + 1].strip() if i + 1 < len(parts) else ""
        pages.append({"page": page_num, "text": content})
    return pages


def _find_section(page_num: int, sections: list) -> str | None:
    for sec in reversed(sections):
        start = sec.get("page_start")
        end = sec.get("page_end")
        if start is not None and page_num >= start:
            if end is None or page_num <= end:
                return sec.get("title")
    return None


def _chunk_by_sections(
    pages: list, sections: list, max_chars: int, overlap: int, source: str
) -> list[dict]:
    chunks = []
    chunk_id = 0

    for sec in sections:
        start = sec.get("page_start", 1)
        end = sec.get("page_end") or start

        # Gather text for this section
        section_text = ""
        for p in pages:
            if start <= p["page"] <= end:
                section_text += p["text"] + "\n\n"

        section_text = section_text.strip()
        if not section_text:
            continue

        # Split into chunks of max_chars with overlap
        for text_chunk, offset in _split_with_overlap(section_text, max_chars, overlap):
            chunk_id += 1
            # Estimate page range for this chunk
            page_start = start
            page_end = end
            chunks.append({
                "id": f"chunk-{chunk_id:04d}",
                "text": text_chunk,
                "metadata": {
                    "source": source,
                    "section": sec.get("title"),
                    "page_start": page_start,
                    "page_end": page_end,
                },
            })

    return chunks


def _chunk_by_pages(
    pages: list, max_chars: int, overlap: int, source: str
) -> list[dict]:
    chunks = []
    chunk_id = 0
    buffer = ""
    buffer_page_start = pages[0]["page"] if pages else 1
    buffer_page_end = buffer_page_start

    for p in pages:
        candidate = buffer + ("\n\n" if buffer else "") + p["text"]

        if len(candidate) > max_chars and buffer:
            chunk_id += 1
            chunks.append({
                "id": f"chunk-{chunk_id:04d}",
                "text": buffer.strip(),
                "metadata": {
                    "source": source,
                    "section": None,
                    "page_start": buffer_page_start,
                    "page_end": buffer_page_end,
                },
            })
            # Start new buffer with overlap
            if overlap > 0:
                buffer = buffer[-overlap:] + "\n\n" + p["text"]
            else:
                buffer = p["text"]
            buffer_page_start = p["page"]
        else:
            buffer = candidate

        buffer_page_end = p["page"]

    if buffer.strip():
        chunk_id += 1
        chunks.append({
            "id": f"chunk-{chunk_id:04d}",
            "text": buffer.strip(),
            "metadata": {
                "source": source,
                "section": None,
                "page_start": buffer_page_start,
                "page_end": buffer_page_end,
            },
        })

    return chunks


def _split_with_overlap(text: str, max_chars: int, overlap: int):
    if len(text) <= max_chars:
        yield text, 0
        return

    start = 0
    while start < len(text):
        end = start + max_chars
        chunk = text[start:end]

        # Try to break at a paragraph or sentence boundary
        if end < len(text):
            last_para = chunk.rfind("\n\n")
            if last_para > max_chars * 0.5:
                end = start + last_para
                chunk = text[start:end]
            else:
                last_period = chunk.rfind(". ")
                if last_period > max_chars * 0.5:
                    end = start + last_period + 1
                    chunk = text[start:end]

        yield chunk.strip(), start
        start = end - overlap
        if start >= len(text):
            break


def main():
    parser = argparse.ArgumentParser(description="Chunk extracted text for AI processing")
    parser.add_argument("text", help="Path to full.md extracted text")
    parser.add_argument("--structure", "-s", default=None, help="Path to structure.json")
    parser.add_argument("--output", "-o", default=None, help="Output JSONL file")
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="Max characters per chunk")
    parser.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP_CHARS, help="Overlap characters between chunks")
    parser.add_argument("--source", default=None, help="Source name for metadata")
    args = parser.parse_args()

    if not os.path.isfile(args.text):
        console.print(f"[red]File not found: {args.text}[/red]")
        sys.exit(1)

    chunks = chunk_text(
        args.text,
        structure_path=args.structure,
        max_chars=args.max_chars,
        overlap=args.overlap,
        source_name=args.source,
    )

    output_path = args.output
    if not output_path:
        output_path = os.path.splitext(args.text)[0] + ".chunks.jsonl"

    with open(output_path, "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk) + "\n")

    console.print(f"[green]Wrote {len(chunks)} chunks to {output_path}[/green]")


if __name__ == "__main__":
    main()
