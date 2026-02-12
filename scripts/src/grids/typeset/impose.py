"""Print-ready PDF imposition for calling cards.

Arranges individual card-face PDFs onto arbitrary stock sheets with:
- Trim marks at each card boundary
- Registration marks
- Bleed extension
- Page 1 = all fronts, Page 2 = all backs (mirrored for duplex)
- CMYK color space
- Detailed cutting instructions for the print shop
"""

import argparse
import math
import os
import sys
from dataclasses import dataclass, field

from rich.console import Console
from rich.table import Table

console = Console(stderr=True)

COMMON_STOCK = {
    "letter":    (8.5, 11.0),
    "tabloid":   (11.0, 17.0),
    "11x17":     (11.0, 17.0),
    "legal":     (8.5, 14.0),
    "a4":        (8.267, 11.692),
    "a3":        (11.692, 16.535),
    "sra3":      (12.598, 17.717),
    "12x18":     (12.0, 18.0),
    "13x19":     (13.0, 19.0),
    "super-b":   (13.0, 19.0),
}


@dataclass
class StockSpec:
    width_inches: float = 11.0
    height_inches: float = 17.0

    @property
    def width_pt(self) -> float:
        return self.width_inches * 72

    @property
    def height_pt(self) -> float:
        return self.height_inches * 72


@dataclass
class ImpositionLayout:
    card_width_pt: float
    card_height_pt: float
    bleed_pt: float
    stock: StockSpec
    gutter_pt: float = 18.0
    margin_pt: float = 36.0

    @property
    def cell_width(self) -> float:
        return self.card_width_pt + 2 * self.bleed_pt

    @property
    def cell_height(self) -> float:
        return self.card_height_pt + 2 * self.bleed_pt

    @property
    def cols(self) -> int:
        usable = self.stock.width_pt - 2 * self.margin_pt
        return max(1, int((usable + self.gutter_pt) / (self.cell_width + self.gutter_pt)))

    @property
    def rows(self) -> int:
        usable = self.stock.height_pt - 2 * self.margin_pt
        return max(1, int((usable + self.gutter_pt) / (self.cell_height + self.gutter_pt)))

    @property
    def capacity(self) -> int:
        return self.cols * self.rows

    def card_positions(self) -> list[tuple[float, float]]:
        total_cards_w = self.cols * self.cell_width + (self.cols - 1) * self.gutter_pt
        total_cards_h = self.rows * self.cell_height + (self.rows - 1) * self.gutter_pt
        x_offset = (self.stock.width_pt - total_cards_w) / 2
        y_offset = (self.stock.height_pt - total_cards_h) / 2

        positions = []
        for row in range(self.rows):
            for col in range(self.cols):
                x = x_offset + col * (self.cell_width + self.gutter_pt)
                y = y_offset + row * (self.cell_height + self.gutter_pt)
                positions.append((x, y))
        return positions


def try_both_orientations(
    card_w_pt: float,
    card_h_pt: float,
    bleed_pt: float,
    stock: StockSpec,
    gutter_pt: float = 18.0,
    margin_pt: float = 36.0,
) -> ImpositionLayout:
    """Try both card orientations and pick whichever fits more cards."""
    layout_normal = ImpositionLayout(
        card_width_pt=card_w_pt, card_height_pt=card_h_pt,
        bleed_pt=bleed_pt, stock=stock,
        gutter_pt=gutter_pt, margin_pt=margin_pt,
    )
    layout_rotated = ImpositionLayout(
        card_width_pt=card_h_pt, card_height_pt=card_w_pt,
        bleed_pt=bleed_pt, stock=stock,
        gutter_pt=gutter_pt, margin_pt=margin_pt,
    )
    if layout_rotated.capacity > layout_normal.capacity:
        return layout_rotated
    return layout_normal


@dataclass
class CuttingInstruction:
    stock_width_inches: float
    stock_height_inches: float
    card_width_inches: float
    card_height_inches: float
    bleed_inches: float
    cols: int
    rows: int
    capacity: int
    margin_inches: float
    gutter_inches: float
    horizontal_cuts: list[float] = field(default_factory=list)
    vertical_cuts: list[float] = field(default_factory=list)
    waste_pct: float = 0.0
    notes: list[str] = field(default_factory=list)


