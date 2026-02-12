"""CLI for the LaTeX typesetting pipeline."""

import argparse
import json
import os
import sys

from rich.console import Console

console = Console(stderr=True)


def typeset_main():
    """Compile a .tex card face to PDF and optionally SVG."""
    parser = argparse.ArgumentParser(
        description="Typeset a calling card face via LuaLaTeX"
    )
    parser.add_argument("input", help="Path to .tex file")
    parser.add_argument("--output-dir", "-o", default=None, help="Output directory")
    parser.add_argument("--svg", action="store_true", help="Also convert to SVG via pdf2svg")
    parser.add_argument("--quiet", "-q", action="store_true")
    args = parser.parse_args()

    from grids.typeset.engine import compile_tex, pdf_to_svg

    output_dir = args.output_dir or os.path.dirname(args.input) or "."
    verbose = not args.quiet

    pdf_path = compile_tex(args.input, output_dir, verbose=verbose)
    if not pdf_path:
        console.print("[red]Compilation failed.[/red]")
        sys.exit(1)

    if args.svg:
        svg_path = pdf_to_svg(pdf_path, verbose=verbose)
        if not svg_path:
            console.print("[yellow]SVG conversion failed (pdf2svg missing?).[/yellow]")


def generate_main():
    """Generate a .tex card from a project.yaml spec."""
    parser = argparse.ArgumentParser(
        description="Generate calling card .tex files from project.yaml"
    )
    parser.add_argument("project", help="Path to project.yaml")
    parser.add_argument("--compile", "-c", action="store_true", help="Also compile to PDF")
    parser.add_argument("--svg", action="store_true", help="Also convert to SVG")
    parser.add_argument("--quiet", "-q", action="store_true")
    args = parser.parse_args()

    import yaml
    from grids.typeset.engine import (
        CardContent, CardSpec, CmykColor, TypographySpec, typeset_card,
    )

    with open(args.project, "r") as f:
        spec = yaml.safe_load(f)

    card = CardSpec(
        width_inches=spec.get("physical", {}).get("item_width_inches", 3.07),
        height_inches=spec.get("physical", {}).get("item_height_inches", 2.61),
        bleed_inches=spec.get("physical", {}).get("bleed_inches", 0.125),
    )

    color_spec = spec.get("color", {})
    primary_data = color_spec.get("primary", {})
    primary = CmykColor(
        c=primary_data.get("c", 0),
        m=primary_data.get("m", 0),
        y=primary_data.get("y", 0),
        k=primary_data.get("k", 100),
        name=primary_data.get("name", "Black"),
    )

    secondary = None
    if color_spec.get("secondary"):
        sd = color_spec["secondary"]
        secondary = CmykColor(
            c=sd.get("c", 0), m=sd.get("m", 0),
            y=sd.get("y", 0), k=sd.get("k", 0),
            name=sd.get("name", "Secondary"),
        )

    typ_spec = spec.get("typography", {})
    typography = TypographySpec(
        primary_font=typ_spec.get("primary_font", ""),
        secondary_font=typ_spec.get("secondary_font", ""),
    )

    project_dir = os.path.dirname(args.project)
    verbose = not args.quiet

    content = CardContent(
        name=spec.get("name", "Name"),
        title="Crafting Workshop",
        organization="",
        contact_lines=[],
        tagline="",
    )

    for side in ["front", "back"]:
        out_dir = os.path.join(project_dir, "cards", side)
        result = typeset_card(
            content=content,
            card=card,
            primary_color=primary,
            secondary_color=secondary,
            typography=typography,
            side=side,
            output_dir=out_dir,
            name="card-01",
            verbose=verbose,
        )
        if verbose:
            console.print(f"[green]{side}: {result}[/green]")


def idml_main():
    """Export calling card(s) as IDML for Adobe InDesign."""
    parser = argparse.ArgumentParser(
        description="Export calling card as IDML (InDesign Markup Language)"
    )
    parser.add_argument("project", help="Path to project.yaml")
    parser.add_argument("--side", choices=["front", "back", "both"], default="both")
    parser.add_argument("--output-dir", "-o", default=None)
    parser.add_argument("--quiet", "-q", action="store_true")
    args = parser.parse_args()

    import yaml
    from grids.typeset.engine import CardContent, CardSpec, CmykColor, TypographySpec
    from grids.typeset.idml import export_card_idml

    with open(args.project, "r") as f:
        spec = yaml.safe_load(f)

    card = CardSpec(
        width_inches=spec.get("physical", {}).get("item_width_inches", 3.07),
        height_inches=spec.get("physical", {}).get("item_height_inches", 2.61),
        bleed_inches=spec.get("physical", {}).get("bleed_inches", 0.125),
    )

    color_spec = spec.get("color", {})
    primary_data = color_spec.get("primary", {})
    primary = CmykColor(
        c=primary_data.get("c", 0),
        m=primary_data.get("m", 0),
        y=primary_data.get("y", 0),
        k=primary_data.get("k", 100),
        name=primary_data.get("name", "Black"),
    )

    secondary = None
    if color_spec.get("secondary"):
        sd = color_spec["secondary"]
        secondary = CmykColor(
            c=sd.get("c", 0), m=sd.get("m", 0),
            y=sd.get("y", 0), k=sd.get("k", 0),
            name=sd.get("name", "Secondary"),
        )

    typ_spec = spec.get("typography", {})
    typography = TypographySpec(
        primary_font=typ_spec.get("primary_font", ""),
        secondary_font=typ_spec.get("secondary_font", ""),
    )

    project_dir = os.path.dirname(args.project)
    out_dir = args.output_dir or os.path.join(project_dir, "output")
    verbose = not args.quiet

    content = CardContent(
        name=spec.get("name", "Name"),
        title="Crafting Workshop",
        organization="",
        contact_lines=[],
        tagline="",
    )

    sides = ["front", "back"] if args.side == "both" else [args.side]
    for side in sides:
        path = export_card_idml(
            content=content,
            card=card,
            primary_color=primary,
            secondary_color=secondary,
            typography=typography,
            side=side,
            output_dir=out_dir,
            name="card-01",
            verbose=verbose,
        )
        if path and verbose:
            console.print(f"[green]{side}: {path}[/green]")


def main():
    typeset_main()
