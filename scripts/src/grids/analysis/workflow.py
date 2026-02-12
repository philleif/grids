"""Workflow breakdown analysis.

Produces a structured phase-by-phase analysis of a grid run:
- Per-phase stats (ticks, LLM calls, items emitted)
- Per-domain contribution breakdown
- Key moments identification (LLM-assisted)
- Flow economics analysis (WSJF, WIP, rework)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from grids.analysis.stream_parser import ParsedStream, LLMCall, TickEvent


@dataclass
class DomainContribution:
    domain: str
    cells_active: int = 0
    items_emitted: int = 0
    total_tokens: int = 0
    critique_scores: list[int | float] = field(default_factory=list)
    verdicts: list[str] = field(default_factory=list)
    agents_active: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "cells_active": self.cells_active,
            "items_emitted": self.items_emitted,
            "total_tokens": self.total_tokens,
            "critique_scores": self.critique_scores,
            "verdicts": self.verdicts,
            "agents_active": self.agents_active,
            "avg_score": round(sum(self.critique_scores) / len(self.critique_scores), 1) if self.critique_scores else None,
            "pass_rate": round(
                sum(1 for v in self.verdicts if v == "pass") / len(self.verdicts), 2
            ) if self.verdicts else None,
        }


@dataclass
class FlowAnalysis:
    wsjf_effectiveness: str = ""
    wip_limit_violations: int = 0
    propagation_rejections: int = 0
    rework_cycles: int = 0
    split_batch_count: int = 0
    pull_count: int = 0
    escalate_count: int = 0
    challenge_count: int = 0
    gap_analysis_count: int = 0

    def to_dict(self) -> dict:
        return {
            "wsjf_effectiveness": self.wsjf_effectiveness,
            "wip_limit_violations": self.wip_limit_violations,
            "propagation_rejections": self.propagation_rejections,
            "rework_cycles": self.rework_cycles,
            "split_batch_count": self.split_batch_count,
            "pull_count": self.pull_count,
            "escalate_count": self.escalate_count,
            "challenge_count": self.challenge_count,
            "gap_analysis_count": self.gap_analysis_count,
        }


@dataclass
class PhaseBreakdown:
    phase: str
    summary: str = ""
    tick_range: list[int] = field(default_factory=list)
    active_ticks: int = 0
    quiescent_ticks: int = 0
    llm_calls: int = 0
    total_tokens: int = 0
    items_emitted: int = 0
    key_moments: list[dict] = field(default_factory=list)
    domain_contributions: dict[str, DomainContribution] = field(default_factory=dict)
    flow_analysis: FlowAnalysis = field(default_factory=FlowAnalysis)

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "summary": self.summary,
            "tick_range": self.tick_range,
            "active_ticks": self.active_ticks,
            "quiescent_ticks": self.quiescent_ticks,
            "llm_calls": self.llm_calls,
            "total_tokens": self.total_tokens,
            "items_emitted": self.items_emitted,
            "key_moments": self.key_moments,
            "domain_contributions": {
                d: c.to_dict() for d, c in self.domain_contributions.items()
            },
            "flow_analysis": self.flow_analysis.to_dict(),
        }


@dataclass
class WorkflowBreakdown:
    phases: list[PhaseBreakdown] = field(default_factory=list)
    overall_summary: str = ""
    activity_curve: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "phases": [p.to_dict() for p in self.phases],
            "overall_summary": self.overall_summary,
            "activity_curve": self.activity_curve,
        }


def analyze_workflow(parsed: ParsedStream) -> WorkflowBreakdown:
    """Build the workflow breakdown from parsed stream data.

    Currently handles single-phase runs (Phase 1a only) and multi-phase runs.
    Phase boundaries are detected from phase_start events in the stream,
    or inferred from tick activity patterns.
    """
    breakdown = WorkflowBreakdown()

    # Build activity curve
    breakdown.activity_curve = [
        {
            "tick": t.tick,
            "llm_calls": t.llm_calls,
            "actions": t.actions,
            "emitted": t.emitted,
            "elapsed": t.elapsed,
        }
        for t in parsed.ticks
    ]

    # Detect phases from stream markers
    if parsed.phase_starts:
        phase_boundaries = _detect_phase_boundaries(parsed)
    else:
        # Single-phase run -- treat everything as one phase
        phase_boundaries = [("1a", 1, parsed.ticks[-1].tick if parsed.ticks else 0)]

    for phase_name, start_tick, end_tick in phase_boundaries:
        phase = _analyze_phase(parsed, phase_name, start_tick, end_tick)
        breakdown.phases.append(phase)

    # Overall summary
    total_llm = sum(p.llm_calls for p in breakdown.phases)
    total_tokens = sum(p.total_tokens for p in breakdown.phases)
    total_ticks = len(parsed.ticks)
    active = parsed.active_ticks
    breakdown.overall_summary = (
        f"Run completed in {total_ticks} ticks ({active} active, {total_ticks - active} quiescent). "
        f"{total_llm} LLM calls generating {total_tokens:,} tokens across {len(parsed.domains)} domains. "
        f"{len(parsed.llm_calls)} agent emissions total."
    )

    return breakdown


def _detect_phase_boundaries(parsed: ParsedStream) -> list[tuple[str, int, int]]:
    """Detect phase boundaries from phase_start events."""
    boundaries = []
    for i, ps in enumerate(parsed.phase_starts):
        phase = ps.get("phase", f"phase-{i}")
        # Find the tick range for this phase
        # The phase starts at the tick after the phase_start event
        start_tick = 1
        for t in parsed.ticks:
            if t.ts >= ps.get("ts", 0):
                start_tick = t.tick
                break

        # End tick: either the next phase_start or the last tick
        if i + 1 < len(parsed.phase_starts):
            end_ts = parsed.phase_starts[i + 1].get("ts", float("inf"))
            end_tick = start_tick
            for t in parsed.ticks:
                if t.ts < end_ts:
                    end_tick = t.tick
                else:
                    break
        else:
            end_tick = parsed.ticks[-1].tick if parsed.ticks else start_tick

        boundaries.append((phase, start_tick, end_tick))

    return boundaries


def _analyze_phase(
    parsed: ParsedStream,
    phase_name: str,
    start_tick: int,
    end_tick: int,
) -> PhaseBreakdown:
    """Analyze a single phase within the run."""
    phase = PhaseBreakdown(phase=phase_name)
    phase.tick_range = [start_tick, end_tick]

    # Filter ticks and calls to this phase
    phase_ticks = [t for t in parsed.ticks if start_tick <= t.tick <= end_tick]
    phase_calls = [c for c in parsed.llm_calls if start_tick <= c.tick <= end_tick]

    phase.active_ticks = sum(1 for t in phase_ticks if t.llm_calls > 0)
    phase.quiescent_ticks = sum(1 for t in phase_ticks if t.llm_calls == 0)
    phase.llm_calls = sum(t.llm_calls for t in phase_ticks)
    phase.items_emitted = sum(t.emitted for t in phase_ticks)
    phase.total_tokens = sum(c.tokens for c in phase_calls)

    # Domain contributions
    for call in phase_calls:
        dc = phase.domain_contributions.setdefault(
            call.domain, DomainContribution(domain=call.domain)
        )
        dc.items_emitted += 1
        dc.total_tokens += call.tokens
        if call.agent not in dc.agents_active:
            dc.agents_active.append(call.agent)
        dc.cells_active = len(dc.agents_active)
        if call.score is not None:
            dc.critique_scores.append(call.score)
        if call.verdict is not None:
            dc.verdicts.append(call.verdict)

    # Flow analysis from tick cell_actions
    flow = FlowAnalysis()
    for t in phase_ticks:
        for ca in t.cell_actions:
            action = ca.get("action", "")
            if action == "split_batch":
                flow.split_batch_count += 1
            elif action == "pull":
                flow.pull_count += 1
            elif action == "escalate":
                flow.escalate_count += 1
            elif action == "challenge":
                flow.challenge_count += 1
            elif action == "gap_analysis":
                flow.gap_analysis_count += 1

    # Count rework from critique failures
    flow.rework_cycles = sum(
        1 for c in phase_calls
        if c.verdict in ("fail", "iterate")
    )

    phase.flow_analysis = flow

    # Summary
    domain_list = ", ".join(sorted(phase.domain_contributions.keys()))
    phase.summary = (
        f"Phase {phase_name}: {len(phase_calls)} LLM calls across {len(phase.domain_contributions)} domains "
        f"({domain_list}). {phase.active_ticks} active ticks, {phase.quiescent_ticks} quiescent. "
        f"{phase.total_tokens:,} tokens generated."
    )

    return phase


def identify_key_moments(
    parsed: ParsedStream,
    workflow: WorkflowBreakdown,
    use_llm: bool = True,
) -> None:
    """Identify the key moments in each phase via LLM analysis.
    Mutates the PhaseBreakdown objects in place.
    """
    if not use_llm:
        _identify_key_moments_heuristic(parsed, workflow)
        return

    try:
        from grids.orchestration.agents import get_llm
        from langchain_core.messages import HumanMessage, SystemMessage
    except ImportError:
        _identify_key_moments_heuristic(parsed, workflow)
        return

    llm = get_llm(temperature=0.2)

    for phase in workflow.phases:
        phase_calls = [
            c for c in parsed.llm_calls
            if phase.tick_range[0] <= c.tick <= phase.tick_range[1]
        ]
        if not phase_calls:
            continue

        events_summary = []
        for c in phase_calls:
            summary = c.chat_summary or c.response[:300]
            score_str = f" [score: {c.score}, verdict: {c.verdict}]" if c.score is not None else ""
            events_summary.append(
                f"Tick {c.tick} | {c.domain}/{c.agent} ({c.action}){score_str}: {summary[:200]}"
            )

        prompt = (
            f"Analyze these agent events from Phase {phase.phase} of a cellular automaton grid run "
            f"and identify the 5-8 most significant moments.\n\n"
            f"Events:\n" + "\n".join(events_summary) + "\n\n"
            f"For each key moment, provide:\n"
            f"- tick: the tick number\n"
            f"- event: what happened (1 sentence)\n"
            f"- significance: why it matters for the overall run (1 sentence)\n\n"
            f"Output JSON: {{\"key_moments\": [{{\"tick\": N, \"event\": \"...\", \"significance\": \"...\"}}]}}"
        )

        try:
            response = llm.invoke([
                SystemMessage(content="You analyze AI agent orchestration sessions for a cellular automaton-based system."),
                HumanMessage(content=prompt),
            ])
            text = response.content.strip()
            import re
            blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
            parsed_json = json.loads(blocks[0] if blocks else text)
            phase.key_moments = parsed_json.get("key_moments", [])
        except Exception:
            _identify_key_moments_heuristic_phase(parsed, phase)


def _identify_key_moments_heuristic(parsed: ParsedStream, workflow: WorkflowBreakdown) -> None:
    """Identify key moments without LLM, using heuristics."""
    for phase in workflow.phases:
        _identify_key_moments_heuristic_phase(parsed, phase)


def _identify_key_moments_heuristic_phase(parsed: ParsedStream, phase: PhaseBreakdown) -> None:
    """Heuristic key moment detection for a single phase."""
    phase_calls = [
        c for c in parsed.llm_calls
        if phase.tick_range[0] <= c.tick <= phase.tick_range[1]
    ]

    moments = []

    # First tick with activity
    if phase_calls:
        first = phase_calls[0]
        moments.append({
            "tick": first.tick,
            "event": f"First activity: {first.domain}/{first.agent} ({first.action})",
            "significance": "Grid activation begins",
        })

    # Lowest critique score
    critiques = [c for c in phase_calls if c.score is not None]
    if critiques:
        worst = min(critiques, key=lambda c: c.score)
        moments.append({
            "tick": worst.tick,
            "event": f"Harshest critique: {worst.domain}/{worst.agent} scored {worst.score} ({worst.verdict})",
            "significance": f"Strongest pushback from {worst.domain} domain",
        })

        best = max(critiques, key=lambda c: c.score)
        if best.seq != worst.seq:
            moments.append({
                "tick": best.tick,
                "event": f"Highest approval: {best.domain}/{best.agent} scored {best.score} ({best.verdict})",
                "significance": f"Strongest endorsement from {best.domain} domain",
            })

    # Highest token output (most substantial contribution)
    if phase_calls:
        biggest = max(phase_calls, key=lambda c: c.tokens)
        moments.append({
            "tick": biggest.tick,
            "event": f"Largest output: {biggest.domain}/{biggest.agent} ({biggest.tokens} tokens)",
            "significance": "Most substantial single agent contribution",
        })

    # Last activity tick
    if phase_calls:
        last = phase_calls[-1]
        if last.tick != phase_calls[0].tick:
            moments.append({
                "tick": last.tick,
                "event": f"Final activity: {last.domain}/{last.agent} ({last.action})",
                "significance": "Last productive action before quiescence",
            })

    phase.key_moments = sorted(moments, key=lambda m: m["tick"])