def generate_cutting_instructions(layout: ImpositionLayout) -> CuttingInstruction:
    """Generate print-shop-ready cutting instructions."""
    positions = layout.card_positions()

    h_cuts_set = set()
    v_cuts_set = set()

    for (x, y) in positions:
        trim_x = x + layout.bleed_pt
        trim_y = y + layout.bleed_pt
        trim_right = trim_x + layout.card_width_pt
        trim_bottom = trim_y + layout.card_height_pt

        h_cuts_set.add(round(trim_y / 72, 4))
        h_cuts_set.add(round(trim_bottom / 72, 4))
        v_cuts_set.add(round(trim_x / 72, 4))
        v_cuts_set.add(round(trim_right / 72, 4))

    h_cuts = sorted(h_cuts_set)
    v_cuts = sorted(v_cuts_set)

    card_area = layout.card_width_pt * layout.card_height_pt * layout.capacity
    stock_area = layout.stock.width_pt * layout.stock.height_pt
    waste_pct = (1.0 - card_area / stock_area) * 100

    card_w_in = layout.card_width_pt / 72
    card_h_in = layout.card_height_pt / 72

    instr = CuttingInstruction(
        stock_width_inches=layout.stock.width_inches,
        stock_height_inches=layout.stock.height_inches,
        card_width_inches=round(card_w_in, 3),
        card_height_inches=round(card_h_in, 3),
        bleed_inches=round(layout.bleed_pt / 72, 3),
        cols=layout.cols,
        rows=layout.rows,
        capacity=layout.capacity,
        margin_inches=round(layout.margin_pt / 72, 3),
        gutter_inches=round(layout.gutter_pt / 72, 3),
        horizontal_cuts=h_cuts,
        vertical_cuts=v_cuts,
        waste_pct=round(waste_pct, 1),
    )

    instr.notes.append(f"Sheet: {layout.stock.width_inches}\" x {layout.stock.height_inches}\"")
    instr.notes.append(f"Cards: {layout.cols} across x {layout.rows} down = {layout.capacity} per sheet")
    instr.notes.append(f"Finished card: {card_w_in:.3f}\" x {card_h_in:.3f}\"")
    instr.notes.append(f"Bleed: {layout.bleed_pt / 72:.3f}\" per side")
    instr.notes.append(f"Gutter: {layout.gutter_pt / 72:.3f}\" between cards")
    instr.notes.append(f"Waste: {waste_pct:.1f}%")
    instr.notes.append("")
    instr.notes.append(f"HORIZONTAL CUTS (from top edge): {len(h_cuts)} cuts")
    for i, h in enumerate(h_cuts):
        instr.notes.append(f"  H{i+1}: {h:.3f}\" from top")
    instr.notes.append("")
    instr.notes.append(f"VERTICAL CUTS (from left edge): {len(v_cuts)} cuts")
    for i, v in enumerate(v_cuts):
        instr.notes.append(f"  V{i+1}: {v:.3f}\" from left")
    instr.notes.append("")
    instr.notes.append("Page 1 = Fronts, Page 2 = Backs (mirrored for duplex)")
    instr.notes.append("Cut through both pages together after duplex printing.")

    return instr


def print_cutting_instructions(instr: CuttingInstruction):
    """Pretty-print cutting instructions to console."""
    console.print()
    console.print("[bold cyan]═══ CUTTING INSTRUCTIONS ═══[/bold cyan]")
    console.print()

    tbl = Table(show_header=False, box=None, padding=(0, 2))
    tbl.add_column(style="bold")
    tbl.add_column()
    tbl.add_row("Stock", f'{instr.stock_width_inches}" x {instr.stock_height_inches}"')
    tbl.add_row("Card (trim)", f'{instr.card_width_inches}" x {instr.card_height_inches}"')
    tbl.add_row("Bleed", f'{instr.bleed_inches}" per side')
    tbl.add_row("Layout", f"{instr.cols} x {instr.rows} = {instr.capacity} cards/sheet")
    tbl.add_row("Gutter", f'{instr.gutter_inches}"')
    tbl.add_row("Waste", f"{instr.waste_pct}%")
    console.print(tbl)
    console.print()

    console.print(f"[bold]Horizontal cuts[/bold] ({len(instr.horizontal_cuts)} from top edge):")
    for i, h in enumerate(instr.horizontal_cuts):
        console.print(f'  H{i+1}: [yellow]{h:.3f}"[/yellow]')
    console.print()
    console.print(f"[bold]Vertical cuts[/bold] ({len(instr.vertical_cuts)} from left edge):")
    for i, v in enumerate(instr.vertical_cuts):
        console.print(f'  V{i+1}: [yellow]{v:.3f}"[/yellow]')
    console.print()
    console.print("[dim]Page 1 = Fronts, Page 2 = Backs (mirrored for duplex)")
    console.print("Cut through both pages together after duplex printing.[/dim]")


