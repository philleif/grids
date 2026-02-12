"""Moodboard -- curate research references into a structured visual board with iteration."""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from rich.console import Console
from rich.table import Table

console = Console(stderr=True)


class MoodboardRef:
    """A single reference on a moodboard."""

    def __init__(self, ref_data: dict, notes: str = "", tags: list[str] | None = None,
                 relevance: float = 0.0, zone: str = "uncategorized"):
        self.id = ref_data.get("id", "")
        self.source = ref_data.get("source", "")
        self.title = ref_data.get("title", "")
        self.artist = ref_data.get("artist", "")
        self.date = ref_data.get("date", "")
        self.image_url = ref_data.get("image_url", "")
        self.local_path = ref_data.get("local_path", "")
        self.page_url = ref_data.get("page_url", "")
        self.notes = notes
        self.tags = tags or ref_data.get("tags", [])
        self.relevance = relevance
        self.zone = zone

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "title": self.title,
            "artist": self.artist,
            "date": self.date,
            "image_url": self.image_url,
            "local_path": self.local_path,
            "page_url": self.page_url,
            "notes": self.notes,
            "tags": self.tags,
            "relevance": self.relevance,
            "zone": self.zone,
        }


class Moodboard:
    """A curated moodboard with zones, annotations, and iteration history."""

    def __init__(self, name: str, brief: str):
        self.name = name
        self.brief = brief
        self.refs: list[MoodboardRef] = []
        self.zones: dict[str, str] = {
            "primary": "Core visual direction -- the strongest references",
            "supporting": "Secondary references that reinforce the direction",
            "texture": "Mood, texture, atmosphere references",
            "typography": "Type specimens and lettering references",
            "color": "Color palette and tonal references",
            "anti": "Counter-examples -- what to avoid",
        }
        self.iterations: list[dict] = []
        self.themes: list[str] = []
        self.created = datetime.now(timezone.utc).isoformat()

    def add_ref(self, ref: MoodboardRef):
        self.refs.append(ref)

    def add_refs_from_research(self, research_path: str):
        with open(research_path, "r") as f:
            data = json.load(f)
        for r in data.get("results", []):
            self.add_ref(MoodboardRef(r))

    def refs_by_zone(self) -> dict[str, list[MoodboardRef]]:
        by_zone: dict[str, list[MoodboardRef]] = {}
        for ref in self.refs:
            by_zone.setdefault(ref.zone, []).append(ref)
        return by_zone

    def record_iteration(self, critique: str, changes: str, agent: str):
        self.iterations.append({
            "iteration": len(self.iterations) + 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": agent,
            "critique": critique,
            "changes": changes,
        })

    def to_svg(self, width: int = 1200, height: int = 800) -> str:
        """Render the moodboard as an SVG contact sheet organized by zone."""
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">'
        svg += f'<rect width="{width}" height="{height}" fill="#1a1a1a"/>'

        # Title
        svg += f'<text x="20" y="30" fill="#fff" font-family="Helvetica" font-size="16" font-weight="bold">{_xml_esc(self.name)}</text>'
        svg += f'<text x="20" y="48" fill="#888" font-family="Helvetica" font-size="10">{_xml_esc(self.brief[:100])}</text>'

        by_zone = self.refs_by_zone()
        y_offset = 70
        thumb_size = 120
        gap = 10

        for zone_name, zone_desc in self.zones.items():
            zone_refs = by_zone.get(zone_name, [])
            if not zone_refs:
                continue

            svg += f'<text x="20" y="{y_offset}" fill="#ccc" font-family="Helvetica" font-size="11" font-weight="bold">{zone_name.upper()}</text>'
            svg += f'<text x="20" y="{y_offset + 14}" fill="#666" font-family="Helvetica" font-size="8">{_xml_esc(zone_desc)}</text>'
            y_offset += 24

            x = 20
            for ref in zone_refs[:8]:
                # Placeholder rect for each reference
                fill = "#333" if zone_name != "anti" else "#3a1a1a"
                svg += f'<rect x="{x}" y="{y_offset}" width="{thumb_size}" height="{thumb_size}" fill="{fill}" rx="4"/>'

                if ref.local_path:
                    svg += f'<image href="{ref.local_path}" x="{x}" y="{y_offset}" width="{thumb_size}" height="{thumb_size}" preserveAspectRatio="xMidYMid slice"/>'

                # Label
                label_y = y_offset + thumb_size + 12
                svg += f'<text x="{x}" y="{label_y}" fill="#aaa" font-family="Helvetica" font-size="7">{_xml_esc(ref.title[:20])}</text>'
                if ref.notes:
                    svg += f'<text x="{x}" y="{label_y + 10}" fill="#666" font-family="Helvetica" font-size="6">{_xml_esc(ref.notes[:30])}</text>'

                x += thumb_size + gap

            y_offset += thumb_size + 30

        # Iteration history sidebar
        if self.iterations:
            ix = width - 280
            iy = 70
            svg += f'<text x="{ix}" y="{iy}" fill="#ccc" font-family="Helvetica" font-size="11" font-weight="bold">ITERATIONS</text>'
            iy += 16
            for it in self.iterations[-5:]:
                svg += f'<text x="{ix}" y="{iy}" fill="#888" font-family="Helvetica" font-size="7">#{it["iteration"]} by {it["agent"]}</text>'
                iy += 10
                svg += f'<text x="{ix}" y="{iy}" fill="#666" font-family="Helvetica" font-size="6">{_xml_esc(it["critique"][:60])}</text>'
                iy += 14

        svg += '</svg>'
        return svg

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "brief": self.brief,
            "created": self.created,
            "themes": self.themes,
            "zones": self.zones,
            "refs": [r.to_dict() for r in self.refs],
            "iterations": self.iterations,
        }

    def save(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str) -> "Moodboard":
        with open(path, "r") as f:
            data = json.load(f)
        board = cls(data["name"], data["brief"])
        board.created = data.get("created", "")
        board.themes = data.get("themes", [])
        board.zones = data.get("zones", board.zones)
        board.iterations = data.get("iterations", [])
        for rd in data.get("refs", []):
            board.add_ref(MoodboardRef(
                rd, notes=rd.get("notes", ""),
                tags=rd.get("tags"), relevance=rd.get("relevance", 0),
                zone=rd.get("zone", "uncategorized"),
            ))
        return board


