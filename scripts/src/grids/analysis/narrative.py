"""Linear narrative + highlights generation.

Produces:
- A chronological prose narrative of the session
- A highlights reel of the 5-10 most significant moments
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from grids.analysis.stream_parser import ParsedStream
from grids.analysis.workflow import WorkflowBreakdown


@dataclass
class Narrative:
    linear_narrative: str = ""
    highlights: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "linear_narrative": self.linear_narrative,
            "highlights": self.highlights,
        }


def generate_narrative(
    parsed: ParsedStream,
    workflow: WorkflowBreakdown,
    use_llm: bool = True,
) -> Narrative:
    """Generate the session narrative and highlights."""
    narrative = Narrative()

    if not use_llm:
        narrative.linear_narrative = _build_narrative_heuristic(parsed, workflow)
        narrative.highlights = _build_highlights_heuristic(parsed, workflow)
        return narrative

    try:
        from grids.orchestration.agents import get_llm
        from langchain_core.messages import HumanMessage, SystemMessage
    except ImportError:
        narrative.linear_narrative = _build_narrative_heuristic(parsed, workflow)
        narrative.highlights = _build_highlights_heuristic(parsed, workflow)
        return narrative

    llm = get_llm(temperature=0.4)

    # Build the context for the LLM
    events_context = _build_events_context(parsed)
    workflow_context = json.dumps(workflow.to_dict(), indent=2, default=str)[:8000]

    # Generate linear narrative
    narrative_prompt = (
        "Write a chronological narrative of this AI agent orchestration session. "
        "Write in present tense, like a sports commentator or war correspondent covering events as they unfold. "
        "Be specific -- name the agents, quote their distinctive contributions, and highlight disagreements. "
        "The narrative should be 3-6 paragraphs, vivid and engaging, not a dry summary.\n\n"
        f"Brief: {parsed.brief[:500]}\n\n"
        f"Grid: {parsed.grid_size}, {parsed.cell_count} cells, {len(parsed.domains)} domains\n\n"
        f"Workflow breakdown:\n{workflow_context}\n\n"
        f"Agent events (chronological):\n{events_context}\n\n"
        "Write the narrative now. No JSON, no markdown headers -- just prose."
    )

    try:
        response = llm.invoke([
            SystemMessage(content=(
                "You write vivid session narratives for a cellular automaton-based AI agent system called GRIDS. "
                "The system arranges AI agents on a 2D grid where they interact through local rules only -- "
                "no central planner. Complex output emerges from simple interactions. "
                "Your narratives should convey the drama of emergence: agents surprising each other, "
                "critiques that reshape the work, unexpected connections between domains."
            )),
            HumanMessage(content=narrative_prompt),
        ])
        narrative.linear_narrative = response.content.strip()
    except Exception:
        narrative.linear_narrative = _build_narrative_heuristic(parsed, workflow)

    # Generate highlights
    highlights_prompt = (
        "From this session, identify the 5-10 most significant moments. "
        "Criteria: highest-impact critiques, most novel contributions, sharpest disagreements, "
        "best examples of emergence (where agent interactions produced something none would alone).\n\n"
        f"Agent events:\n{events_context}\n\n"
        "Output JSON: {\"highlights\": [{\"tick\": N, \"title\": \"short title\", "
        "\"description\": \"2-3 sentences\", \"agents_involved\": [\"domain/agent\", ...], "
        "\"category\": \"critique|emergence|disagreement|contribution\"}]}"
    )

    try:
        response = llm.invoke([
            SystemMessage(content="You identify key moments in AI agent orchestration sessions."),
            HumanMessage(content=highlights_prompt),
        ])
        text = response.content.strip()
        import re
        blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
        parsed_json = json.loads(blocks[0] if blocks else text)
        narrative.highlights = parsed_json.get("highlights", [])
    except Exception:
        narrative.highlights = _build_highlights_heuristic(parsed, workflow)

    return narrative


def _build_events_context(parsed: ParsedStream) -> str:
    """Build a concise event timeline for LLM context."""
    lines = []
    for call in parsed.llm_calls:
        score_str = ""
        if call.score is not None:
            score_str = f" [score: {call.score}, verdict: {call.verdict}]"
        summary = call.chat_summary or call.response[:200]
        # Truncate summary for context window
        if len(summary) > 300:
            summary = summary[:300] + "..."
        lines.append(
            f"Tick {call.tick} | {call.domain}/{call.agent} ({call.action}){score_str}: {summary}"
        )
    return "\n".join(lines)


def _build_narrative_heuristic(parsed: ParsedStream, workflow: WorkflowBreakdown) -> str:
    """Build a basic narrative without LLM."""
    parts = []

    # Opening
    parts.append(
        f"The run begins with a {parsed.grid_size} grid of {parsed.cell_count} cells "
        f"across {len(parsed.domains)} domains: {', '.join(parsed.domains)}."
    )

    # Per-tick highlights
    current_tick = 0
    for call in parsed.llm_calls:
        if call.tick != current_tick:
            current_tick = call.tick
            tick_calls = [c for c in parsed.llm_calls if c.tick == current_tick]
            actions = set(c.action for c in tick_calls)
            domains = set(c.domain for c in tick_calls)
            parts.append(
                f"\nTick {current_tick}: {len(tick_calls)} agents fire across {', '.join(sorted(domains))} "
                f"({', '.join(sorted(actions))})."
            )

            # Highlight any notable critiques
            for c in tick_calls:
                if c.score is not None and (c.score <= 10 or c.score >= 90):
                    parts.append(
                        f"  {c.domain}/{c.agent} scores {c.score} ({c.verdict}): "
                        f"{(c.chat_summary or c.response[:100])[:150]}"
                    )

    # Closing
    run_end = parsed.run_end
    if run_end:
        elapsed = run_end.get("elapsed", 0)
        minutes = elapsed / 60
        parts.append(
            f"\nThe run concludes after {run_end.get('ticks', '?')} ticks and "
            f"{minutes:.1f} minutes with {run_end.get('llm_calls', '?')} LLM calls."
        )

    return "\n".join(parts)


def _build_highlights_heuristic(parsed: ParsedStream, workflow: WorkflowBreakdown) -> list[dict]:
    """Build highlights without LLM, using score extremes and token counts."""
    highlights = []
    critiques = [c for c in parsed.llm_calls if c.score is not None]

    # Harshest critique
    if critiques:
        worst = min(critiques, key=lambda c: c.score)
        highlights.append({
            "tick": worst.tick,
            "title": f"Harshest critique: {worst.domain}/{worst.agent}",
            "description": f"Scored {worst.score} ({worst.verdict}). {(worst.chat_summary or worst.response[:200])[:200]}",
            "agents_involved": [f"{worst.domain}/{worst.agent}"],
            "category": "critique",
        })

    # Highest approval
    if critiques:
        best = max(critiques, key=lambda c: c.score)
        highlights.append({
            "tick": best.tick,
            "title": f"Strongest endorsement: {best.domain}/{best.agent}",
            "description": f"Scored {best.score} ({best.verdict}). {(best.chat_summary or best.response[:200])[:200]}",
            "agents_involved": [f"{best.domain}/{best.agent}"],
            "category": "critique",
        })

    # Most substantial contribution (by tokens)
    if parsed.llm_calls:
        biggest = max(parsed.llm_calls, key=lambda c: c.tokens)
        highlights.append({
            "tick": biggest.tick,
            "title": f"Largest contribution: {biggest.domain}/{biggest.agent}",
            "description": f"{biggest.tokens} tokens. {(biggest.chat_summary or biggest.response[:200])[:200]}",
            "agents_involved": [f"{biggest.domain}/{biggest.agent}"],
            "category": "contribution",
        })

    # Score disagreements (same tick, different scores)
    tick_scores: dict[int, list] = {}
    for c in critiques:
        tick_scores.setdefault(c.tick, []).append(c)
    for tick, calls in tick_scores.items():
        if len(calls) >= 2:
            scores = [c.score for c in calls]
            spread = max(scores) - min(scores)
            if spread >= 40:
                low = min(calls, key=lambda c: c.score)
                high = max(calls, key=lambda c: c.score)
                highlights.append({
                    "tick": tick,
                    "title": f"Score spread: {int(spread)} points",
                    "description": (
                        f"{low.domain}/{low.agent} scored {low.score} vs "
                        f"{high.domain}/{high.agent} scored {high.score}. "
                        f"Domains disagree on quality."
                    ),
                    "agents_involved": [f"{low.domain}/{low.agent}", f"{high.domain}/{high.agent}"],
                    "category": "disagreement",
                })

    return sorted(highlights, key=lambda h: h["tick"])
