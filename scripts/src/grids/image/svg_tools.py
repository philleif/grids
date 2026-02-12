"""Inkscape CLI wrapper for SVG manipulation.

Handles boolean operations, path simplification, text-to-path
conversion, and SVG cleanup for print production.
"""

import argparse
import os
import shutil
import subprocess
import sys

from rich.console import Console

console = Console(stderr=True)


def _inkscape() -> str:
    """Find the Inkscape binary."""
    for path in [
        shutil.which("inkscape"),
        "/Applications/Inkscape.app/Contents/MacOS/inkscape",
        "/usr/local/bin/inkscape",
    ]:
        if path and os.path.exists(path):
            return path
    console.print("[red]Inkscape not found. Install: brew install --cask inkscape[/red]")
    sys.exit(1)


def run_inkscape(args: list[str], verbose: bool = True) -> subprocess.CompletedProcess:
    """Run an Inkscape CLI command."""
    cmd = [_inkscape()] + args
    if verbose:
        console.print(f"[dim]$ inkscape {' '.join(args)}[/dim]")
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def text_to_path(input_path: str, output_path: str | None = None, verbose: bool = True) -> str:
    """Convert all text objects to paths (for print-safe SVGs)."""
    output_path = output_path or input_path
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    run_inkscape([
        input_path,
        "--export-text-to-path",
        f"--export-filename={output_path}",
        "--export-type=svg",
    ], verbose=verbose)
    if verbose and os.path.exists(output_path):
        console.print(f"[green]Text-to-path: {output_path}[/green]")
    return output_path


def simplify_paths(
    input_path: str,
    output_path: str | None = None,
    threshold: float = 0.002,
    verbose: bool = True,
) -> str:
    """Simplify SVG paths to reduce complexity."""
    output_path = output_path or input_path
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    run_inkscape([
        input_path,
        "--actions=select-all;object-simplify-path",
        f"--export-filename={output_path}",
        "--export-type=svg",
    ], verbose=verbose)
    if verbose and os.path.exists(output_path):
        console.print(f"[green]Simplified: {output_path}[/green]")
    return output_path


def export_pdf(input_path: str, output_path: str | None = None, verbose: bool = True) -> str:
    """Export SVG to PDF via Inkscape."""
    output_path = output_path or input_path.rsplit(".", 1)[0] + ".pdf"
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    run_inkscape([
        input_path,
        f"--export-filename={output_path}",
        "--export-type=pdf",
    ], verbose=verbose)
    if verbose and os.path.exists(output_path):
        console.print(f"[green]PDF: {output_path}[/green]")
    return output_path


def export_png(
    input_path: str,
    output_path: str | None = None,
    dpi: int = 300,
    verbose: bool = True,
) -> str:
    """Export SVG to PNG at specified DPI."""
    output_path = output_path or input_path.rsplit(".", 1)[0] + ".png"
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    run_inkscape([
        input_path,
        f"--export-filename={output_path}",
        "--export-type=png",
        f"--export-dpi={dpi}",
    ], verbose=verbose)
    if verbose and os.path.exists(output_path):
        console.print(f"[green]PNG ({dpi}dpi): {output_path}[/green]")
    return output_path


def svg_info(input_path: str, verbose: bool = True) -> dict:
    """Get SVG document info via Inkscape."""
    result = run_inkscape([input_path, "--query-all"], verbose=verbose)
    elements = []
    for line in result.stdout.splitlines():
        parts = line.split(",")
        if len(parts) >= 5:
            elements.append({
                "id": parts[0],
                "x": float(parts[1]),
                "y": float(parts[2]),
                "width": float(parts[3]),
                "height": float(parts[4]),
            })
    return {"elements": elements, "count": len(elements)}


def main():
    parser = argparse.ArgumentParser(description="Inkscape SVG manipulation wrapper")
    sub = parser.add_subparsers(dest="command")

    ttp = sub.add_parser("text-to-path", help="Convert text to paths")
    ttp.add_argument("input")
    ttp.add_argument("--output", "-o", default=None)

    simp = sub.add_parser("simplify", help="Simplify paths")
    simp.add_argument("input")
    simp.add_argument("--output", "-o", default=None)

    pdf = sub.add_parser("to-pdf", help="Export to PDF")
    pdf.add_argument("input")
    pdf.add_argument("--output", "-o", default=None)

    png = sub.add_parser("to-png", help="Export to PNG")
    png.add_argument("input")
    png.add_argument("--output", "-o", default=None)
    png.add_argument("--dpi", type=int, default=300)

    info = sub.add_parser("info", help="Show SVG element info")
    info.add_argument("input")

    args = parser.parse_args()

    if args.command == "text-to-path":
        text_to_path(args.input, args.output)
    elif args.command == "simplify":
        simplify_paths(args.input, args.output)
    elif args.command == "to-pdf":
        export_pdf(args.input, args.output)
    elif args.command == "to-png":
        export_png(args.input, args.output, args.dpi)
    elif args.command == "info":
        import json
        print(json.dumps(svg_info(args.input), indent=2))
    else:
        parser.print_help()
