"""Page rendering -- converts PDF pages to PNG images via pypdfium2."""

import argparse
import os
import sys

import pypdfium2 as pdfium
from rich.console import Console
from rich.progress import Progress

console = Console(stderr=True)


def render_pdf(pdf_path: str, output_dir: str, dpi: int = 150) -> dict:
    os.makedirs(output_dir, exist_ok=True)

    pdf = pdfium.PdfDocument(pdf_path)
    num_pages = len(pdf)
    scale = dpi / 72.0
    stats = {"pages": num_pages, "dpi": dpi}

    with Progress(console=console) as progress:
        task = progress.add_task("Rendering pages", total=num_pages)

        for i in range(num_pages):
            page = pdf[i]
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil()

            img_path = os.path.join(output_dir, f"page_{i+1:04d}.png")
            image.save(img_path, "PNG")
            progress.advance(task)

    return stats


def main():
    parser = argparse.ArgumentParser(description="Render PDF pages as PNG images")
    parser.add_argument("pdf", help="Path to PDF file")
    parser.add_argument("--output", "-o", default=None, help="Output directory")
    parser.add_argument("--dpi", type=int, default=150, help="Rendering DPI (default: 150)")
    args = parser.parse_args()

    if not os.path.isfile(args.pdf):
        console.print(f"[red]File not found: {args.pdf}[/red]")
        sys.exit(1)

    output_dir = args.output
    if not output_dir:
        base = os.path.splitext(os.path.basename(args.pdf))[0]
        output_dir = os.path.join("tmp", base, "pages")

    stats = render_pdf(args.pdf, output_dir, dpi=args.dpi)

    console.print(f"\n[bold green]Done.[/bold green] Rendered {stats['pages']} pages at {stats['dpi']} DPI to {output_dir}/")


if __name__ == "__main__":
    main()
