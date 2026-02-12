"""GraphicsMagick wrapper for raster image operations.

Handles CMYK conversion, cropping, levels adjustment, resizing,
and ICC profile embedding for print production workflows.
"""

import argparse
import os
import shutil
import subprocess
import sys

from rich.console import Console

console = Console(stderr=True)


def _gm() -> str:
    """Find the GraphicsMagick binary."""
    gm = shutil.which("gm")
    if not gm:
        console.print("[red]GraphicsMagick not found. Install: brew install graphicsmagick[/red]")
        sys.exit(1)
    return gm


def run_gm(args: list[str], verbose: bool = True) -> subprocess.CompletedProcess:
    """Run a GraphicsMagick command."""
    cmd = [_gm()] + args
    if verbose:
        console.print(f"[dim]$ gm {' '.join(args)}[/dim]")
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def to_cmyk(input_path: str, output_path: str, verbose: bool = True) -> str:
    """Convert an image to CMYK color space."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    result = run_gm(
        ["convert", input_path, "-colorspace", "CMYK", output_path],
        verbose=verbose,
    )
    if result.returncode != 0:
        console.print(f"[red]CMYK conversion failed: {result.stderr}[/red]")
    return output_path


def threshold(input_path: str, output_path: str, level: int = 50, verbose: bool = True) -> str:
    """Threshold an image to pure black/white for tracing."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    result = run_gm(
        ["convert", input_path, "-threshold", f"{level}%", output_path],
        verbose=verbose,
    )
    if result.returncode != 0:
        console.print(f"[red]Threshold failed: {result.stderr}[/red]")
    return output_path


def crop(
    input_path: str, output_path: str,
    width: int, height: int, x: int = 0, y: int = 0,
    verbose: bool = True,
) -> str:
    """Crop a region from an image."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    geometry = f"{width}x{height}+{x}+{y}"
    run_gm(["convert", input_path, "-crop", geometry, output_path], verbose=verbose)
    return output_path


def resize(
    input_path: str, output_path: str,
    width: int | None = None, height: int | None = None,
    dpi: int | None = None,
    verbose: bool = True,
) -> str:
    """Resize an image, optionally setting DPI."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    args = ["convert", input_path]
    if width and height:
        args += ["-resize", f"{width}x{height}"]
    elif width:
        args += ["-resize", f"{width}x"]
    elif height:
        args += ["-resize", f"x{height}"]
    if dpi:
        args += ["-density", str(dpi)]
    args.append(output_path)
    run_gm(args, verbose=verbose)
    return output_path


def levels(
    input_path: str, output_path: str,
    black: int = 0, white: int = 100, gamma: float = 1.0,
    verbose: bool = True,
) -> str:
    """Adjust levels (black point, white point, gamma)."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    run_gm(
        ["convert", input_path, "-level", f"{black}%,{white}%,{gamma}", output_path],
        verbose=verbose,
    )
    return output_path


def identify(input_path: str, verbose: bool = True) -> dict:
    """Get image metadata."""
    result = run_gm(["identify", "-verbose", input_path], verbose=verbose)
    info = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if ":" in line:
            key, _, val = line.partition(":")
            info[key.strip()] = val.strip()
    return info


def main():
    parser = argparse.ArgumentParser(description="GraphicsMagick wrapper for GRIDS")
    sub = parser.add_subparsers(dest="command")

    cmyk_p = sub.add_parser("cmyk", help="Convert to CMYK")
    cmyk_p.add_argument("input")
    cmyk_p.add_argument("output")

    thresh_p = sub.add_parser("threshold", help="Threshold to B/W")
    thresh_p.add_argument("input")
    thresh_p.add_argument("output")
    thresh_p.add_argument("--level", type=int, default=50)

    crop_p = sub.add_parser("crop", help="Crop region")
    crop_p.add_argument("input")
    crop_p.add_argument("output")
    crop_p.add_argument("--width", "-W", type=int, required=True)
    crop_p.add_argument("--height", "-H", type=int, required=True)
    crop_p.add_argument("--x", type=int, default=0)
    crop_p.add_argument("--y", type=int, default=0)

    resize_p = sub.add_parser("resize", help="Resize image")
    resize_p.add_argument("input")
    resize_p.add_argument("output")
    resize_p.add_argument("--width", "-W", type=int, default=None)
    resize_p.add_argument("--height", "-H", type=int, default=None)
    resize_p.add_argument("--dpi", type=int, default=None)

    id_p = sub.add_parser("identify", help="Show image info")
    id_p.add_argument("input")

    args = parser.parse_args()

    if args.command == "cmyk":
        to_cmyk(args.input, args.output)
    elif args.command == "threshold":
        threshold(args.input, args.output, args.level)
    elif args.command == "crop":
        crop(args.input, args.output, args.width, args.height, args.x, args.y)
    elif args.command == "resize":
        resize(args.input, args.output, args.width, args.height, args.dpi)
    elif args.command == "identify":
        import json
        info = identify(args.input)
        print(json.dumps(info, indent=2))
    else:
        parser.print_help()
