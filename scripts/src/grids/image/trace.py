"""Potrace wrapper for bitmap-to-SVG vectorization.

Traces scanned images (vintage cards, hand-drawn elements) into
clean SVG paths for use in the design canvas.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

from rich.console import Console

console = Console(stderr=True)


def _potrace() -> str:
    """Find the potrace binary."""
    pt = shutil.which("potrace")
    if not pt:
        console.print("[red]potrace not found. Install: brew install potrace[/red]")
        sys.exit(1)
    return pt


def trace_to_svg(
    input_path: str,
    output_path: str,
    turdsize: int = 2,
    alphamax: float = 1.0,
    opttolerance: float = 0.2,
    invert: bool = False,
    verbose: bool = True,
) -> str:
    """Trace a bitmap image to SVG.

    Input should be PBM/PGM/PPM/BMP. For JPEG/PNG, pre-convert with
    GraphicsMagick: `gm convert input.jpg -threshold 50% input.pbm`

    Args:
        turdsize: Suppress speckles of up to this many pixels.
        alphamax: Corner threshold (0=sharp corners, 1.334=smooth).
        opttolerance: Curve optimization tolerance.
        invert: Invert input before tracing.
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    needs_convert = input_path.lower().endswith((".jpg", ".jpeg", ".png", ".tiff", ".tif"))
    actual_input = input_path

    if needs_convert:
        gm = shutil.which("gm")
        if not gm:
            console.print("[red]Need GraphicsMagick to convert image format for potrace[/red]")
            sys.exit(1)

        tmp = tempfile.NamedTemporaryFile(suffix=".pbm", delete=False)
        tmp.close()
        actual_input = tmp.name

        if verbose:
            console.print(f"[dim]Converting {input_path} -> PBM for tracing[/dim]")
        subprocess.run(
            [gm, "convert", input_path, "-threshold", "50%", actual_input],
            capture_output=True, timeout=30,
        )

    cmd = [
        _potrace(),
        "-s",
        "-t", str(turdsize),
        "-a", str(alphamax),
        "-O", str(opttolerance),
        "-o", output_path,
    ]

    if invert:
        cmd.append("--invert")

    cmd.append(actual_input)

    if verbose:
        console.print(f"[cyan]Tracing {os.path.basename(input_path)} -> SVG[/cyan]")

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    if needs_convert and actual_input != input_path:
        os.unlink(actual_input)

    if result.returncode != 0:
        console.print(f"[red]Potrace error: {result.stderr}[/red]")
    elif os.path.exists(output_path):
        if verbose:
            console.print(f"[green]Traced: {output_path}[/green]")

    return output_path


def main():
    parser = argparse.ArgumentParser(description="Trace bitmap images to SVG (potrace wrapper)")
    parser.add_argument("input", help="Input image (PBM/PGM/PPM/BMP, or JPEG/PNG with auto-convert)")
    parser.add_argument("--output", "-o", default=None, help="Output SVG path")
    parser.add_argument("--turdsize", "-t", type=int, default=2, help="Speckle suppression (default: 2)")
    parser.add_argument("--alphamax", "-a", type=float, default=1.0, help="Corner threshold (default: 1.0)")
    parser.add_argument("--opttolerance", "-O", type=float, default=0.2, help="Optimization tolerance")
    parser.add_argument("--invert", action="store_true", help="Invert input before tracing")
    parser.add_argument("--quiet", "-q", action="store_true")
    args = parser.parse_args()

    output = args.output or args.input.rsplit(".", 1)[0] + ".svg"
    trace_to_svg(
        args.input, output,
        turdsize=args.turdsize,
        alphamax=args.alphamax,
        opttolerance=args.opttolerance,
        invert=args.invert,
        verbose=not args.quiet,
    )
