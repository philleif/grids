"""Smart text extraction -- uses pdfplumber for text-layer PDFs, falls back to OCR."""

import argparse
import os
import sys

import pdfplumber
from rich.console import Console
from rich.progress import Progress

console = Console(stderr=True)


def extract_text(pdf_path: str, output_dir: str, ocr_fallback: bool = True) -> dict:
    os.makedirs(output_dir, exist_ok=True)

    stats = {"pages": 0, "pages_with_text": 0, "pages_ocr": 0, "total_chars": 0}
    all_text_parts = []

    with pdfplumber.open(pdf_path) as pdf:
        stats["pages"] = len(pdf.pages)

        with Progress(console=console) as progress:
            task = progress.add_task("Extracting text", total=len(pdf.pages))

            for i, page in enumerate(pdf.pages):
                page_num = i + 1
                text = page.extract_text() or ""

                if len(text.strip()) < 20 and ocr_fallback:
                    try:
                        from grids.pdf.ocr import ocr_page_from_pdf
                        text = ocr_page_from_pdf(pdf_path, i)
                        if text.strip():
                            stats["pages_ocr"] += 1
                    except Exception as e:
                        console.print(f"[yellow]OCR failed for page {page_num}: {e}[/yellow]")

                if text.strip():
                    stats["pages_with_text"] += 1

                stats["total_chars"] += len(text)

                # Write per-page markdown
                page_md = f"# Page {page_num}\n\n{text}\n"
                page_file = os.path.join(output_dir, f"page_{page_num:04d}.md")
                with open(page_file, "w", encoding="utf-8") as f:
                    f.write(page_md)

                all_text_parts.append(page_md)
                progress.advance(task)

    # Write combined file
    full_path = os.path.join(output_dir, "full.md")
    with open(full_path, "w", encoding="utf-8") as f:
        f.write("\n---\n\n".join(all_text_parts))

    # Write tables if any
    _extract_tables(pdf_path, output_dir)

    return stats


def _extract_tables(pdf_path: str, output_dir: str) -> None:
    tables_dir = os.path.join(output_dir, "tables")
    table_count = 0

    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            try:
                tables = page.extract_tables()
                for j, table in enumerate(tables):
                    if table and len(table) > 1:
                        if table_count == 0:
                            os.makedirs(tables_dir, exist_ok=True)
                        table_count += 1
                        table_file = os.path.join(
                            tables_dir, f"page_{i+1:04d}_table_{j+1}.md"
                        )
                        with open(table_file, "w", encoding="utf-8") as f:
                            # Write as markdown table
                            header = table[0]
                            f.write("| " + " | ".join(str(c or "") for c in header) + " |\n")
                            f.write("| " + " | ".join("---" for _ in header) + " |\n")
                            for row in table[1:]:
                                f.write("| " + " | ".join(str(c or "") for c in row) + " |\n")
            except Exception:
                continue

    if table_count:
        console.print(f"[green]Extracted {table_count} tables to {tables_dir}[/green]")


def main():
    parser = argparse.ArgumentParser(description="Extract text from a PDF")
    parser.add_argument("pdf", help="Path to PDF file")
    parser.add_argument("--output", "-o", default=None, help="Output directory")
    parser.add_argument("--no-ocr", action="store_true", help="Disable OCR fallback")
    args = parser.parse_args()

    if not os.path.isfile(args.pdf):
        console.print(f"[red]File not found: {args.pdf}[/red]")
        sys.exit(1)

    output_dir = args.output
    if not output_dir:
        base = os.path.splitext(os.path.basename(args.pdf))[0]
        output_dir = os.path.join("tmp", base)

    stats = extract_text(args.pdf, output_dir, ocr_fallback=not args.no_ocr)

    console.print(f"\n[bold green]Done.[/bold green]")
    console.print(f"  Pages: {stats['pages']}")
    console.print(f"  With text: {stats['pages_with_text']}")
    console.print(f"  OCR'd: {stats['pages_ocr']}")
    console.print(f"  Total chars: {stats['total_chars']:,}")
    console.print(f"  Output: {output_dir}/")


if __name__ == "__main__":
    main()
