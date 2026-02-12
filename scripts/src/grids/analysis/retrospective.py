"""ANKOS thesis evaluation retrospective.

Evaluates a grid run against the core GRIDS thesis:
A. Cellular Automata Dynamics (Class 4, anti-quiescence, totalistic signals)
B. Reinertsen Flow Economics (WSJF, WIP, iteration, batch splitting)
C. Domain Expertise Grounding (knowledge retrieval, citation, calibration)
D. Emergence Assessment (novel output from interactions)
E. What Worked / What Didn't / What to Try Next
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from grids.analysis.stream_parser import ParsedStream
from grids.analysis.workflow import WorkflowBreakdown
from grids.analysis.narrative import Narrative


@dataclass
class Retrospective:
    ca_dynamics: dict = field(default_factory=dict)
    flow_economics: dict = field(default_factory=dict)
    domain_grounding: dict = field(default_factory=dict)
    emergence: dict = field(default_factory=dict)
    what_worked: list[str] = field(default_factory=list)
    what_didnt: list[str] = field(default_factory=list)
    what_to_try: list[str] = field(default_factory=list)
    health_score: float = 0.5

    def to_dict(self) -> dict:
        return {
            "ca_dynamics": self.ca_dynamics,
            "flow_economics": self.flow_economics,
            "domain_grounding": self.domain_grounding,
            "emergence": self.emergence,
            "what_worked": self.what_worked,
            "what_didnt": self.what_didnt,
            "what_to_try": self.what_to_try,
            "health_score": self.health_score,
        }


RETROSPECTIVE_SYSTEM = """You are the retrospective analyst for GRIDS, a cellular automaton-based AI agent orchestration system.

You evaluate grid runs against the system's core thesis, built on three pillars:

1. WOLFRAM LOCAL RULES (NKS): Complex output emerges from agents following simple local rules on a 2D grid. Each agent has a rule table: (state, signal) -> (action, next_state). Key NKS principles:
   - Totalistic rules: signals fire based on aggregate neighbor state counts (not individual checks)
   - Class 4 behavior: the "edge of chaos" -- complex, sustained, non-repetitive patterns
   - Anti-quiescence: STALE signal fires when cells are idle too long while neighbors produce
   - Rule space search: don't design rules by intuition, search for them

2. REINERTSEN FLOW ECONOMICS: Optimize for flow, not utilization.
   - WSJF: cost_of_delay / job_size prioritization
   - WIP limits: hard caps prevent cascade failures
   - Iteration economics: rework gets 1.2x CoD, 0.7x size -- naturally rises in priority
   - Batch splitting: overloaded cells share work with idle neighbors

3. DOMAIN EXPERTISE GROUNDING: Agents are grounded in actual reference texts via ChromaDB.
   - Sub-agents score narrow aspects with configurable strictness
   - Masters have veto power
   - Knowledge retrieval should produce cited, traceable output

