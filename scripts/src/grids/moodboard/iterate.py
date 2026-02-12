"""Moodboard visual iteration -- curate references with visual critique loop.

Extends the base moodboard with:
1. LLM-driven zone assignment (auto-categorize refs into primary/supporting/texture/anti)
2. Visual critique of the rendered moodboard SVG
3. Automated curation: add/remove/re-zone refs based on critique
"""

import json
import os

from langchain_core.messages import HumanMessage, SystemMessage
from rich.console import Console

from grids.moodboard.board import Moodboard, MoodboardRef
from grids.moodboard.research import deep_research, download_references
from grids.orchestration.agents import get_llm
from grids.visual.capture import capture_svg
from grids.visual.critique import visual_critique

console = Console(stderr=True)

ZONE_ASSIGNMENT_PROMPT = """You are a moodboard curator for a design project.

Given a creative brief and a list of visual references, assign each reference
to the most appropriate zone on the moodboard:
- primary: Core visual direction -- the strongest, most relevant references
- supporting: Secondary references that reinforce the direction
- texture: Mood, texture, atmosphere references
- typography: Type specimens and lettering references
- color: Color palette and tonal references
- anti: Counter-examples -- what to specifically avoid

Also assign a relevance score (0.0-1.0) and brief notes explaining placement.

Output a JSON array: [{ref_id, zone, relevance, notes}, ...]"""

CURATE_PROMPT = """You are curating a design moodboard after visual critique.

Given the critique feedback, decide what changes to make:
- remove: list ref IDs to remove (weak/irrelevant references)
- rezone: list {ref_id, new_zone} to move references
- add_queries: list new search queries to find missing reference types

Output JSON: {remove: [...], rezone: [...], add_queries: [...], reasoning: "..."}"""


def auto_zone_refs(board: Moodboard, model: str | None = None) -> Moodboard:
    """Assign zones to unzoned references using LLM."""
    unzoned = [r for r in board.refs if r.zone == "uncategorized"]
    if not unzoned:
        return board

    llm = get_llm(model=model, temperature=0.2)
    refs_summary = json.dumps([
        {"id": r.id, "title": r.title, "artist": r.artist, "tags": r.tags[:5], "source": r.source}
        for r in unzoned
    ], indent=2)

    messages = [
        SystemMessage(content=ZONE_ASSIGNMENT_PROMPT),
        HumanMessage(content=f"Brief: {board.brief}\n\nReferences:\n{refs_summary}"),
    ]
    response = llm.invoke(messages)
    assignments = _parse_json_response(response.content)

    if isinstance(assignments, list):
        assignment_map = {a["ref_id"]: a for a in assignments if "ref_id" in a}
        for ref in board.refs:
            if ref.id in assignment_map:
                a = assignment_map[ref.id]
                ref.zone = a.get("zone", ref.zone)
                ref.relevance = a.get("relevance", ref.relevance)
                ref.notes = a.get("notes", ref.notes)

    return board


def visual_iterate_moodboard(
    board: Moodboard,
    output_dir: str,
    max_iterations: int = 2,
    model: str | None = None,
    verbose: bool = True,
) -> Moodboard:
    """Run visual critique loop on the moodboard.

    1. Render moodboard to SVG
    2. Capture SVG to PNG
    3. Visual critique
    4. Curate (add/remove/rezone)
    5. Repeat
    """
    os.makedirs(output_dir, exist_ok=True)

    for i in range(max_iterations):
        if verbose:
            console.print(f"\n[cyan]Moodboard iteration {i + 1}/{max_iterations}[/cyan]")

        # Render SVG
        svg = board.to_svg()
        svg_path = os.path.join(output_dir, f"moodboard_iter{i + 1}.svg")
        with open(svg_path, "w") as f:
            f.write(svg)

        # Capture to PNG
        png_path = os.path.join(output_dir, f"moodboard_iter{i + 1}.png")
        try:
            capture_svg(svg, png_path)
        except Exception as e:
            if verbose:
                console.print(f"  [yellow]Capture failed: {e}[/yellow]")
            break

        # Visual critique
        critique = visual_critique(
            screenshot_path=png_path,
            brief=f"Moodboard for: {board.brief}",
            model=model,
            iteration=i,
        )

        overall = critique.get("overall_score", 0.0)
        if verbose:
            console.print(f"  Visual score: {overall:.2f}")
            console.print(f"  Feedback: {critique.get('feedback', '')[:200]}")

        board.record_iteration(
            critique=critique.get("feedback", ""),
            changes=json.dumps(critique.get("priority_changes", [])),
            agent="visual-critic",
        )

        if critique.get("verdict") == "approve" or overall >= 0.8:
            if verbose:
                console.print(f"  [green]Moodboard approved[/green]")
            break

        # Curate based on feedback
        board = _curate_from_critique(board, critique, output_dir, model, verbose)

    return board


def _curate_from_critique(
    board: Moodboard,
    critique: dict,
    output_dir: str,
    model: str | None,
    verbose: bool,
) -> Moodboard:
    """Apply curation changes based on visual critique."""
    llm = get_llm(model=model, temperature=0.3)

    refs_summary = json.dumps([
        {"id": r.id, "title": r.title, "zone": r.zone, "relevance": r.relevance}
        for r in board.refs
    ])

    messages = [
        SystemMessage(content=CURATE_PROMPT),
        HumanMessage(content=(
            f"Brief: {board.brief}\n\n"
            f"Critique: {critique.get('feedback', '')}\n"
            f"Priority changes: {json.dumps(critique.get('priority_changes', []))}\n\n"
            f"Current refs:\n{refs_summary}"
        )),
    ]

    response = llm.invoke(messages)
    curation = _parse_json_response(response.content)

    if not isinstance(curation, dict):
        return board

    # Remove weak refs
    remove_ids = set(curation.get("remove", []))
    if remove_ids:
        board.refs = [r for r in board.refs if r.id not in remove_ids]
        if verbose:
            console.print(f"  Removed {len(remove_ids)} refs")

    # Rezone
    for rz in curation.get("rezone", []):
        for ref in board.refs:
            if ref.id == rz.get("ref_id"):
                ref.zone = rz.get("new_zone", ref.zone)

    # Add new searches
    for query in curation.get("add_queries", [])[:3]:
        if verbose:
            console.print(f"  [cyan]Searching: {query}[/cyan]")
        new_results = deep_research(query, limit_per_source=5)
        if new_results:
            dl_dir = os.path.join(output_dir, "refs")
            new_results = download_references(new_results, dl_dir)
            for r in new_results[:3]:
                board.add_ref(MoodboardRef(r))

    # Re-zone any new unzoned refs
    board = auto_zone_refs(board, model=model)

    return board


def _parse_json_response(text: str):
    import re
    try:
        blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
        for block in blocks:
            try:
                return json.loads(block)
            except json.JSONDecodeError:
                continue
        return json.loads(text)
    except json.JSONDecodeError:
        return {}
