"""Figure extraction -- extracts embedded images and renders figure regions from PDFs."""

import argparse
import json
import os
import sys

import pypdfium2 as pdfium
from rich.console import Console
from rich.progress import Progress

console = Console(stderr=True)


def extract_figures(pdf_path: str, output_dir: str, min_size: int = 50) -> dict:
    os.makedirs(output_dir, exist_ok=True)

    pdf = pdfium.PdfDocument(pdf_path)
    stats = {"pages": len(pdf), "figures_extracted": 0}
    manifest = []

    with Progress(console=console) as progress:
        task = progress.add_task("Extracting figures", total=len(pdf))

        for page_idx in range(len(pdf)):
            page = pdf[page_idx]
            page_num = page_idx + 1

            # Try to extract embedded image objects
            try:
                obj_count = page.get_object_count()
                for obj_idx in range(obj_count):
                    obj = page.get_object(obj_idx)
                    if obj.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE:
                        try:
                            bitmap = obj.get_bitmap()
                            img = bitmap.to_pil()
                            w, h = img.size

                            if w >= min_size and h >= min_size:
                                stats["figures_extracted"] += 1
                                fig_id = f"page{page_num:04d}_fig{stats['figures_extracted']:04d}"
                                img_path = os.path.join(output_dir, f"{fig_id}.png")
                                img.save(img_path, "PNG")

                                entry = {
                                    "id": fig_id,
                                    "page": page_num,
                                    "width": w,
                                    "height": h,
                                    "file": f"{fig_id}.png",
                                    "source": "embedded",
                                }
                                manifest.append(entry)
                        except Exception:
                            continue
            except Exception:
                continue

            progress.advance(task)

    # Write manifest
    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    return stats


def main():
    parser = argparse.ArgumentParser(description="Extract figures and images from a PDF")
    parser.add_argument("pdf", help="Path to PDF file")
    parser.add_argument("--output", "-o", default=None, help="Output directory")
    parser.add_argument("--min-size", type=int, default=50, help="Minimum image dimension in px (default: 50)")
    args = parser.parse_args()

    if not os.path.isfile(args.pdf):
        console.print(f"[red]File not found: {args.pdf}[/red]")
        sys.exit(1)

    output_dir = args.output
    if not output_dir:
        base = os.path.splitext(os.path.basename(args.pdf))[0]
        output_dir = os.path.join("tmp", base, "figures")

    stats = extract_figures(args.pdf, output_dir, min_size=args.min_size)

    console.print(f"\n[bold green]Done.[/bold green]")
    console.print(f"  Pages scanned: {stats['pages']}")
    console.print(f"  Figures extracted: {stats['figures_extracted']}")
    console.print(f"  Output: {output_dir}/")


if __name__ == "__main__":
    main()
