"""PDF profiling and triage -- analyze a PDF to determine the best processing path."""

import argparse
import json
import os
import sys

from pypdf import PdfReader
from rich.console import Console
from rich.table import Table

console = Console(stderr=True)


def profile_pdf(path: str) -> dict:
    file_size = os.path.getsize(path)
    reader = PdfReader(path)
    num_pages = len(reader.pages)
    meta = reader.metadata

    # Sample pages for text layer detection
    sample_indices = [0]
    if num_pages > 2:
        sample_indices.append(num_pages // 2)
    if num_pages > 1:
        sample_indices.append(num_pages - 1)

    text_samples = {}
    pages_with_text = 0
    for idx in sample_indices:
        page = reader.pages[idx]
        text = (page.extract_text() or "").strip()
        has_text = len(text) > 20
        text_samples[idx + 1] = {
            "has_text": has_text,
            "char_count": len(text),
            "sample": text[:200] if text else "",
        }
        if has_text:
            pages_with_text += 1

    # Bookmarks / outline
    bookmarks = []
    try:
        outline = reader.outline
        if outline:
            bookmarks = _flatten_outline(outline)
    except Exception:
        pass

    # Form fields
    has_forms = False
    form_field_count = 0
    try:
        if reader.get_fields():
            has_forms = True
            form_field_count = len(reader.get_fields())
    except Exception:
        pass

    # Determine pipeline recommendation
    text_ratio = pages_with_text / len(sample_indices) if sample_indices else 0
    if text_ratio >= 0.8:
        recommendation = "text-extract"
    elif text_ratio == 0:
        recommendation = "ocr-required"
    else:
        recommendation = "mixed"

    return {
        "file": os.path.basename(path),
        "path": os.path.abspath(path),
        "file_size_bytes": file_size,
        "file_size_human": _human_size(file_size),
        "pages": num_pages,
        "pdf_version": reader.pdf_header if hasattr(reader, "pdf_header") else None,
        "metadata": {
            "title": str(meta.title) if meta and meta.title else None,
            "author": str(meta.author) if meta and meta.author else None,
            "subject": str(meta.subject) if meta and meta.subject else None,
            "creator": str(meta.creator) if meta and meta.creator else None,
        },
        "text_layer": {
            "sampled_pages": text_samples,
            "pages_with_text": pages_with_text,
            "pages_sampled": len(sample_indices),
            "text_ratio": text_ratio,
        },
        "bookmarks": bookmarks[:20],
        "bookmark_count": len(bookmarks),
        "has_forms": has_forms,
        "form_field_count": form_field_count,
        "recommendation": recommendation,
    }


def _flatten_outline(outline, level=0) -> list:
    items = []
    for entry in outline:
        if isinstance(entry, list):
            items.extend(_flatten_outline(entry, level + 1))
        else:
            title = str(entry.title) if hasattr(entry, "title") else str(entry)
            items.append({"level": level, "title": title})
    return items


def _human_size(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024:
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} TB"


def print_report(report: dict) -> None:
    table = Table(title=f"PDF Profile: {report['file']}", show_lines=True)
    table.add_column("Property", style="bold cyan")
    table.add_column("Value")

    table.add_row("Pages", str(report["pages"]))
    table.add_row("File Size", report["file_size_human"])
    table.add_row("Title", report["metadata"]["title"] or "(none)")
    table.add_row("Author", report["metadata"]["author"] or "(none)")
    table.add_row("Text Layer", f"{report['text_layer']['text_ratio']:.0%} of sampled pages")
    table.add_row("Bookmarks", str(report["bookmark_count"]))
    table.add_row("Form Fields", str(report["form_field_count"]))
    table.add_row("Recommendation", f"[bold green]{report['recommendation']}[/bold green]")

    console.print(table)


def main():
    parser = argparse.ArgumentParser(description="Profile a PDF for GRIDS processing")
    parser.add_argument("pdf", help="Path to PDF file")
    parser.add_argument("--json", action="store_true", help="Output raw JSON instead of table")
    args = parser.parse_args()

    if not os.path.isfile(args.pdf):
        console.print(f"[red]File not found: {args.pdf}[/red]")
        sys.exit(1)

    report = profile_pdf(args.pdf)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