def _xml_esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def main():
    parser = argparse.ArgumentParser(description="Moodboard -- curate visual references")
    sub = parser.add_subparsers(dest="command")

    create_p = sub.add_parser("create", help="Create a new moodboard from research")
    create_p.add_argument("name", help="Moodboard name")
    create_p.add_argument("--brief", "-b", required=True, help="Creative brief")
    create_p.add_argument("--research", "-r", help="Path to research.json from deep-research")
    create_p.add_argument("--output", "-o", default=None, help="Output directory")

    render_p = sub.add_parser("render", help="Render moodboard to SVG")
    render_p.add_argument("board", help="Path to moodboard.json")
    render_p.add_argument("--output", "-o", default=None, help="Output SVG path")

    show_p = sub.add_parser("show", help="Show moodboard summary")
    show_p.add_argument("board", help="Path to moodboard.json")

    args = parser.parse_args()

    if args.command == "create":
        board = Moodboard(args.name, args.brief)
        if args.research:
            board.add_refs_from_research(args.research)
            console.print(f"[green]Added {len(board.refs)} references from research[/green]")

        out_dir = args.output or os.path.join("tmp", "moodboard", args.name.replace(" ", "-"))
        board_path = os.path.join(out_dir, "moodboard.json")
        board.save(board_path)
        console.print(f"[green]Created moodboard: {board_path}[/green]")

    elif args.command == "render":
        board = Moodboard.load(args.board)
        svg = board.to_svg()
        out = args.output or args.board.replace(".json", ".svg")
        with open(out, "w") as f:
            f.write(svg)
        console.print(f"[green]Rendered to {out}[/green]")

    elif args.command == "show":
        board = Moodboard.load(args.board)
        table = Table(title=f"Moodboard: {board.name}")
        table.add_column("Zone")
        table.add_column("Count")
        table.add_column("Sample")
        by_zone = board.refs_by_zone()
        for zone in board.zones:
            refs = by_zone.get(zone, [])
            sample = refs[0].title if refs else "-"
            table.add_row(zone, str(len(refs)), sample)
        console.print(table)
        console.print(f"\nBrief: {board.brief}")
        console.print(f"Iterations: {len(board.iterations)}")
        console.print(f"Total refs: {len(board.refs)}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
