"""LaTeX typesetting engine for GRIDS calling cards.

Compiles .tex files via LuaLaTeX with CMYK color support,
fontspec for system fonts, and microtype for microtypography.
Converts output to SVG for canvas compositing via pdf2svg.
"""

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

console = Console(stderr=True)


@dataclass
class CardSpec:
    width_inches: float = 3.07
    height_inches: float = 2.61
    bleed_inches: float = 0.125
    safe_margin_inches: float = 0.125

    @property
    def width_pt(self) -> float:
        return self.width_inches * 72

    @property
    def height_pt(self) -> float:
        return self.height_inches * 72

    @property
    def bleed_pt(self) -> float:
        return self.bleed_inches * 72

    @property
    def total_width_pt(self) -> float:
        return self.width_pt + 2 * self.bleed_pt

    @property
    def total_height_pt(self) -> float:
        return self.height_pt + 2 * self.bleed_pt


@dataclass
class CmykColor:
    c: float = 0.0
    m: float = 0.0
    y: float = 0.0
    k: float = 100.0
    name: str = "Black"

    def to_latex(self) -> str:
        return f"{{cmyk}}{{{self.c/100},{self.m/100},{self.y/100},{self.k/100}}}"

    def define_latex(self, varname: str) -> str:
        return f"\\definecolor{{{varname}}}{{cmyk}}{{{self.c/100:.3f},{self.m/100:.3f},{self.y/100:.3f},{self.k/100:.3f}}}"


@dataclass
class TypographySpec:
    primary_font: str = "Helvetica"
    secondary_font: str = ""
    body_size_pt: float = 9.0
    heading_size_pt: float = 12.0


@dataclass
class CardContent:
    """Content for one face of a calling card."""
    name: str = ""
    title: str = ""
    organization: str = ""
    contact_lines: list[str] = field(default_factory=list)
    tagline: str = ""
    custom_tex: str = ""


def generate_card_tex(
    content: CardContent,
    card: CardSpec,
    primary_color: CmykColor,
    secondary_color: CmykColor | None = None,
    typography: TypographySpec | None = None,
    side: str = "front",
) -> str:
    """Generate a standalone LaTeX file for one card face."""
    typ = typography or TypographySpec()
    sec_color = secondary_color or primary_color

    total_w = card.total_width_pt
    total_h = card.total_height_pt
    bleed = card.bleed_pt
    safe = card.safe_margin_inches * 72

    tex = []
    tex.append("\\documentclass[tikz,border=0pt]{standalone}")
    tex.append("\\usepackage{fontspec}")
    tex.append("\\usepackage{microtype}")
    tex.append("\\usepackage[cmyk]{xcolor}")
    tex.append("")
    tex.append(primary_color.define_latex("primary"))
    tex.append(sec_color.define_latex("secondary"))
    tex.append("")

    if typ.primary_font:
        tex.append(f"\\setmainfont{{{typ.primary_font}}}")
    if typ.secondary_font:
        tex.append(f"\\setsansfont{{{typ.secondary_font}}}")

    tex.append("")
    tex.append("\\begin{document}")
    tex.append(f"\\begin{{tikzpicture}}[x=1pt,y=-1pt]")
    tex.append(f"  % Total area: {total_w:.2f}pt x {total_h:.2f}pt (includes bleed)")
    tex.append(f"  \\useasboundingbox (0,0) rectangle ({total_w:.2f},{total_h:.2f});")
    tex.append("")

    if content.custom_tex:
        tex.append("  % Custom TeX content")
        tex.append(content.custom_tex)
    else:
        if side == "front":
            tex.append(_front_layout(content, card, bleed, safe, typ))
        else:
            tex.append(_back_layout(content, card, bleed, safe, typ))

    tex.append("\\end{tikzpicture}")
    tex.append("\\end{document}")

    return "\n".join(tex)


def _front_layout(
    content: CardContent,
    card: CardSpec,
    bleed: float,
    safe: float,
    typ: TypographySpec,
) -> str:
    """Default front layout: name, title, organization."""
    cx = card.total_width_pt / 2
    lines = []

    y_start = bleed + safe + 20

    if content.name:
        lines.append(
            f"  \\node[anchor=north,text=primary,font=\\fontsize{{{typ.heading_size_pt}}}{{14}}\\selectfont\\bfseries] "
            f"at ({cx:.1f},{y_start:.1f}) {{{_tex_escape(content.name)}}};"
        )
        y_start += typ.heading_size_pt + 8

    if content.title:
        lines.append(
            f"  \\node[anchor=north,text=secondary,font=\\fontsize{{{typ.body_size_pt}}}{{11}}\\selectfont\\itshape] "
            f"at ({cx:.1f},{y_start:.1f}) {{{_tex_escape(content.title)}}};"
        )
        y_start += typ.body_size_pt + 6

    if content.organization:
        lines.append(
            f"  \\node[anchor=north,text=primary,font=\\fontsize{{{typ.body_size_pt}}}{{11}}\\selectfont\\scshape] "
            f"at ({cx:.1f},{y_start:.1f}) {{{_tex_escape(content.organization)}}};"
        )

    return "\n".join(lines)


