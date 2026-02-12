"""Raster-to-vector border/frame extraction pipeline.

Replicates the Photoshop workflow for extracting decorative elements
from reference images:
  1. Crop to region of interest (optional)
  2. Grayscale conversion
  3. Levels adjustment (crush blacks, blow whites)
  4. Denoise (median filter)
  5. Invert (optional, for light-on-dark)
  6. Threshold to pure B&W bitmap
  7. Potrace vectorization to SVG
  8. Resize/fit SVG to target card dimensions

Usage:
    grids-extract-border input.png -o border.svg
    grids-extract-border input.png --levels 20,80 --threshold 45 --fit 221.04x187.92
    grids-extract-border input.png --invert --turdsize 5
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from rich.console import Console

console = Console(stderr=True)


def _gm() -> str:
    gm = shutil.which("gm")
    if not gm:
        console.print("[red]GraphicsMagick not found. Install: brew install graphicsmagick[/red]")
        sys.exit(1)
    return gm


def _potrace() -> str:
    pt = shutil.which("potrace")
    if not pt:
        console.print("[red]potrace not found. Install: brew install potrace[/red]")
        sys.exit(1)
    return pt


def _run(cmd: list[str], verbose: bool = True):
    if verbose:
        console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0 and result.stderr.strip():
        console.print(f"[red]{result.stderr.strip()}[/red]")
    return result


def identify_image(path: str) -> dict:
    result = subprocess.run(
        [_gm(), "identify", "-format", "%w %h", path],
        capture_output=True, text=True, timeout=10,
    )
    parts = result.stdout.strip().split()
    if len(parts) >= 2:
        return {"width": int(parts[0]), "height": int(parts[1])}
    return {}


def extract_border(
    input_path: str,
    output_path: str,
    crop: str | None = None,
    levels_black: int = 15,
    levels_white: int = 85,
    levels_gamma: float = 0.8,
    threshold: int = 50,
    invert: bool = False,
    turdsize: int = 3,
    alphamax: float = 1.0,
    opttolerance: float = 0.2,
    fit: str | None = None,
    denoise: bool = True,
    verbose: bool = True,
) -> str:
    """Full pipeline: raster reference image -> clean SVG.

    Args:
        input_path: Source image (PNG, JPEG, TIFF, etc.)
        output_path: Output SVG path
        crop: Crop geometry "x,y,w,h" or None for full image
        levels_black: Black point percentage (0-100)
        levels_white: White point percentage (0-100)
        levels_gamma: Gamma correction (< 1 darkens midtones)
        threshold: B&W threshold percentage (0-100)
        invert: Invert before tracing (for light-on-dark sources)
        turdsize: Potrace speckle suppression pixel count
        alphamax: Potrace corner threshold (0=sharp, 1.334=smooth)
        opttolerance: Potrace curve optimization tolerance
        fit: Target dimensions "WxH" in points to set SVG viewBox
        denoise: Apply median filter to reduce noise
        verbose: Print progress
    """
    gm = _gm()
    potrace = _potrace()
    tmpdir = tempfile.mkdtemp(prefix="grids-extract-")

    try:
        if verbose:
            info = identify_image(input_path)
            console.print(
                f"[cyan]Source: {os.path.basename(input_path)} "
                f"({info.get('width', '?')}x{info.get('height', '?')})[/cyan]"
            )

        current = input_path

        # Step 1: Crop
        if crop:
            parts = crop.split(",")
            if len(parts) == 4:
                x, y, w, h = [int(p) for p in parts]
                cropped = os.path.join(tmpdir, "cropped.png")
                _run([gm, "convert", current, "-crop", f"{w}x{h}+{x}+{y}", cropped], verbose)
                current = cropped
                if verbose:
                    console.print(f"  Cropped: {w}x{h}+{x}+{y}")

        # Step 2: Grayscale
        gray = os.path.join(tmpdir, "gray.png")
        _run([gm, "convert", current, "-colorspace", "Gray", gray], verbose)
        current = gray
        if verbose:
            console.print("  Grayscale")

        # Step 3: Levels
        leveled = os.path.join(tmpdir, "leveled.png")
        _run([
            gm, "convert", current,
            "-level", f"{levels_black}%,{levels_white}%,{levels_gamma}",
            leveled,
        ], verbose)
        current = leveled
        if verbose:
            console.print(f"  Levels: black={levels_black}% white={levels_white}% gamma={levels_gamma}")

        # Step 3.5: Denoise
        if denoise:
            denoised = os.path.join(tmpdir, "denoised.png")
            _run([gm, "convert", current, "-median", "1", denoised], verbose)
            current = denoised
            if verbose:
                console.print("  Denoise (median 1px)")

        # Step 4: Invert
        if invert:
            inverted = os.path.join(tmpdir, "inverted.png")
            _run([gm, "convert", current, "-negate", inverted], verbose)
            current = inverted
            if verbose:
                console.print("  Inverted")

        # Step 5: Threshold
        bw = os.path.join(tmpdir, "bw.pbm")
        _run([gm, "convert", current, "-threshold", f"{threshold}%", bw], verbose)
        current = bw
        if verbose:
            console.print(f"  Threshold: {threshold}%")

        # Step 6: Potrace
        os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
        cmd = [
            potrace, "-s",
            "-t", str(turdsize),
            "-a", str(alphamax),
            "-O", str(opttolerance),
            "-o", output_path,
            current,
        ]
        _run(cmd, verbose)

        if not os.path.exists(output_path):
            console.print("[red]Potrace produced no output[/red]")
            return output_path

        if verbose:
            svg_size = os.path.getsize(output_path)
            console.print(f"  SVG: {svg_size / 1024:.1f}KB")

        # Step 7: Fit to target dimensions
        if fit:
            refit_svg(output_path, fit, verbose)

        if verbose:
            console.print(f"[green]Done: {output_path}[/green]")

        return output_path

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def refit_svg(svg_path: str, fit: str, verbose: bool = True):
    """Rewrite the SVG width/height to fit target card size in points."""
    parts = fit.lower().split("x")
    if len(parts) != 2:
        return
    target_w, target_h = float(parts[0]), float(parts[1])

    with open(svg_path, "r") as f:
        content = f.read()

    content = re.sub(r'width="[^"]+"', f'width="{target_w}pt"', content)
    content = re.sub(r'height="[^"]+"', f'height="{target_h}pt"', content)

    with open(svg_path, "w") as f:
        f.write(content)

    if verbose:
        console.print(f"  Refit to {target_w}x{target_h}pt")


def main():
    parser = argparse.ArgumentParser(
        description="Extract decorative borders/elements from reference images -> SVG",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  grids-extract-border photo.png -o border.svg
  grids-extract-border card.jpg --crop 0,0,800,100 --levels 25,75 -o border.svg
  grids-extract-border dark-card.png --invert --threshold 40 -o border.svg
  grids-extract-border ref.png -o frame.svg --fit 221.04x187.92
  grids-extract-border ref.png -o frame.svg --turdsize 5 --alphamax 1.2
        """,
    )
    parser.add_argument("input", help="Source image (PNG, JPEG, TIFF, BMP)")
    parser.add_argument("-o", "--output", default=None, help="Output SVG path")
    parser.add_argument("--crop", default=None, help="Crop geometry: x,y,w,h")
    parser.add_argument("--levels", default="15,85", help="Black,white points (default: 15,85)")
    parser.add_argument("--gamma", type=float, default=0.8, help="Gamma (default: 0.8)")
    parser.add_argument("--threshold", "-t", type=int, default=50, help="B&W threshold %% (default: 50)")
    parser.add_argument("--invert", "-i", action="store_true", help="Invert before tracing")
    parser.add_argument("--turdsize", type=int, default=3, help="Speckle suppression (default: 3)")
    parser.add_argument("--alphamax", type=float, default=1.0, help="Corner smoothness (default: 1.0)")
    parser.add_argument("--opttolerance", type=float, default=0.2, help="Curve optimization (default: 0.2)")
    parser.add_argument("--fit", default=None, help="Target WxH in points (e.g., 221.04x187.92)")
    parser.add_argument("--no-denoise", action="store_true", help="Skip median filter")
    parser.add_argument("--json", action="store_true", help="Output JSON result")
    parser.add_argument("--quiet", "-q", action="store_true")
    args = parser.parse_args()

    output = args.output or args.input.rsplit(".", 1)[0] + ".svg"
    levels = args.levels.split(",")
    levels_black = int(levels[0]) if len(levels) > 0 else 15
    levels_white = int(levels[1]) if len(levels) > 1 else 85

    kwargs = dict(
        crop=args.crop,
        levels_black=levels_black,
        levels_white=levels_white,
        levels_gamma=args.gamma,
        threshold=args.threshold,
        invert=args.invert,
        turdsize=args.turdsize,
        alphamax=args.alphamax,
        opttolerance=args.opttolerance,
        fit=args.fit,
        denoise=not args.no_denoise,
        verbose=not args.quiet,
    )

    if args.json:
        result_path = extract_border(args.input, output, **kwargs)
        result = {"input": args.input, "output": result_path, "exists": os.path.exists(result_path)}
        if result["exists"]:
            result["size_bytes"] = os.path.getsize(result_path)
        print(json.dumps(result, indent=2))
    else:
        extract_border(args.input, output, **kwargs)
