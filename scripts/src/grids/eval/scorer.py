"""Grid run health scorer.

Reads grid-snapshot.json, tick-history.json, and run-result.json from a run
directory (or phase subdirectory) and produces a health scorecard with five
metrics:

  1. Cell utilization     -- % of cells that processed >= 1 item
  2. Propagation efficiency -- items emitted / items that could have been (via tick data)
  3. Critique coverage    -- did critique cells actually review artifacts? (binary + count)
  4. Quiescence legitimacy -- settled because done, or because stuck? (non-empty inboxes)
  5. Tick efficiency       -- ratio of active ticks (with LLM calls) to total ticks
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CellScore:
    position: str
    domain: str
    agent_type: str
    role: str
    items_processed: int
    llm_calls: int
    inbox_size: int
    state: str
    last_output_kind: str
    last_output_tick: int
    ticks_active: int


@dataclass
class PhaseScore:
    """Health scorecard for a single phase (or standalone run)."""
    phase_name: str
    path: str

    # Raw data
    total_cells: int = 0
    cells_that_processed: int = 0
    critique_cells_total: int = 0
    critique_cells_that_reviewed: int = 0
    cells_with_nonempty_inbox: int = 0

    total_ticks: int = 0
    active_ticks: int = 0  # ticks with at least 1 LLM call
    total_llm_calls: int = 0
    total_items_emitted: int = 0
    quiescent: bool = False
    elapsed_seconds: float = 0.0

    # Per-cell detail
    cell_scores: list[CellScore] = field(default_factory=list)
    idle_cells: list[CellScore] = field(default_factory=list)

    # Two-level metrics (GRD-6)
    routing_scheduled: int = 0
    routing_delivered: int = 0
    routing_rejected: int = 0
    routing_per_role: dict = field(default_factory=dict)
    quality_critique_scores: list[float] = field(default_factory=list)
    quality_critique_verdicts: dict = field(default_factory=dict)
    quality_rework_count: int = 0

    # Computed scores (0.0 - 1.0)
    @property
    def cell_utilization(self) -> float:
        if self.total_cells == 0:
            return 0.0
        return self.cells_that_processed / self.total_cells

    @property
    def critique_coverage(self) -> float:
        if self.critique_cells_total == 0:
            return 1.0  # no critique cells = N/A, treat as passing
        return self.critique_cells_that_reviewed / self.critique_cells_total

    @property
    def tick_efficiency(self) -> float:
        if self.total_ticks == 0:
            return 0.0
        return self.active_ticks / self.total_ticks

    @property
    def quiescence_legitimacy(self) -> float:
        if self.total_cells == 0:
            return 0.0
        if not self.quiescent:
            # Hit max ticks -- not quiescent. Score based on whether cells had work left.
            stuck_ratio = self.cells_with_nonempty_inbox / self.total_cells
            return max(0.0, 1.0 - stuck_ratio * 2)
        # Grid quiesced. Legitimate if no cells still have unprocessed inbox items.
        if self.cells_with_nonempty_inbox == 0:
            return 1.0
        return max(0.0, 1.0 - (self.cells_with_nonempty_inbox / self.total_cells))

    @property
    def propagation_efficiency(self) -> float:
        # Ratio of total items emitted to total LLM calls.
        # Each LLM call should ideally produce output. If many calls
        # produce nothing, propagation is inefficient.
        if self.total_llm_calls == 0:
            return 0.0
        return min(1.0, self.total_items_emitted / self.total_llm_calls)

    # Two-level metrics (GRD-6)
    @property
    def routing_efficiency(self) -> float:
        """P(work reaching right cell) -- delivery rate of propagations."""
        if self.routing_scheduled == 0:
            return 0.0
        return self.routing_delivered / self.routing_scheduled

    @property
    def avg_quality_score(self) -> float | None:
        """P(cell producing quality output) -- mean critique score."""
        if not self.quality_critique_scores:
            return None
        return sum(self.quality_critique_scores) / len(self.quality_critique_scores)

    @property
    def p_good_output(self) -> float | None:
        """P(good) = P(routing) x P(quality). The RAG Flywheel formula."""
        re = self.routing_efficiency
        qs = self.avg_quality_score
        if qs is None:
            return None
        # Normalize quality score to 0-1 range (assuming 0-100 scale)
        qs_norm = min(1.0, max(0.0, qs / 100.0)) if qs > 1.0 else qs
        return re * qs_norm

    @property
    def overall_health(self) -> float:
        weights = {
            "cell_utilization": 0.25,
            "tick_efficiency": 0.20,
            "critique_coverage": 0.15,
            "quiescence_legitimacy": 0.10,
            "propagation_efficiency": 0.10,
            "routing_efficiency": 0.10,
            "quality_score": 0.10,
        }
        quality_contrib = 0.0
        if self.avg_quality_score is not None:
            qs = self.avg_quality_score
            quality_contrib = (min(1.0, max(0.0, qs / 100.0)) if qs > 1.0 else qs) * weights["quality_score"]
        else:
            # Redistribute quality weight to other metrics
            weights["cell_utilization"] += weights["quality_score"] / 2
            weights["routing_efficiency"] += weights["quality_score"] / 2

        return (
            self.cell_utilization * weights["cell_utilization"]
            + self.tick_efficiency * weights["tick_efficiency"]
            + self.critique_coverage * weights["critique_coverage"]
            + self.quiescence_legitimacy * weights["quiescence_legitimacy"]
            + self.propagation_efficiency * weights["propagation_efficiency"]
            + self.routing_efficiency * weights["routing_efficiency"]
            + quality_contrib
        )

    @property
    def verdict(self) -> str:
        h = self.overall_health
        if h >= 0.75:
            return "HEALTHY"
        if h >= 0.50:
            return "DEGRADED"
        return "UNHEALTHY"

    def to_dict(self) -> dict:
        avg_q = self.avg_quality_score
        p_good = self.p_good_output
        return {
            "phase": self.phase_name,
            "path": self.path,
            "verdict": self.verdict,
            "overall_health": round(self.overall_health, 3),
            "scores": {
                "cell_utilization": round(self.cell_utilization, 3),
                "tick_efficiency": round(self.tick_efficiency, 3),
                "critique_coverage": round(self.critique_coverage, 3),
                "quiescence_legitimacy": round(self.quiescence_legitimacy, 3),
                "propagation_efficiency": round(self.propagation_efficiency, 3),
                "routing_efficiency": round(self.routing_efficiency, 3),
                "avg_quality_score": round(avg_q, 2) if avg_q is not None else None,
                "p_good_output": round(p_good, 3) if p_good is not None else None,
            },
            "raw": {
                "total_cells": self.total_cells,
                "cells_that_processed": self.cells_that_processed,
                "critique_cells_total": self.critique_cells_total,
                "critique_cells_that_reviewed": self.critique_cells_that_reviewed,
                "cells_with_nonempty_inbox": self.cells_with_nonempty_inbox,
                "total_ticks": self.total_ticks,
                "active_ticks": self.active_ticks,
                "total_llm_calls": self.total_llm_calls,
                "total_items_emitted": self.total_items_emitted,
                "quiescent": self.quiescent,
                "elapsed_seconds": self.elapsed_seconds,
            },
            "routing": {
                "items_scheduled": self.routing_scheduled,
                "items_delivered": self.routing_delivered,
                "items_rejected": self.routing_rejected,
                "routing_efficiency": round(self.routing_efficiency, 3),
                "per_role_breakdown": self.routing_per_role,
            },
            "quality": {
                "critique_scores": self.quality_critique_scores,
                "avg_critique_score": round(avg_q, 2) if avg_q is not None else None,
                "critique_verdicts": self.quality_critique_verdicts,
                "rework_count": self.quality_rework_count,
            },
            "idle_cells": [
                {"position": c.position, "domain": c.domain,
                 "agent_type": c.agent_type, "role": c.role}
                for c in self.idle_cells
            ],
        }


@dataclass
class RunScore:
    """Aggregate score across all phases in a run."""
    run_dir: str
    phases: list[PhaseScore] = field(default_factory=list)

    @property
    def overall_health(self) -> float:
        if not self.phases:
            return 0.0
        return sum(p.overall_health for p in self.phases) / len(self.phases)

    @property
    def verdict(self) -> str:
        h = self.overall_health
        if h >= 0.75:
            return "HEALTHY"
        if h >= 0.50:
            return "DEGRADED"
        return "UNHEALTHY"

    def to_dict(self) -> dict:
        return {
            "run_dir": self.run_dir,
            "verdict": self.verdict,
            "overall_health": round(self.overall_health, 3),
            "phases": [p.to_dict() for p in self.phases],
        }


def score_phase(phase_dir: str, phase_name: str = "") -> PhaseScore | None:
    """Score a single phase directory containing grid-snapshot.json, etc."""
    phase_path = Path(phase_dir)

    snapshot_path = phase_path / "grid-snapshot.json"
    tick_path = phase_path / "tick-history.json"
    result_path = phase_path / "run-result.json"

    if not snapshot_path.exists():
        return None

    with open(snapshot_path, "r", encoding="utf-8") as f:
        snapshot = json.load(f)

    tick_history = []
    if tick_path.exists():
        with open(tick_path, "r", encoding="utf-8") as f:
            tick_history = json.load(f)

    run_result = {}
    if result_path.exists():
        with open(result_path, "r", encoding="utf-8") as f:
            run_result = json.load(f)

    if not phase_name:
        phase_name = phase_path.name

    ps = PhaseScore(phase_name=phase_name, path=str(phase_path))

    # Parse cells from snapshot
    cells_data = snapshot.get("cells", {})
    ps.total_cells = len(cells_data)
    ps.quiescent = snapshot.get("quiescent", False)

    for pos_key, cell_data in cells_data.items():
        cs = CellScore(
            position=pos_key,
            domain=cell_data.get("domain", ""),
            agent_type=cell_data.get("agent_type", ""),
            role=cell_data.get("role", ""),
            items_processed=cell_data.get("items_processed", 0),
            llm_calls=cell_data.get("llm_calls", 0),
            inbox_size=cell_data.get("inbox_size", 0),
            state=cell_data.get("state", ""),
            last_output_kind=cell_data.get("last_output_kind", ""),
            last_output_tick=cell_data.get("last_output_tick", 0),
            ticks_active=cell_data.get("ticks_active", 0),
        )
        ps.cell_scores.append(cs)

        if cs.items_processed > 0:
            ps.cells_that_processed += 1
        else:
            ps.idle_cells.append(cs)

        if cs.role == "critique":
            ps.critique_cells_total += 1
            # A critique cell "reviewed" if it processed items AND produced critique output
            if cs.items_processed > 0 and cs.last_output_kind in ("critique", "output", "challenge"):
                ps.critique_cells_that_reviewed += 1

        if cs.inbox_size > 0:
            ps.cells_with_nonempty_inbox += 1

    # Parse tick history
    ps.total_ticks = len(tick_history)
    for t in tick_history:
        llm_calls = t.get("llm", 0)
        if llm_calls > 0:
            ps.active_ticks += 1

    # Parse run result
    ps.total_llm_calls = run_result.get("total_llm_calls", 0)
    ps.total_items_emitted = run_result.get("total_items_emitted", 0)
    ps.elapsed_seconds = run_result.get("elapsed_seconds", 0.0)

    # If run_result didn't have these, compute from tick history
    if ps.total_llm_calls == 0 and tick_history:
        ps.total_llm_calls = sum(t.get("llm", 0) for t in tick_history)
    if ps.total_items_emitted == 0 and tick_history:
        ps.total_items_emitted = sum(t.get("emitted", 0) for t in tick_history)
    if ps.total_ticks == 0:
        ps.total_ticks = run_result.get("total_ticks", 0)

    # Two-level metrics (GRD-6) -- from run-result.json if available
    routing_data = run_result.get("routing", {})
    if routing_data:
        ps.routing_scheduled = routing_data.get("items_scheduled", 0)
        ps.routing_delivered = routing_data.get("items_delivered", 0)
        ps.routing_rejected = routing_data.get("items_rejected", 0)
        ps.routing_per_role = routing_data.get("per_role_breakdown", {})

    quality_data = run_result.get("quality", {})
    if quality_data:
        ps.quality_critique_scores = quality_data.get("critique_scores", [])
        ps.quality_critique_verdicts = quality_data.get("critique_verdicts", {})
        ps.quality_rework_count = quality_data.get("rework_count", 0)

    # Retroactive computation from tick-history.json when run-result.json lacks metrics
    if not routing_data and tick_history:
        for t in tick_history:
            t_routing = t.get("routing", {})
            ps.routing_scheduled += t_routing.get("scheduled", 0)
            ps.routing_delivered += t_routing.get("delivered", 0)
            ps.routing_rejected += t_routing.get("rejected", 0)

            t_quality = t.get("quality", {})
            ps.quality_critique_scores.extend(t_quality.get("critique_scores", []))
            ps.quality_rework_count += t_quality.get("rework_count", 0)
            for v in t_quality.get("critique_verdicts", []):
                ps.quality_critique_verdicts[v] = ps.quality_critique_verdicts.get(v, 0) + 1

    return ps


def score_run(run_dir: str) -> RunScore:
    """Score a full run directory. Detects phase subdirectories automatically."""
    run_path = Path(run_dir)
    rs = RunScore(run_dir=str(run_path))

    # Check for phase subdirectories
    phase_dirs = sorted(run_path.glob("phase-*"))

    if phase_dirs:
        for pd in phase_dirs:
            ps = score_phase(str(pd), phase_name=pd.name)
            if ps is not None:
                rs.phases.append(ps)
    else:
        # Single-phase run (no subdirectories)
        ps = score_phase(str(run_path), phase_name="run")
        if ps is not None:
            rs.phases.append(ps)

    return rs
