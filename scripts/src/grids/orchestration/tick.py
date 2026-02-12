"""Tick scheduler -- the CA execution engine.

NKS core mechanism: all cells read state simultaneously, all compute
simultaneously, all write simultaneously, outputs propagate on the NEXT tick.

Reinertsen: each tick is a cadence boundary. WSJF ordering within inboxes.
Iteration economics (1.2x CoD, 0.7x size) applied when work loops back.

This module does NOT know about LLMs. It orchestrates the grid; actual
execution is delegated to an invoke_fn callback.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from rich.console import Console

from grids.orchestration.grid import AgentGrid, AgentCell, CellOutput, WorkFragment
from grids.orchestration.rules import AgentState, Signal, Action, RuleEntry

console = Console(stderr=True)


@dataclass
class PropagationRecord:
    """Record of a single propagation attempt for routing analysis."""
    source_role: str
    target_role: str
    kind: str
    accepted: bool
    source_domain: str = ""
    target_domain: str = ""
    broadcast: bool = False


@dataclass
class TickResult:
    """Result of a single tick across the grid."""
    tick: int
    actions_taken: int
    llm_calls: int
    items_emitted: int
    propagations: int
    rejected: int
    elapsed_seconds: float
    stuck_cells: int = 0
    cell_actions: list[dict] = field(default_factory=list)
    # Two-level metrics (GRD-6)
    routing_records: list[PropagationRecord] = field(default_factory=list)
    critique_scores: list[float] = field(default_factory=list)
    critique_verdicts: list[str] = field(default_factory=list)
    rework_count: int = 0


@dataclass
class RoutingMetrics:
    """Metric 1: Routing success -- did work reach the right cells?"""
    items_scheduled: int = 0
    items_delivered: int = 0
    items_rejected: int = 0

    @property
    def routing_efficiency(self) -> float:
        if self.items_scheduled == 0:
            return 0.0
        return self.items_delivered / self.items_scheduled

    def per_role_breakdown(self, records: list[PropagationRecord]) -> dict[str, dict]:
        """What % of items reached each role type."""
        by_target: dict[str, dict] = {}
        for r in records:
            entry = by_target.setdefault(r.target_role, {"scheduled": 0, "delivered": 0, "rejected": 0})
            entry["scheduled"] += 1
            if r.accepted:
                entry["delivered"] += 1
            else:
                entry["rejected"] += 1
        for entry in by_target.values():
            entry["efficiency"] = round(entry["delivered"] / entry["scheduled"], 3) if entry["scheduled"] else 0.0
        return by_target

    def to_dict(self, records: list[PropagationRecord] | None = None) -> dict:
        d = {
            "items_scheduled": self.items_scheduled,
            "items_delivered": self.items_delivered,
            "items_rejected": self.items_rejected,
            "routing_efficiency": round(self.routing_efficiency, 3),
        }
        if records:
            d["per_role_breakdown"] = self.per_role_breakdown(records)
        return d


@dataclass
class QualityMetrics:
    """Metric 2: Cell output quality -- did cells produce good output?"""
    critique_scores: list[float] = field(default_factory=list)
    critique_verdicts: list[str] = field(default_factory=list)
    rework_count: int = 0

    @property
    def avg_critique_score(self) -> float | None:
        if not self.critique_scores:
            return None
        return sum(self.critique_scores) / len(self.critique_scores)

    @property
    def verdict_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for v in self.critique_verdicts:
            counts[v] = counts.get(v, 0) + 1
        return counts

    def to_dict(self) -> dict:
        avg = self.avg_critique_score
        return {
            "critique_scores": self.critique_scores,
            "avg_critique_score": round(avg, 2) if avg is not None else None,
            "critique_verdicts": self.verdict_counts,
            "rework_count": self.rework_count,
        }


@dataclass
class RunResult:
    """Result of a full grid run (many ticks)."""
    total_ticks: int
    total_llm_calls: int
    total_items_emitted: int
    artifacts: list[dict] = field(default_factory=list)
    tick_history: list[TickResult] = field(default_factory=list)
    quiescent: bool = False
    elapsed_seconds: float = 0.0
    # Two-level metrics (GRD-6)
    routing: RoutingMetrics = field(default_factory=RoutingMetrics)
    quality: QualityMetrics = field(default_factory=QualityMetrics)
    all_routing_records: list[PropagationRecord] = field(default_factory=list)


# Type for the LLM invocation callback
# (cell: AgentCell, action: Action, work: WorkFragment | None, neighbors: list[CellOutput]) -> Any
InvokeFn = Callable[[AgentCell, Action, WorkFragment | None, list[CellOutput]], Any]


def tick(grid: AgentGrid, invoke_fn: InvokeFn) -> TickResult:
    """Execute one tick of the cellular automaton.

    Phase 1 (READ):    All cells snapshot neighbor states and detect signals
    Phase 2 (COMPUTE): All cells apply rule tables to determine actions
    Phase 3 (EXECUTE): All cells execute their action (may invoke LLM)
    Phase 4 (PROPAGATE): All outputs delivered to neighbor inboxes
    """
    t0 = time.time()
    grid.tick_count += 1
    tick_num = grid.tick_count

    cells = grid.all_cells()
    actions_taken = 0
    llm_calls = 0
    items_emitted = 0
    stuck_cells = 0
    cell_actions = []

    # Phase 1: READ -- snapshot all neighbor states
    cell_signals: dict[tuple[int, int], Signal] = {}
    cell_neighbor_outputs: dict[tuple[int, int], list[CellOutput]] = {}

    for cell in cells:
        neighbor_outs = grid.neighbor_outputs(cell.position)
        cell_neighbor_outputs[cell.position] = neighbor_outs
        signal = cell.detect_signal(neighbor_outs)
        cell_signals[cell.position] = signal

    # Phase 2: COMPUTE -- all cells apply rules
    cell_rules: dict[tuple[int, int], RuleEntry | None] = {}
    for cell in cells:
        signal = cell_signals[cell.position]
        rule = cell.apply_rule(signal)
        cell_rules[cell.position] = rule

        # Stuck-cell detection: inbox has items but no rule matched.
        # Exclude execution cells waiting on domain coverage (legitimate wait).
        legitimately_waiting = (
            cell.role == "execution"
            and signal == Signal.INSUFFICIENT_COVERAGE
        )
        if cell.has_work and rule is None and not legitimately_waiting:
            cell.ticks_with_unprocessed += 1
            if cell.ticks_with_unprocessed >= cell.stuck_threshold:
                cell.stuck_ticks += 1
                stuck_cells += 1
                console.print(
                    f"  [yellow]STUCK: {cell.domain}/{cell.agent_type} at {cell.position} "
                    f"has {len(cell.inbox)} items but no matching rule for "
                    f"{cell.state.value}+{signal.value}[/yellow]"
                )
        else:
            cell.ticks_with_unprocessed = 0

    # Phase 3: EXECUTE -- all cells act
    for cell in cells:
        cell.ticks_active += 1

        # Class 4: track consecutive idle ticks for STALE detection
        if cell.state == AgentState.IDLE and not cell.has_work:
            cell.ticks_idle_consecutive += 1
        else:
            cell.ticks_idle_consecutive = 0

        rule = cell_rules[cell.position]

        if rule is None:
            continue

        action = rule.action

        if action in (Action.WAIT, Action.SKIP):
            cell_actions.append({
                "pos": cell.position, "action": action.value, "skipped": True,
            })
            continue

        actions_taken += 1
        work = cell.peek_inbox()

        if action == Action.SPLIT_BATCH and len(cell.inbox) > 1:
            # Share work with an idle neighbor
            _split_to_neighbor(cell, grid, tick_num)
            cell_actions.append({
                "pos": cell.position, "action": "split_batch",
            })
            continue

        if action == Action.PULL:
            # Try to pull work from a busy neighbor
            _pull_from_neighbor(cell, grid)
            cell_actions.append({
                "pos": cell.position, "action": "pull",
            })
            continue

        if action == Action.ESCALATE:
            # Send current work to a critique/master neighbor
            if work:
                _escalate_to_neighbor(cell, grid, tick_num)
            cell_actions.append({
                "pos": cell.position, "action": "escalate",
            })
            continue

        if action == Action.PATCH:
            # Patch existing artifact with late-arriving enrichment
            if work is None:
                continue
            consumed = cell.pop_inbox()
            neighbors = cell_neighbor_outputs[cell.position]
            cell.llm_calls += 1
            llm_calls += 1
            result = invoke_fn(cell, action, consumed, neighbors)
            if result is not None:
                cell.emit(result, "artifact", tick_num)
                items_emitted += 1
                _propagate_output(cell, grid, result, "artifact", tick_num)
            cell_actions.append({
                "pos": cell.position, "action": "patch",
                "domain": cell.domain, "agent": cell.agent_type,
                "consumed": consumed.kind if consumed else None,
                "emitted": "artifact" if result else None,
            })
            continue

        if action == Action.CHALLENGE:
            # Class 4 perturbation: critique cell injects a "what's missing?" challenge
            neighbors = cell_neighbor_outputs[cell.position]
            cell.llm_calls += 1
            llm_calls += 1
            result = invoke_fn(cell, action, None, neighbors)
            if result is not None:
                cell.emit(result, "challenge", tick_num)
                items_emitted += 1
                _propagate_output(cell, grid, result, "challenge", tick_num)
            cell_actions.append({
                "pos": cell.position, "action": "challenge",
                "domain": cell.domain, "agent": cell.agent_type,
                "emitted": "challenge" if result else None,
            })
            continue

        if action == Action.GAP_ANALYSIS:
            # Class 4 perturbation: analyze gaps between brief and produced artifacts
            neighbors = cell_neighbor_outputs[cell.position]
            cell.llm_calls += 1
            llm_calls += 1
            result = invoke_fn(cell, action, None, neighbors)
            if result is not None:
                kind = "enrichment" if cell.role == "research" else "work_spec"
                cell.emit(result, kind, tick_num)
                items_emitted += 1
                _propagate_output(cell, grid, result, kind, tick_num)
            cell_actions.append({
                "pos": cell.position, "action": "gap_analysis",
                "domain": cell.domain, "agent": cell.agent_type,
                "emitted": kind if result else None,
            })
            continue

        if action in (Action.PROCESS, Action.CRITIQUE, Action.EMIT):
            if work is None and action != Action.EMIT:
                continue

            # EMIT with empty inbox: just transition state, no LLM call needed.
            # The cell already has output from its previous PROCESS action.
            if action == Action.EMIT and work is None:
                cell_actions.append({
                    "pos": cell.position,
                    "action": action.value,
                    "domain": cell.domain,
                    "agent": cell.agent_type,
                    "consumed": None,
                    "emitted": None,
                })
                continue

            consumed = cell.pop_inbox() if work else None
            neighbors = cell_neighbor_outputs[cell.position]

            # Invoke the actual execution (LLM call or local computation)
            cell.llm_calls += 1
            llm_calls += 1
            result = invoke_fn(cell, action, consumed, neighbors)

            if result is not None:
                # Emit output
                kind = _output_kind(cell, action, consumed)
                cell.emit(result, kind, tick_num)
                items_emitted += 1

                # Propagate to neighbors' inboxes
                _propagate_output(cell, grid, result, kind, tick_num)

            cell_actions.append({
                "pos": cell.position,
                "action": action.value,
                "domain": cell.domain,
                "agent": cell.agent_type,
                "consumed": consumed.kind if consumed else None,
                "emitted": kind if result else None,
            })

    # Phase 4: PROPAGATE -- deliver all pending fragments
    delivered, rejected, routing_records = grid.flush_propagations_detailed()

    # Collect critique scores and rework counts from this tick's actions
    tick_critique_scores: list[float] = []
    tick_critique_verdicts: list[str] = []
    tick_rework_count = 0
    for cell in cells:
        if cell.output.tick == tick_num and cell.output.kind == "critique":
            content = cell.output.content
            if isinstance(content, dict):
                score = content.get("score")
                verdict = content.get("verdict")
                if isinstance(score, (int, float)):
                    tick_critique_scores.append(float(score))
                if isinstance(verdict, str) and verdict:
                    tick_critique_verdicts.append(verdict)
                if verdict in ("fail", "iterate"):
                    tick_rework_count += 1

    elapsed = time.time() - t0
    return TickResult(
        tick=tick_num,
        actions_taken=actions_taken,
        llm_calls=llm_calls,
        items_emitted=items_emitted,
        propagations=delivered,
        rejected=rejected,
        elapsed_seconds=round(elapsed, 3),
        stuck_cells=stuck_cells,
        cell_actions=cell_actions,
        routing_records=routing_records,
        critique_scores=tick_critique_scores,
        critique_verdicts=tick_critique_verdicts,
        rework_count=tick_rework_count,
    )


def run(
    grid: AgentGrid,
    invoke_fn: InvokeFn,
    max_ticks: int = 50,
    quiescence_ticks: int = 3,
    verbose: bool = True,
    on_tick: Callable[[TickResult], None] | None = None,
) -> RunResult:
    """Run the grid until quiescence or max ticks.

    Quiescence = all cells idle with empty inboxes for `quiescence_ticks` consecutive ticks.
    """
    t0 = time.time()
    total_llm = 0
    total_emitted = 0
    artifacts: list[dict] = []
    tick_history: list[TickResult] = []
    idle_streak = 0

    # Two-level metrics accumulators (GRD-6)
    routing = RoutingMetrics()
    quality = QualityMetrics()
    all_routing_records: list[PropagationRecord] = []

    for i in range(max_ticks):
        result = tick(grid, invoke_fn)
        tick_history.append(result)
        total_llm += result.llm_calls
        total_emitted += result.items_emitted

        # Accumulate two-level metrics
        routing.items_scheduled += result.propagations + result.rejected
        routing.items_delivered += result.propagations
        routing.items_rejected += result.rejected
        all_routing_records.extend(result.routing_records)

        quality.critique_scores.extend(result.critique_scores)
        quality.critique_verdicts.extend(result.critique_verdicts)
        quality.rework_count += result.rework_count

        if verbose:
            _print_tick(result, grid)

        if on_tick:
            on_tick(result)

        # Collect artifacts from master/execution cells that emitted approved work.
        # Use position as key so later ticks overwrite earlier ones from the
        # same cell (e.g., PATCH updates), while preserving artifacts from
        # different cells (e.g., coder + runner both contributing files).
        for cell in grid.all_cells():
            if cell.output.tick == grid.tick_count and cell.output.kind in ("artifact", "approved"):
                entry = {
                    "source": f"{cell.domain}/{cell.agent_type}",
                    "position": cell.position,
                    "tick": cell.output.tick,
                    "content": cell.output.content,
                    "kind": cell.output.kind,
                }
                # Replace any earlier artifact from the same cell position
                replaced = False
                for idx, existing in enumerate(artifacts):
                    if existing["position"] == cell.position:
                        artifacts[idx] = entry
                        replaced = True
                        break
                if not replaced:
                    artifacts.append(entry)

        # Check quiescence
        if grid.is_quiescent() and not grid.has_pending_work():
            idle_streak += 1
            if idle_streak >= quiescence_ticks:
                if verbose:
                    console.print(f"\n[green]Grid quiescent after {i + 1} ticks.[/green]")
                break
        else:
            idle_streak = 0

    return RunResult(
        total_ticks=grid.tick_count,
        total_llm_calls=total_llm,
        total_items_emitted=total_emitted,
        artifacts=artifacts,
        tick_history=tick_history,
        quiescent=grid.is_quiescent(),
        elapsed_seconds=round(time.time() - t0, 2),
        routing=routing,
        quality=quality,
        all_routing_records=all_routing_records,
    )


# --- Internal helpers ---

def _output_kind(cell: AgentCell, action: Action, consumed: WorkFragment | None) -> str:
    """Determine the kind of output based on cell role and action."""
    if action == Action.CRITIQUE:
        return "critique"
    if cell.role == "master" and action == Action.PROCESS:
        return "work_spec"
    if cell.role == "execution":
        return "artifact"
    if cell.role == "research":
        return "research"
    # Consultant reviewing an artifact emits enrichment (triggers long-range broadcast
    # back to execution cells and PATCH action on arrival)
    if cell.agent_type == "consultant" and consumed and consumed.kind in ("artifact", "code"):
        return "enrichment"
    if consumed:
        return consumed.kind
    return "output"


def _propagate_output(
    cell: AgentCell,
    grid: AgentGrid,
    content: Any,
    kind: str,
    tick_num: int,
):
    """Propagate a cell's output to its neighbors' inboxes for the NEXT tick.
    Critique FAILs trigger rework propagation back to the source cell.

    Long-range broadcast: work_specs from masters and enrichment from research
    are also sent directly to execution cells, bypassing hop-by-hop propagation.
    This ensures execution cells receive domain knowledge even when physically
    distant on the grid.
    """
    neighbor_positions = grid.neighbor_positions(cell.position)
    fragment = WorkFragment(
        id=f"t{tick_num}-{cell.position[0]},{cell.position[1]}-{kind}",
        kind=kind,
        content=content,
        source_cell=cell.position,
        cost_of_delay=1.0,
        job_size=1.0,
    )

    # Track which positions we've already scheduled to avoid duplicates
    scheduled_positions = set()

    for npos in neighbor_positions:
        neighbor = grid.get(npos)
        if neighbor is None:
            continue
        if neighbor.position == cell.position:
            continue
        if _should_receive(neighbor, kind, cell):
            grid.schedule_propagation(npos, WorkFragment(
                id=f"{fragment.id}->{npos[0]},{npos[1]}",
                kind=fragment.kind,
                content=fragment.content,
                source_cell=cell.position,
                cost_of_delay=fragment.cost_of_delay,
                job_size=fragment.job_size,
                tags={"from_domain": cell.domain, "from_agent": cell.agent_type},
            ))
            scheduled_positions.add(npos)

    # Long-range broadcast: masters and research send work_specs/enrichment
    # directly to ALL execution cells, regardless of grid distance.
    # This solves the "execution starved" problem where domain output
    # can't reach execution cells through hop-by-hop propagation alone.
    if kind in ("work_spec", "enrichment", "research", "concept") and cell.role in ("master", "research", "sub"):
        for exec_cell in grid.cells_by_role("execution"):
            if exec_cell.position in scheduled_positions:
                continue
            if exec_cell.position == cell.position:
                continue
            grid.schedule_propagation(exec_cell.position, WorkFragment(
                id=f"{fragment.id}->exec-{exec_cell.position[0]},{exec_cell.position[1]}",
                kind=fragment.kind,
                content=fragment.content,
                source_cell=cell.position,
                cost_of_delay=fragment.cost_of_delay,
                job_size=fragment.job_size,
                tags={"from_domain": cell.domain, "from_agent": cell.agent_type, "broadcast": "true"},
            ))

    # Long-range broadcast: execution artifacts are sent directly to consultant
    # cells for domain-specific review. Consultant enrichment flows back to
    # execution cells via the existing enrichment broadcast path.
    if kind in ("artifact", "code") and cell.role == "execution":
        for consultant in grid.cells_by_role("sub"):
            if consultant.agent_type != "consultant":
                continue
            if consultant.position in scheduled_positions:
                continue
            if consultant.position == cell.position:
                continue
            grid.schedule_propagation(consultant.position, WorkFragment(
                id=f"{fragment.id}->consult-{consultant.position[0]},{consultant.position[1]}",
                kind=fragment.kind,
                content=fragment.content,
                source_cell=cell.position,
                cost_of_delay=fragment.cost_of_delay,
                job_size=fragment.job_size,
                tags={"from_domain": cell.domain, "from_agent": cell.agent_type, "review_requested": "true"},
            ))

    # Rework loop: critique FAIL/iterate forces feedback to original source
    if kind == "critique" and isinstance(content, dict):
        verdict = content.get("verdict", "")
        score = content.get("score", 100)
        if verdict in ("fail", "iterate") or (isinstance(score, (int, float)) and score < 75):
            _propagate_rework(cell, grid, content, tick_num)


def _should_receive(neighbor: AgentCell, kind: str, source: AgentCell) -> bool:
    """Determine if a neighbor should receive a particular kind of output.
    This implements the local information barrier (Package 4)."""
    # Masters receive critiques, artifacts, rework, and challenges for validation
    if neighbor.role == "master" and kind in ("critique", "artifact", "code", "rework", "challenge"):
        return True
    # Critique cells receive artifacts, layouts, work specs, and general output for review
    if neighbor.role == "critique" and kind in ("artifact", "layout", "concept", "code", "work_spec", "output"):
        return True
    # Sub-agents receive work specs, research, and challenges
    # Consultant sub-agents also receive artifacts for domain-specific review
    if neighbor.role == "sub" and kind in ("work_spec", "research", "brief_chunk", "challenge"):
        return True
    if neighbor.role == "sub" and neighbor.agent_type == "consultant" and kind in ("artifact", "code"):
        return True
    # Research cells receive briefs, work specs, and challenges
    if neighbor.role == "research" and kind in ("brief_chunk", "work_spec", "challenge"):
        return True
    # Execution cells receive concepts, layouts, iteration feedback, rework, enrichment,
    # research, challenges, and broadcast work_specs from distant masters
    if neighbor.role == "execution" and kind in ("concept", "layout", "work_spec", "critique", "rework", "enrichment", "research", "challenge"):
        return True
    # Same domain sub-agents share freely
    if neighbor.domain == source.domain and kind in ("concept", "layout", "research", "enrichment"):
        return True
    return False


def _propagate_rework(
    cell: AgentCell,
    grid: AgentGrid,
    critique_content: dict,
    tick_num: int,
):
    """Route critique feedback back to source cells for rework.
    Reinertsen iteration economics: cost_of_delay * 1.2, job_size * 0.7."""
    # Find the source of the critiqued work by walking neighbor outputs
    feedback = critique_content.get("feedback", "")
    source_positions = []

    # Check neighbors for master/execution cells in the same or related domain
    for npos in grid.neighbor_positions(cell.position):
        neighbor = grid.get(npos)
        if neighbor and neighbor.role in ("master", "execution"):
            source_positions.append(npos)

    if not source_positions:
        return

    rework_fragment = WorkFragment(
        id=f"rework-t{tick_num}-{cell.position[0]},{cell.position[1]}",
        kind="rework",
        content={
            "critique_from": f"{cell.domain}/{cell.agent_type}",
            "score": critique_content.get("score"),
            "verdict": critique_content.get("verdict"),
            "feedback": feedback,
        },
        source_cell=cell.position,
        cost_of_delay=1.2,  # Reinertsen: iterated work has higher urgency
        job_size=0.7,       # Reinertsen: revisions are smaller than originals
        iteration=1,
        tags={"from_domain": cell.domain, "rework": "true"},
    )

    for target_pos in source_positions:
        grid.schedule_propagation(target_pos, WorkFragment(
            id=f"{rework_fragment.id}->{target_pos[0]},{target_pos[1]}",
            kind=rework_fragment.kind,
            content=rework_fragment.content,
            source_cell=cell.position,
            cost_of_delay=rework_fragment.cost_of_delay,
            job_size=rework_fragment.job_size,
            iteration=rework_fragment.iteration,
            tags=dict(rework_fragment.tags),
        ))


def _split_to_neighbor(cell: AgentCell, grid: AgentGrid, tick_num: int):
    """Split work from an overloaded cell to an idle neighbor."""
    if len(cell.inbox) <= 1:
        return
    neighbors = grid.neighbors(cell.position)
    idle = [n for n in neighbors if n.state == AgentState.IDLE and not n.at_capacity]
    if not idle:
        return
    # Give lowest-priority item to the first idle neighbor
    item = cell.inbox.pop()  # lowest WSJF (sorted descending)
    target = idle[0]
    grid.schedule_propagation(target.position, item)


def _pull_from_neighbor(cell: AgentCell, grid: AgentGrid):
    """Pull work from a busy neighbor."""
    neighbors = grid.neighbors(cell.position)
    busy = [n for n in neighbors if n.has_work and len(n.inbox) > 1]
    if not busy:
        return
    # Pull from the most overloaded neighbor
    busy.sort(key=lambda n: len(n.inbox), reverse=True)
    donor = busy[0]
    item = donor.inbox.pop()  # take lowest priority
    cell.receive(item)


def _escalate_to_neighbor(cell: AgentCell, grid: AgentGrid, tick_num: int):
    """Send current work to a critique or master neighbor."""
    if not cell.inbox:
        return
    work = cell.pop_inbox()
    neighbors = grid.neighbors(cell.position)
    targets = [n for n in neighbors if n.role in ("critique", "master") and not n.at_capacity]
    if targets:
        # Bump iteration economics
        escalated = WorkFragment(
            id=f"{work.id}-escalate-t{tick_num}",
            kind=work.kind,
            content=work.content,
            source_cell=cell.position,
            cost_of_delay=work.cost_of_delay * 1.2,
            job_size=work.job_size * 0.7,
            iteration=work.iteration + 1,
            tags={**work.tags, "escalated_from": f"{cell.domain}/{cell.agent_type}"},
        )
        grid.schedule_propagation(targets[0].position, escalated)


def _print_tick(result: TickResult, grid: AgentGrid):
    """Print a compact tick summary."""
    active = [a for a in result.cell_actions if not a.get("skipped")]
    stuck_str = f" [yellow]stuck={result.stuck_cells}[/yellow]" if result.stuck_cells else ""
    console.print(
        f"  [dim]tick {result.tick:3d}[/dim] "
        f"actions={result.actions_taken} "
        f"llm={result.llm_calls} "
        f"emitted={result.items_emitted} "
        f"propagated={result.propagations}"
        f"{stuck_str} "
        f"({result.elapsed_seconds:.1f}s)"
    )
    if active:
        for a in active[:5]:
            pos = a.get("pos", "?")
            act = a.get("action", "?")
            domain = a.get("domain", "")
            agent = a.get("agent", "")
            console.print(f"    [{domain}/{agent} @ {pos}] {act}")
        if len(active) > 5:
            console.print(f"    ... +{len(active) - 5} more")
