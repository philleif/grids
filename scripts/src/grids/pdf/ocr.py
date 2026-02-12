"""OCR pipeline -- converts PDF pages to images and runs Tesseract."""

import argparse
import os
import sys

import pypdfium2 as pdfium
from PIL import Image
from rich.console import Console
from rich.progress import Progress

console = Console(stderr=True)


def ocr_page_image(image: Image.Image, lang: str = "eng") -> str:
    import pytesseract
    return pytesseract.image_to_string(image, lang=lang, config="--oem 1 --psm 6")


def ocr_page_from_pdf(pdf_path: str, page_index: int, dpi: int = 300, lang: str = "eng") -> str:
    pdf = pdfium.PdfDocument(pdf_path)
    page = pdf[page_index]
    scale = dpi / 72.0
    bitmap = page.render(scale=scale)
    image = bitmap.to_pil()
    return ocr_page_image(image, lang=lang)


def ocr_pdf(
    pdf_path: str,
    output_dir: str,
    dpi: int = 300,
    lang: str = "eng",
    skip_text_pages: bool = True,
) -> dict:
    os.makedirs(output_dir, exist_ok=True)

    pdf = pdfium.PdfDocument(pdf_path)
    num_pages = len(pdf)
    stats = {"pages": num_pages, "ocr_pages": 0, "skipped_pages": 0, "total_chars": 0}
    all_text_parts = []

    # Optionally check for existing text layer
    text_layer_pages = set()
    if skip_text_pages:
        try:
            from pypdf import PdfReader
            reader = PdfReader(pdf_path)
            for i, page in enumerate(reader.pages):
                text = (page.extract_text() or "").strip()
                if len(text) > 20:
                    text_layer_pages.add(i)
        except Exception:
            pass

    scale = dpi / 72.0

    with Progress(console=console) as progress:
        task = progress.add_task("OCR processing", total=num_pages)

        for i in range(num_pages):
            page_num = i + 1

            if i in text_layer_pages:
                # Use existing text layer
                from pypdf import PdfReader
                reader = PdfReader(pdf_path)
                text = reader.pages[i].extract_text() or ""
                stats["skipped_pages"] += 1
            else:
                page = pdf[i]
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil()

                # Save page image
                img_path = os.path.join(output_dir, f"page_{page_num:04d}.png")
                image.save(img_path, "PNG")

                # OCR
                text = ocr_page_image(image, lang=lang)
                stats["ocr_pages"] += 1

            stats["total_chars"] += len(text)

            # Write per-page text
            page_md = f"# Page {page_num}\n\n{text}\n"
            page_file = os.path.join(output_dir, f"page_{page_num:04d}.md")
            with open(page_file, "w", encoding="utf-8") as f:
                f.write(page_md)

            all_text_parts.append(page_md)
            progress.advance(task)

    # Write combined
    full_path = os.path.join(output_dir, "full.md")
    with open(full_path, "w", encoding="utf-8") as f:
        f.write("\n---\n\n".join(all_text_parts))

    return stats


def main():
    parser = argparse.ArgumentParser(description="OCR a PDF document")
    parser.add_argument("pdf", help="Path to PDF file")
    parser.add_argument("--output", "-o", default=None, help="Output directory")
    parser.add_argument("--dpi", type=int, default=300, help="Rendering DPI (default: 300)")
    parser.add_argument("--lang", default="eng", help="Tesseract language (default: eng)")
    parser.add_argument("--force-ocr", action="store_true", help="OCR all pages even if text layer exists")
    args = parser.parse_args()

    if not os.path.isfile(args.pdf):
        console.print(f"[red]File not found: {args.pdf}[/red]")
        sys.exit(1)

    output_dir = args.output
    if not output_dir:
        base = os.path.splitext(os.path.basename(args.pdf))[0]
        output_dir = os.path.join("tmp", base, "ocr")

    stats = ocr_pdf(
        args.pdf, output_dir,
        dpi=args.dpi, lang=args.lang,
        skip_text_pages=not args.force_ocr,
    )

    console.print(f"\n[bold green]Done.[/bold green]")
    console.print(f"  Pages: {stats['pages']}")
    console.print(f"  OCR'd: {stats['ocr_pages']}")
    console.print(f"  Skipped (had text): {stats['skipped_pages']}")
    console.print(f"  Total chars: {stats['total_chars']:,}")
    console.print(f"  Output: {output_dir}/")


if __name__ == "__main__":
    main()