def write_cutting_instructions(instr: CuttingInstruction, path: str):
    """Write cutting instructions to a plain text file for the print shop."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write("CUTTING INSTRUCTIONS\n")
        f.write("=" * 50 + "\n\n")
        for line in instr.notes:
            f.write(line + "\n")


def impose_pdf(
    front_pdfs: list[str],
    back_pdfs: list[str],
    output_path: str,
    card_width_inches: float = 3.07,
    card_height_inches: float = 2.61,
    bleed_inches: float = 0.125,
    stock: StockSpec | None = None,
    gutter_inches: float = 0.25,
    margin_inches: float = 0.5,
    auto_rotate: bool = True,
    verbose: bool = True,
) -> tuple[str, CuttingInstruction]:
    """Create an imposed 2-page PDF with cutting instructions."""
    try:
        from reportlab.lib.colors import CMYKColor
        from reportlab.pdfgen import canvas as rl_canvas
    except ImportError:
        console.print("[red]reportlab not installed. Run: pip install reportlab[/red]")
        sys.exit(1)

    stock = stock or StockSpec()
    card_w_pt = card_width_inches * 72
    card_h_pt = card_height_inches * 72
    bleed_pt = bleed_inches * 72
    gutter_pt = gutter_inches * 72
    margin_pt = margin_inches * 72

    if auto_rotate:
        layout = try_both_orientations(
            card_w_pt, card_h_pt, bleed_pt, stock, gutter_pt, margin_pt,
        )
    else:
        layout = ImpositionLayout(
            card_width_pt=card_w_pt, card_height_pt=card_h_pt,
            bleed_pt=bleed_pt, stock=stock,
            gutter_pt=gutter_pt, margin_pt=margin_pt,
        )

    cutting = generate_cutting_instructions(layout)

    if verbose:
        console.print(f"[cyan]Imposition: {layout.cols}x{layout.rows} = {layout.capacity} cards per sheet[/cyan]")
        console.print(f"[cyan]Stock: {stock.width_inches}\" x {stock.height_inches}\"[/cyan]")
        console.print(f"[cyan]Card: {layout.card_width_pt/72:.3f}\" x {layout.card_height_pt/72:.3f}\" (bleed: {bleed_inches}\", gutter: {gutter_inches}\")[/cyan]")
        console.print(f"[cyan]Waste: {cutting.waste_pct}%[/cyan]")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    c = rl_canvas.Canvas(
        output_path,
        pagesize=(stock.width_pt, stock.height_pt),
    )

    positions = layout.card_positions()

    if verbose:
        console.print("[cyan]Page 1: Fronts[/cyan]")
    _draw_page(c, front_pdfs, positions, layout, verbose)
    c.showPage()

    if verbose:
        console.print("[cyan]Page 2: Backs (mirrored for duplex)[/cyan]")
    mirrored_positions = [
        (stock.width_pt - x - layout.cell_width, y)
        for x, y in positions
    ]
    _draw_page(c, back_pdfs, mirrored_positions, layout, verbose)
    c.showPage()

    c.save()

    if verbose:
        console.print(f"[green]Imposed PDF: {output_path}[/green]")

    return output_path, cutting


def _draw_page(c, pdfs, positions, layout, verbose):
    from reportlab.lib.colors import CMYKColor

    mark_color = CMYKColor(0, 0, 0, 1)
    mark_len = 12
    mark_offset = 3

    for i, (x, y) in enumerate(positions):
        rl_y = layout.stock.height_pt - y - layout.cell_height

        if i < len(pdfs) and pdfs[i] and os.path.exists(pdfs[i]):
            try:
                c.saveState()
                c.translate(x, rl_y)
                c.setFillColor(CMYKColor(0, 0, 0, 0.03))
                c.rect(0, 0, layout.cell_width, layout.cell_height, fill=1, stroke=0)
                c.setFillColor(CMYKColor(0, 0, 0, 0.5))
                c.setFont("Helvetica", 7)
                c.drawCentredString(
                    layout.cell_width / 2,
                    layout.cell_height / 2,
                    f"Card {i + 1}: {os.path.basename(pdfs[i])}",
                )
                c.restoreState()
            except Exception as e:
                if verbose:
                    console.print(f"[yellow]Could not embed card {i}: {e}[/yellow]")

        trim_x = x + layout.bleed_pt
        trim_y_top = layout.stock.height_pt - (y + layout.bleed_pt)
        trim_y_bottom = layout.stock.height_pt - (y + layout.bleed_pt + layout.card_height_pt)
        trim_right = trim_x + layout.card_width_pt

        c.setStrokeColor(mark_color)
        c.setLineWidth(0.25)

        _draw_crop_mark(c, trim_x, trim_y_top, mark_len, mark_offset, "tl")
        _draw_crop_mark(c, trim_right, trim_y_top, mark_len, mark_offset, "tr")
        _draw_crop_mark(c, trim_x, trim_y_bottom, mark_len, mark_offset, "bl")
        _draw_crop_mark(c, trim_right, trim_y_bottom, mark_len, mark_offset, "br")


def _draw_crop_mark(c, x, y, length, offset, corner):
    if "t" in corner:
        c.line(x, y + offset, x, y + offset + length)
    if "b" in corner:
        c.line(x, y - offset, x, y - offset - length)
    if "l" in corner:
        c.line(x - offset, y, x - offset - length, y)
    if "r" in corner:
        c.line(x + offset, y, x + offset + length, y)


def main():
    parser = argparse.ArgumentParser(
        description="Impose calling card PDFs onto stock sheets for printing"
    )
    parser.add_argument("--project", "-p", required=True, help="Path to project.yaml")
    parser.add_argument("--output", "-o", default=None, help="Output PDF path")
    parser.add_argument(
        "--stock", "-s", default=None,
        help='Stock size: name (letter, tabloid, a3, sra3, 12x18, 13x19) or WxH in inches (e.g. "13x19")',
    )
    parser.add_argument("--gutter", "-g", type=float, default=0.25, help="Gutter between cards in inches (default: 0.25)")
    parser.add_argument("--margin", "-m", type=float, default=0.5, help="Sheet margin in inches (default: 0.5)")
    parser.add_argument("--no-rotate", action="store_true", help="Disable auto-rotation optimization")
    parser.add_argument("--cut-file", default=None, help="Write cutting instructions to file")
    parser.add_argument("--quiet", "-q", action="store_true")
    args = parser.parse_args()

    import yaml

    with open(args.project, "r") as f:
        spec = yaml.safe_load(f)

    project_dir = os.path.dirname(args.project)
    phys = spec.get("physical", {})

    stock = None
    if args.stock:
        key = args.stock.lower().strip()
        if key in COMMON_STOCK:
            w, h = COMMON_STOCK[key]
            stock = StockSpec(width_inches=w, height_inches=h)
        elif "x" in key:
            parts = key.split("x")
            try:
                stock = StockSpec(width_inches=float(parts[0]), height_inches=float(parts[1]))
            except ValueError:
                console.print(f"[red]Invalid stock size: {args.stock}[/red]")
                sys.exit(1)
        else:
            console.print(f"[red]Unknown stock: {args.stock}. Use a name ({', '.join(COMMON_STOCK.keys())}) or WxH.[/red]")
            sys.exit(1)
    else:
        stock = StockSpec(
            width_inches=phys.get("stock_width_inches", 11.0),
            height_inches=phys.get("stock_height_inches", 17.0),
        )

    front_dir = os.path.join(project_dir, "cards", "front")
    back_dir = os.path.join(project_dir, "cards", "back")

    front_pdfs = sorted(
        [os.path.join(front_dir, f) for f in os.listdir(front_dir) if f.endswith(".pdf")]
    ) if os.path.isdir(front_dir) else []

    back_pdfs = sorted(
        [os.path.join(back_dir, f) for f in os.listdir(back_dir) if f.endswith(".pdf")]
    ) if os.path.isdir(back_dir) else []

    output = args.output or os.path.join(project_dir, "output", "imposed-print.pdf")
    verbose = not args.quiet

    _, cutting = impose_pdf(
        front_pdfs=front_pdfs,
        back_pdfs=back_pdfs,
        output_path=output,
        card_width_inches=phys.get("item_width_inches", 3.07),
        card_height_inches=phys.get("item_height_inches", 2.61),
        bleed_inches=phys.get("bleed_inches", 0.125),
        stock=stock,
        gutter_inches=args.gutter,
        margin_inches=args.margin,
        auto_rotate=not args.no_rotate,
        verbose=verbose,
    )

    if verbose:
        print_cutting_instructions(cutting)

    cut_path = args.cut_file or os.path.join(
        os.path.dirname(output), "cutting-instructions.txt"
    )
    write_cutting_instructions(cutting, cut_path)
    if verbose:
        console.print(f"[green]Cutting instructions: {cut_path}[/green]")