def _back_layout(
    content: CardContent,
    card: CardSpec,
    bleed: float,
    safe: float,
    typ: TypographySpec,
) -> str:
    """Default back layout: contact info, tagline."""
    cx = card.total_width_pt / 2
    lines = []

    y_start = bleed + safe + 15

    for line in content.contact_lines:
        lines.append(
            f"  \\node[anchor=north,text=primary,font=\\fontsize{{{typ.body_size_pt}}}{{11}}\\selectfont] "
            f"at ({cx:.1f},{y_start:.1f}) {{{_tex_escape(line)}}};"
        )
        y_start += typ.body_size_pt + 4

    if content.tagline:
        y_bottom = card.total_height_pt - bleed - safe - 10
        lines.append(
            f"  \\node[anchor=south,text=secondary,font=\\fontsize{{7}}{{9}}\\selectfont\\itshape] "
            f"at ({cx:.1f},{y_bottom:.1f}) {{{_tex_escape(content.tagline)}}};"
        )

    return "\n".join(lines)


def _tex_escape(s: str) -> str:
    return (
        s.replace("\\", "\\textbackslash{}")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("&", "\\&")
        .replace("%", "\\%")
        .replace("$", "\\$")
        .replace("#", "\\#")
        .replace("_", "\\_")
        .replace("~", "\\textasciitilde{}")
        .replace("^", "\\textasciicircum{}")
    )


def compile_tex(
    tex_path: str,
    output_dir: str | None = None,
    verbose: bool = True,
) -> str | None:
    """Compile a .tex file with LuaLaTeX. Returns path to output PDF."""
    tex_path = os.path.abspath(tex_path)
    output_dir = output_dir or os.path.dirname(tex_path)
    os.makedirs(output_dir, exist_ok=True)

    lualatex = shutil.which("lualatex")
    if not lualatex:
        for p in ["/Library/TeX/texbin/lualatex", "/usr/local/bin/lualatex"]:
            if os.path.exists(p):
                lualatex = p
                break

    if not lualatex:
        if verbose:
            console.print("[red]lualatex not found. Install MacTeX or TeX Live.[/red]")
        return None

    tex_basename = os.path.basename(tex_path)
    cmd = [
        lualatex,
        "-interaction=nonstopmode",
        tex_basename,
    ]

    if verbose:
        console.print(f"[cyan]Compiling {os.path.basename(tex_path)}...[/cyan]")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=output_dir,
        )
        if result.returncode != 0:
            if verbose:
                console.print(f"[red]LuaLaTeX error:[/red]")
                for line in result.stdout.splitlines()[-20:]:
                    if line.startswith("!") or "Error" in line:
                        console.print(f"  {line}")
            return None
    except subprocess.TimeoutExpired:
        if verbose:
            console.print("[red]LuaLaTeX timed out after 30s[/red]")
        return None
    except FileNotFoundError:
        if verbose:
            console.print("[red]lualatex binary not found[/red]")
        return None

    stem = Path(tex_path).stem
    pdf_path = os.path.join(output_dir, f"{stem}.pdf")
    if os.path.exists(pdf_path):
        if verbose:
            console.print(f"[green]Compiled: {pdf_path}[/green]")
        return pdf_path

    return None


def pdf_to_svg(pdf_path: str, svg_path: str | None = None, verbose: bool = True) -> str | None:
    """Convert PDF to SVG using pdf2svg."""
    pdf2svg = shutil.which("pdf2svg")
    if not pdf2svg:
        if verbose:
            console.print("[yellow]pdf2svg not found. Install: brew install pdf2svg[/yellow]")
        return None

    svg_path = svg_path or pdf_path.rsplit(".", 1)[0] + ".svg"
    os.makedirs(os.path.dirname(svg_path) or ".", exist_ok=True)

    try:
        subprocess.run(
            [pdf2svg, pdf_path, svg_path],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        if verbose:
            console.print("[red]pdf2svg conversion failed[/red]")
        return None

    if os.path.exists(svg_path):
        if verbose:
            console.print(f"[green]SVG: {svg_path}[/green]")
        return svg_path

    return None


def typeset_card(
    content: CardContent,
    card: CardSpec | None = None,
    primary_color: CmykColor | None = None,
    secondary_color: CmykColor | None = None,
    typography: TypographySpec | None = None,
    side: str = "front",
    output_dir: str = ".",
    name: str = "card",
    verbose: bool = True,
) -> dict:
    """Full pipeline: generate TeX → compile → convert to SVG.

    Returns dict with paths to .tex, .pdf, .svg files.
    """
    card = card or CardSpec()
    primary_color = primary_color or CmykColor()

    tex_content = generate_card_tex(
        content=content,
        card=card,
        primary_color=primary_color,
        secondary_color=secondary_color,
        typography=typography,
        side=side,
    )

    os.makedirs(output_dir, exist_ok=True)
    tex_path = os.path.join(output_dir, f"{name}-{side}.tex")
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write(tex_content)

    result = {"tex": tex_path, "pdf": None, "svg": None}

    pdf_path = compile_tex(tex_path, output_dir, verbose=verbose)
    if pdf_path:
        result["pdf"] = pdf_path
        svg_path = pdf_to_svg(pdf_path, verbose=verbose)
        if svg_path:
            result["svg"] = svg_path

    return result