Your evaluation must be concrete, grounded in the actual run data, and include specific proposals for improvement. No vague praise -- identify exactly what worked and what failed."""


def generate_retrospective(
    parsed: ParsedStream,
    workflow: WorkflowBreakdown,
    narrative: Narrative,
    ankos_path: str | Path | None = None,
    use_llm: bool = True,
) -> Retrospective:
    """Generate the ANKOS thesis evaluation retrospective."""
    retro = Retrospective()

    # Always compute quantitative metrics
    retro.ca_dynamics = _compute_ca_metrics(parsed, workflow)
    retro.flow_economics = _compute_flow_metrics(parsed, workflow)
    retro.domain_grounding = _compute_domain_metrics(parsed, workflow)

    if not use_llm:
        retro.emergence = _compute_emergence_heuristic(parsed)
        _generate_heuristic_assessment(retro, parsed, workflow)
        return retro

    try:
        from grids.orchestration.agents import get_llm
        from langchain_core.messages import HumanMessage, SystemMessage
    except ImportError:
        retro.emergence = _compute_emergence_heuristic(parsed)
        _generate_heuristic_assessment(retro, parsed, workflow)
        return retro

    # Load ANKOS framework if available
    ankos_context = ""
    if ankos_path and Path(ankos_path).exists():
        ankos_context = Path(ankos_path).read_text(encoding="utf-8")[:4000]

    llm = get_llm(temperature=0.3)

    # Build comprehensive context
    workflow_json = json.dumps(workflow.to_dict(), indent=2, default=str)[:6000]
    metrics_json = json.dumps({
        "ca_dynamics": retro.ca_dynamics,
        "flow_economics": retro.flow_economics,
        "domain_grounding": retro.domain_grounding,
    }, indent=2, default=str)

    events_summary = []
    for c in parsed.llm_calls:
        score_str = f" [score: {c.score}, verdict: {c.verdict}]" if c.score is not None else ""
        summary = c.chat_summary or c.response[:200]
        events_summary.append(
            f"T{c.tick} {c.domain}/{c.agent} ({c.action}){score_str}: {summary[:250]}"
        )
    events_text = "\n".join(events_summary)

    prompt = (
        f"Evaluate this GRIDS run against the ANKOS thesis.\n\n"
        f"Brief: {parsed.brief[:400]}\n"
        f"Grid: {parsed.grid_size}, {parsed.cell_count} cells\n"
        f"Duration: {parsed.run_end.get('elapsed', 0):.0f}s, "
        f"{parsed.run_end.get('ticks', 0)} ticks, "
        f"{parsed.run_end.get('llm_calls', 0)} LLM calls\n\n"
        f"Quantitative metrics:\n{metrics_json}\n\n"
        f"Workflow breakdown:\n{workflow_json}\n\n"
        f"Narrative:\n{narrative.linear_narrative[:2000]}\n\n"
        f"Agent events:\n{events_text}\n\n"
    )
    if ankos_context:
        prompt += f"ANKOS framework reference:\n{ankos_context}\n\n"

    prompt += (
        "Produce a JSON retrospective with these sections:\n"
        "{\n"
        '  "emergence": {\n'
        '    "assessment": "Did multi-agent interaction produce novel insights? Specific examples.",\n'
        '    "emergence_examples": ["example 1", ...],\n'
        '    "emergence_score": 0.0-1.0\n'
        "  },\n"
        '  "what_worked": ["specific observation 1", ...],\n'
        '  "what_didnt": ["specific failure 1", ...],\n'
        '  "what_to_try": ["concrete proposal 1", ...],\n'
        '  "health_score": 0.0-1.0,\n'
        '  "ca_dynamics_assessment": "Prose assessment of CA behavior",\n'
        '  "flow_economics_assessment": "Prose assessment of flow",\n'
        '  "domain_grounding_assessment": "Prose assessment of knowledge grounding"\n'
        "}"
    )

    try:
        response = llm.invoke([
            SystemMessage(content=RETROSPECTIVE_SYSTEM),
            HumanMessage(content=prompt),
        ])
        text = response.content.strip()
        import re
        blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
        result = json.loads(blocks[0] if blocks else text)

        retro.emergence = result.get("emergence", _compute_emergence_heuristic(parsed))
        retro.what_worked = result.get("what_worked", [])
        retro.what_didnt = result.get("what_didnt", [])
        retro.what_to_try = result.get("what_to_try", [])
        retro.health_score = result.get("health_score", 0.5)

        # Merge prose assessments into the quantitative sections
        if "ca_dynamics_assessment" in result:
            retro.ca_dynamics["assessment"] = result["ca_dynamics_assessment"]
        if "flow_economics_assessment" in result:
            retro.flow_economics["assessment"] = result["flow_economics_assessment"]
        if "domain_grounding_assessment" in result:
            retro.domain_grounding["assessment"] = result["domain_grounding_assessment"]

    except Exception:
        retro.emergence = _compute_emergence_heuristic(parsed)
        _generate_heuristic_assessment(retro, parsed, workflow)

    return retro


def _compute_ca_metrics(parsed: ParsedStream, workflow: WorkflowBreakdown) -> dict:
    """Quantitative CA dynamics metrics."""
    total_ticks = len(parsed.ticks)
    active_ticks = parsed.active_ticks
    quiescent_ticks = parsed.quiescent_ticks

    # Activity curve classification
    if total_ticks == 0:
        activity_ratio = 0
    else:
        activity_ratio = active_ticks / total_ticks

    # Classify behavior
    if activity_ratio < 0.2:
        ca_class = "Class 1/2 (premature quiescence)"
    elif activity_ratio > 0.8:
        ca_class = "Class 3 (chaotic/wasteful)"
    elif 0.3 <= activity_ratio <= 0.6:
        ca_class = "Class 4 (edge of chaos -- ideal)"
    else:
        ca_class = "Near Class 4"

    # Anti-quiescence effectiveness
    challenges = sum(
        1 for p in workflow.phases for _ in [p.flow_analysis.challenge_count] if p.flow_analysis.challenge_count > 0
    )
    gap_analyses = sum(p.flow_analysis.gap_analysis_count for p in workflow.phases)

    # Activity concentration
    if parsed.ticks:
        first_half = parsed.ticks[:len(parsed.ticks) // 2]
        second_half = parsed.ticks[len(parsed.ticks) // 2:]
        first_half_llm = sum(t.llm_calls for t in first_half)
        second_half_llm = sum(t.llm_calls for t in second_half)
    else:
        first_half_llm = second_half_llm = 0

    return {
        "total_ticks": total_ticks,
        "active_ticks": active_ticks,
        "quiescent_ticks": quiescent_ticks,
        "activity_ratio": round(activity_ratio, 3),
        "ca_class": ca_class,
        "anti_quiescence_challenges": challenges,
        "anti_quiescence_gap_analyses": gap_analyses,
        "activity_first_half_llm": first_half_llm,
        "activity_second_half_llm": second_half_llm,
        "activity_concentration": "front-loaded" if first_half_llm > second_half_llm * 3 else (
            "back-loaded" if second_half_llm > first_half_llm * 3 else "distributed"
        ),
    }


def _compute_flow_metrics(parsed: ParsedStream, workflow: WorkflowBreakdown) -> dict:
    """Quantitative Reinertsen flow metrics + two-level performance (GRD-6)."""
    total_rework = sum(p.flow_analysis.rework_cycles for p in workflow.phases)
    total_splits = sum(p.flow_analysis.split_batch_count for p in workflow.phases)
    total_pulls = sum(p.flow_analysis.pull_count for p in workflow.phases)
    total_escalates = sum(p.flow_analysis.escalate_count for p in workflow.phases)

    # Critique pass/fail rates
    critiques = parsed.critique_scores()
    pass_count = sum(1 for c in critiques if c["verdict"] == "pass")
    fail_count = sum(1 for c in critiques if c["verdict"] in ("fail", "iterate"))

    # Two-level metrics (GRD-6) -- retroactive computation from stream
    routing = parsed.compute_routing_summary()
    quality = parsed.compute_quality_summary()

    avg_q = quality.avg_critique_score
    p_good = None
    if avg_q is not None and routing.items_scheduled > 0:
        qs_norm = min(1.0, max(0.0, avg_q / 100.0)) if avg_q > 1.0 else avg_q
        p_good = routing.routing_efficiency * qs_norm

    return {
        "total_rework_cycles": total_rework,
        "total_batch_splits": total_splits,
        "total_pulls": total_pulls,
        "total_escalations": total_escalates,
        "critique_pass_count": pass_count,
        "critique_fail_count": fail_count,
        "critique_pass_rate": round(pass_count / (pass_count + fail_count), 2) if (pass_count + fail_count) > 0 else None,
        "avg_critique_score": round(
            sum(c["score"] for c in critiques) / len(critiques), 1
        ) if critiques else None,
        # Two-level performance metrics (GRD-6)
        "two_level": {
            "routing": routing.to_dict(),
            "quality": quality.to_dict(),
            "p_good_output": round(p_good, 3) if p_good is not None else None,
            "diagnosis": _diagnose_performance(routing, quality),
        },
    }


def _diagnose_performance(routing, quality) -> str:
    """Diagnose whether the problem is routing, quality, or both.
    From RAG Flywheel Ch. 5-6: the multiplication helps pinpoint the issue."""
    from grids.analysis.stream_parser import RoutingSummary, QualitySummary

    r_eff = routing.routing_efficiency
    avg_q = quality.avg_critique_score

    if avg_q is None:
        if r_eff < 0.5:
            return "ROUTING PROBLEM: Low delivery rate with no quality data. Work is not reaching target cells."
        return "INSUFFICIENT DATA: No critique scores available to assess quality."

    q_norm = min(1.0, max(0.0, avg_q / 100.0)) if avg_q > 1.0 else avg_q

    if r_eff >= 0.7 and q_norm >= 0.7:
        return "HEALTHY: Both routing and quality are strong."
    if r_eff < 0.5 and q_norm >= 0.7:
        return "ROUTING PROBLEM: Work is not reaching the right cells, but cells that do receive work produce quality output."
    if r_eff >= 0.7 and q_norm < 0.5:
        return "QUALITY PROBLEM: Work reaches cells successfully, but output quality is poor."
    if r_eff < 0.5 and q_norm < 0.5:
        return "DUAL PROBLEM: Both routing and quality are failing. Fix routing first, then quality."
    return f"MIXED: routing={r_eff:.0%}, quality={q_norm:.0%}. Both need improvement."


def _compute_domain_metrics(parsed: ParsedStream, workflow: WorkflowBreakdown) -> dict:
    """Quantitative domain grounding metrics."""
    by_domain = parsed.calls_by_domain()
    domain_stats = {}
    for domain, calls in by_domain.items():
        tokens = sum(c.tokens for c in calls)
        critiques = [c for c in calls if c.score is not None]
        domain_stats[domain] = {
            "agents_active": len(set(c.agent for c in calls)),
            "total_emissions": len(calls),
            "total_tokens": tokens,
            "critique_count": len(critiques),
            "avg_score": round(sum(c.score for c in critiques) / len(critiques), 1) if critiques else None,
            "scores": [c.score for c in critiques],
        }

    return {
        "domains_active": len(by_domain),
        "domain_stats": domain_stats,
        "total_tokens": parsed.total_tokens,
    }


def _compute_emergence_heuristic(parsed: ParsedStream) -> dict:
    """Heuristic emergence assessment."""
    # Look for agents that respond to other agents' outputs (not just the brief)
    response_chains = 0
    for call in parsed.llm_calls:
        if call.tick > 1 and call.action in ("process", "critique"):
            response_chains += 1

    return {
        "assessment": f"{response_chains} agent-to-agent response chains detected.",
        "emergence_examples": [],
        "emergence_score": min(response_chains / 20.0, 1.0),
    }


def _generate_heuristic_assessment(
    retro: Retrospective,
    parsed: ParsedStream,
    workflow: WorkflowBreakdown,
) -> None:
    """Generate what_worked/what_didnt/what_to_try without LLM."""
    # What worked
    if parsed.active_ticks >= 3:
        retro.what_worked.append(
            f"Grid sustained activity for {parsed.active_ticks} ticks with {len(parsed.llm_calls)} total emissions."
        )
    if len(parsed.domains) >= 3:
        retro.what_worked.append(
            f"Multi-domain analysis: {len(parsed.domains)} domains contributed perspectives."
        )
    critiques = parsed.critique_scores()
    if critiques:
        fails = [c for c in critiques if c["verdict"] in ("fail", "iterate")]
        if fails:
            retro.what_worked.append(
                f"Critique system engaged: {len(fails)} failures triggered rework signals."
            )

    # What didn't work
    ca_metrics = retro.ca_dynamics
    if ca_metrics.get("activity_concentration") == "front-loaded":
        retro.what_didnt.append(
            f"Activity was front-loaded: {ca_metrics.get('activity_first_half_llm', 0)} LLM calls "
            f"in first half vs {ca_metrics.get('activity_second_half_llm', 0)} in second half. "
            f"Grid effectively died after initial burst."
        )
    if ca_metrics.get("anti_quiescence_challenges", 0) == 0:
        retro.what_didnt.append(
            "Anti-quiescence (STALE/CHALLENGE) never fired despite long idle periods."
        )
    if parsed.quiescent_ticks > parsed.active_ticks * 2:
        retro.what_didnt.append(
            f"{parsed.quiescent_ticks} quiescent ticks vs {parsed.active_ticks} active -- "
            f"grid was mostly idle."
        )

    # Two-level metrics diagnosis
    flow_metrics = retro.flow_economics
    two_level = flow_metrics.get("two_level", {})
    diagnosis = two_level.get("diagnosis", "")
    if diagnosis:
        if "ROUTING PROBLEM" in diagnosis:
            retro.what_didnt.append(f"Two-level diagnosis: {diagnosis}")
        elif "QUALITY PROBLEM" in diagnosis:
            retro.what_didnt.append(f"Two-level diagnosis: {diagnosis}")
        elif "DUAL PROBLEM" in diagnosis:
            retro.what_didnt.append(f"Two-level diagnosis: {diagnosis}")

    routing_data = two_level.get("routing", {})
    quality_data = two_level.get("quality", {})
    r_eff = routing_data.get("routing_efficiency", 0)
    if r_eff < 0.7:
        retro.what_didnt.append(
            f"Routing efficiency only {r_eff:.0%} -- "
            f"{routing_data.get('items_rejected', 0)} propagations rejected out of "
            f"{routing_data.get('items_scheduled', 0)} scheduled."
        )

    # What to try
    retro.what_to_try.append(
        "Lower stale_threshold (currently 4) to 2-3 so anti-quiescence fires earlier."
    )
    if ca_metrics.get("activity_concentration") == "front-loaded":
        retro.what_to_try.append(
            "Investigate why work doesn't propagate past tick ~8. "
            "Possible cause: WIP limits rejecting propagations, or _should_receive() filtering too aggressively."
        )
    if r_eff < 0.7:
        retro.what_to_try.append(
            "Increase WIP limits or relax _should_receive() filtering to improve routing efficiency."
        )
    retro.what_to_try.append(
        "Run --evolve-rules to search for better rule tables that sustain activity longer."
    )

    retro.health_score = round(min(
        ca_metrics.get("activity_ratio", 0) * 1.5,
        1.0
    ), 2)
