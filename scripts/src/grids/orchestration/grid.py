"""Agent Grid -- cellular automaton lattice for emergent orchestration.

NKS principles implemented:
- Agents are cells in a 2D grid, not nodes in a DAG
- Each cell sees only its immediate neighbors (Von Neumann or Moore)
- State transitions governed by local rule tables
- Simultaneous update: all cells read, then all compute, then all write
- Initial conditions (seed) determine emergent behavior

Reinertsen principles implemented:
- Each cell has a local inbox (WIP-limited queue)
- Outputs propagate to neighbor inboxes on the NEXT tick (batch boundary)
- WSJF ordering within each cell's inbox
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from grids.orchestration.rules import AgentState, RuleTable, Signal, Action, RuleEntry


class Neighborhood(str, Enum):
    VON_NEUMANN = "von_neumann"  # 4 neighbors: N, S, E, W
    MOORE = "moore"              # 8 neighbors: N, NE, E, SE, S, SW, W, NW


@dataclass
class WorkFragment:
    """A local unit of work in a cell's inbox. Smaller than a WorkOrder --
    this is what propagates between cells."""
    id: str
    kind: str           # "brief_chunk", "research", "concept", "layout", "critique", "code", "artifact"
    content: Any        # the actual payload (text, dict, SVG, etc.)
    source_cell: tuple[int, int] | None = None
    cost_of_delay: float = 1.0
    job_size: float = 1.0
    iteration: int = 0
    created_at: float = field(default_factory=time.time)
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def wsjf(self) -> float:
        if self.job_size <= 0:
            return float("inf")
        return self.cost_of_delay / self.job_size


@dataclass
class CellOutput:
    """Snapshot of a cell's last emitted output, visible to neighbors."""
    content: Any = None
    kind: str = ""
    tick: int = 0
    state: AgentState = AgentState.IDLE


@dataclass
class AgentCell:
    """A single cell in the agent grid. Follows NKS: local state + local rules."""

    position: tuple[int, int]
    domain: str                    # "design", "editorial", etc.
    agent_type: str                # "typography", "print-design", "master", etc.
    role: str = "sub"              # "master" | "sub" | "critique" | "research" | "execution"
    state: AgentState = AgentState.IDLE
    rule_table: RuleTable | None = None

    # Local inbox -- WIP limited
    inbox: list[WorkFragment] = field(default_factory=list)
    wip_limit: int = 3

    # Last output (visible to neighbors)
    output: CellOutput = field(default_factory=CellOutput)

    # Knowledge collections this cell can query
    knowledge_collections: list[str] = field(default_factory=list)

    # Strictness (from domain YAML, affects critique threshold)
    strictness: float = 0.8

    # Execution gate: minimum distinct domain outputs visible before building
    min_domain_coverage: int = 0  # 0 = no gate, 2+ = wait for N domains

    # Project config (passed to execution agents for software output)
    project_config: dict[str, str] = field(default_factory=dict)

    # Class 4 perturbation: consecutive idle ticks with no work
    ticks_idle_consecutive: int = 0
    stale_threshold: int = 4  # fire STALE after this many consecutive idle ticks

    # Stuck-cell detection: consecutive ticks with inbox items but no rule fired
    ticks_with_unprocessed: int = 0
    stuck_threshold: int = 2  # warn after this many consecutive stuck ticks
    stuck_ticks: int = 0      # total stuck ticks (for eval scorecard)

    # Metrics
    ticks_active: int = 0
    items_processed: int = 0
    llm_calls: int = 0
    transitions: list[dict] = field(default_factory=list)

    @property
    def has_work(self) -> bool:
        return len(self.inbox) > 0

    @property
    def at_capacity(self) -> bool:
        return len(self.inbox) >= self.wip_limit

    def receive(self, fragment: WorkFragment) -> bool:
        """Accept a work fragment into inbox if capacity allows."""
        if self.at_capacity:
            return False
        self.inbox.append(fragment)
        self.inbox.sort(key=lambda f: f.wsjf, reverse=True)
        return True

    def peek_inbox(self) -> WorkFragment | None:
        """Look at highest-priority item without removing."""
        return self.inbox[0] if self.inbox else None

    def pop_inbox(self) -> WorkFragment | None:
        """Remove and return highest-priority item."""
        if not self.inbox:
            return None
        return self.inbox.pop(0)

    def detect_signal(self, neighbor_states: list[CellOutput]) -> Signal:
        """Determine current signal from local information only.
        NKS: cell sees only its own inbox and neighbors' states.

        Uses totalistic rules (NKS Ch. 2): signal detection based on
        aggregate neighbor state counts, not individual cell checks.
        """
        # --- Totalistic neighbor counts (NKS Ch. 2) ---
        n_working = 0
        n_critiquing = 0
        n_idle = 0
        n_waiting = 0
        n_active_output = 0
        for n in neighbor_states:
            if n.state == AgentState.WORKING:
                n_working += 1
            elif n.state == AgentState.CRITIQUING:
                n_critiquing += 1
            elif n.state == AgentState.IDLE:
                n_idle += 1
            elif n.state == AgentState.WAITING:
                n_waiting += 1
            if n.content is not None and n.kind:
                n_active_output += 1

        if self.inbox:
            # Execution gate: check domain coverage before allowing build.
            # Counts distinct source domains from BOTH neighbor outputs AND
            # inbox items (which may have arrived via long-range broadcast).
            if self.min_domain_coverage > 0 and self.role == "execution":
                distinct_domains = set()
                # Check neighbor outputs
                for n in neighbor_states:
                    if n.content is not None and n.kind:
                        if isinstance(n.content, dict):
                            d = n.content.get("domain", "")
                            if d:
                                distinct_domains.add(d)
                        if n.kind in ("work_spec", "research", "critique", "concept"):
                            distinct_domains.add(n.kind)
                # Check inbox items for broadcast sources
                for item in self.inbox:
                    from_domain = item.tags.get("from_domain", "")
                    if from_domain:
                        distinct_domains.add(from_domain)
                if len(distinct_domains) < self.min_domain_coverage:
                    return Signal.INSUFFICIENT_COVERAGE

            # Totalistic: critique cells activate when 3+ neighbors are working
            if self.role == "critique" and n_working >= 3:
                return Signal.CRITIQUE_NEEDED

            # Totalistic: execution cells pause when 2+ neighbors critiquing
            # (too much critique in flight -- wait for stability)
            if self.role == "execution" and n_critiquing >= 2:
                return Signal.INSUFFICIENT_COVERAGE

            # Totalistic: share work when multiple neighbors idle
            if n_idle >= 2 and len(self.inbox) > 1:
                return Signal.NEIGHBOR_IDLE

            # Single idle neighbor can still absorb overflow
            if n_idle >= 1 and len(self.inbox) > 1:
                return Signal.NEIGHBOR_IDLE

            # Check if inbox item needs critique
            top = self.inbox[0]
            if top.kind in ("layout", "concept", "code", "artifact") and self.role == "critique":
                return Signal.CRITIQUE_NEEDED

            # Patch signal: execution cell with existing output receives enrichment
            if (self.role == "execution" and self.output.content is not None
                    and top.kind in ("critique", "enrichment", "rework")):
                return Signal.ITERATION_DONE

            if top.iteration > 0:
                return Signal.ITERATION_DONE

            return Signal.NEW_ITEM

        # --- Empty inbox totalistic signals ---

        # Class 4 anti-quiescence: STALE detection
        # Cell has been idle too long while neighbors are actively producing.
        # This prevents premature quiescence -- the key Class 4 behavior.
        if (self.state == AgentState.IDLE
                and self.ticks_idle_consecutive >= self.stale_threshold
                and (n_working >= 2 or n_active_output >= 2)):
            self.ticks_idle_consecutive = 0  # reset after firing
            return Signal.STALE

        # Totalistic: idle cell with many active neighbors should try to help
        if (self.state == AgentState.IDLE
                and self.ticks_active > 0
                and n_working >= 3):
            return Signal.NEIGHBOR_IDLE

        if self.at_capacity:
            return Signal.QUEUE_FULL

        return Signal.QUEUE_EMPTY

    def apply_rule(self, signal: Signal) -> RuleEntry | None:
        """Look up and apply a rule transition. Returns the rule or None."""
        if self.rule_table is None:
            return None
        rule = self.rule_table.lookup(self.state, signal)
        if rule is None:
            return None

        old_state = self.state
        self.state = rule.next_state
        self.transitions.append({
            "tick": self.ticks_active,
            "from": old_state.value,
            "signal": signal.value,
            "action": rule.action.value,
            "to": rule.next_state.value,
        })
        return rule

    def emit(self, content: Any, kind: str, tick: int):
        """Set output visible to neighbors."""
        self.output = CellOutput(
            content=content,
            kind=kind,
            tick=tick,
            state=self.state,
        )
        self.items_processed += 1

    def metrics(self) -> dict:
        return {
            "position": self.position,
            "domain": self.domain,
            "agent_type": self.agent_type,
            "role": self.role,
            "state": self.state.value,
            "inbox_size": len(self.inbox),
            "wip_limit": self.wip_limit,
            "ticks_active": self.ticks_active,
            "items_processed": self.items_processed,
            "llm_calls": self.llm_calls,
            "last_output_kind": self.output.kind,
            "last_output_tick": self.output.tick,
            "stuck_ticks": self.stuck_ticks,
            "ticks_with_unprocessed": self.ticks_with_unprocessed,
            "ticks_idle_consecutive": self.ticks_idle_consecutive,
        }


class AgentGrid:
    """2D grid of AgentCells. The lattice topology for emergent orchestration.

    NKS: the grid IS the computation. Topology determines what emerges."""

    def __init__(
        self,
        width: int,
        height: int,
        neighborhood: Neighborhood = Neighborhood.VON_NEUMANN,
    ):
        self.width = width
        self.height = height
        self.neighborhood = neighborhood
        self.cells: dict[tuple[int, int], AgentCell] = {}
        self.tick_count: int = 0
        self._pending_propagations: list[tuple[tuple[int, int], WorkFragment]] = []

    def place(self, cell: AgentCell):
        """Place a cell at its position on the grid."""
        if not (0 <= cell.position[0] < self.width and 0 <= cell.position[1] < self.height):
            raise ValueError(f"Position {cell.position} out of bounds for {self.width}x{self.height} grid")
        self.cells[cell.position] = cell

    def get(self, pos: tuple[int, int]) -> AgentCell | None:
        return self.cells.get(pos)

    def neighbor_positions(self, pos: tuple[int, int]) -> list[tuple[int, int]]:
        """Get valid neighbor positions for a cell. Wrapping optional (toroidal grid)."""
        x, y = pos
        if self.neighborhood == Neighborhood.VON_NEUMANN:
            offsets = [(0, -1), (0, 1), (-1, 0), (1, 0)]
        else:  # Moore
            offsets = [
                (-1, -1), (0, -1), (1, -1),
                (-1, 0),           (1, 0),
                (-1, 1),  (0, 1),  (1, 1),
            ]
        neighbors = []
        for dx, dy in offsets:
            nx, ny = x + dx, y + dy
            if 0 <= nx < self.width and 0 <= ny < self.height:
                if (nx, ny) in self.cells:
                    neighbors.append((nx, ny))
        return neighbors

    def neighbors(self, pos: tuple[int, int]) -> list[AgentCell]:
        """Get actual neighbor cells."""
        return [self.cells[p] for p in self.neighbor_positions(pos)]

    def neighbor_outputs(self, pos: tuple[int, int]) -> list[CellOutput]:
        """Get neighbor cells' last outputs. This is what a cell can SEE."""
        return [self.cells[p].output for p in self.neighbor_positions(pos)]

    def schedule_propagation(self, target_pos: tuple[int, int], fragment: WorkFragment):
        """Queue a work fragment to be delivered to a cell on the NEXT tick.
        NKS: outputs propagate after all cells have computed."""
        self._pending_propagations.append((target_pos, fragment))

    def flush_propagations(self):
        """Deliver all pending work fragments to their target cells.
        Called at the end of a tick."""
        delivered = 0
        rejected = 0
        for target_pos, fragment in self._pending_propagations:
            cell = self.cells.get(target_pos)
            if cell and cell.receive(fragment):
                delivered += 1
            else:
                rejected += 1
        self._pending_propagations.clear()
        return delivered, rejected

    def flush_propagations_detailed(self):
        """Like flush_propagations but also returns per-propagation routing records.
        Used by tick() to build two-level performance metrics (GRD-6)."""
        from grids.orchestration.tick import PropagationRecord

        delivered = 0
        rejected = 0
        records: list[PropagationRecord] = []

        for target_pos, fragment in self._pending_propagations:
            cell = self.cells.get(target_pos)
            source_cell = self.cells.get(fragment.source_cell) if fragment.source_cell else None
            accepted = cell is not None and cell.receive(fragment)

            if accepted:
                delivered += 1
            else:
                rejected += 1

            records.append(PropagationRecord(
                source_role=source_cell.role if source_cell else "unknown",
                target_role=cell.role if cell else "unknown",
                kind=fragment.kind,
                accepted=accepted,
                source_domain=source_cell.domain if source_cell else "",
                target_domain=cell.domain if cell else "",
                broadcast=fragment.tags.get("broadcast") == "true",
            ))

        self._pending_propagations.clear()
        return delivered, rejected, records

    def all_cells(self) -> list[AgentCell]:
        """All cells in row-major order."""
        positions = sorted(self.cells.keys(), key=lambda p: (p[1], p[0]))
        return [self.cells[p] for p in positions]

    def cells_by_domain(self, domain: str) -> list[AgentCell]:
        return [c for c in self.cells.values() if c.domain == domain]

    def cells_by_role(self, role: str) -> list[AgentCell]:
        return [c for c in self.cells.values() if c.role == role]

    def cells_by_state(self, state: AgentState) -> list[AgentCell]:
        return [c for c in self.cells.values() if c.state == state]

    def is_quiescent(self) -> bool:
        """Check if all cells are idle with empty inboxes. Grid has settled."""
        return all(
            c.state == AgentState.IDLE and not c.has_work
            for c in self.cells.values()
        )

    def has_pending_work(self) -> bool:
        """Check if any cell has work in inbox or pending propagations."""
        return (
            any(c.has_work for c in self.cells.values())
            or len(self._pending_propagations) > 0
        )

    def inject(self, pos: tuple[int, int], fragment: WorkFragment) -> bool:
        """Inject a work fragment directly into a cell (for initial conditions)."""
        cell = self.cells.get(pos)
        if cell is None:
            return False
        return cell.receive(fragment)

    def inject_broadcast(self, fragment: WorkFragment, role: str | None = None, domain: str | None = None):
        """Broadcast a work fragment to all matching cells."""
        for cell in self.cells.values():
            if role and cell.role != role:
                continue
            if domain and cell.domain != domain:
                continue
            cell.receive(WorkFragment(
                id=f"{fragment.id}-{cell.position[0]}-{cell.position[1]}",
                kind=fragment.kind,
                content=fragment.content,
                source_cell=fragment.source_cell,
                cost_of_delay=fragment.cost_of_delay,
                job_size=fragment.job_size,
                iteration=fragment.iteration,
                tags=dict(fragment.tags),
            ))

    def snapshot(self) -> dict:
        """Full grid state snapshot for observation/debugging."""
        return {
            "tick": self.tick_count,
            "width": self.width,
            "height": self.height,
            "neighborhood": self.neighborhood.value,
            "total_cells": len(self.cells),
            "quiescent": self.is_quiescent(),
            "pending_propagations": len(self._pending_propagations),
            "cells": {
                f"{x},{y}": cell.metrics()
                for (x, y), cell in sorted(self.cells.items())
            },
            "state_distribution": {
                state.value: len(self.cells_by_state(state))
                for state in AgentState
            },
        }

    def ascii_view(self) -> str:
        """Simple ASCII visualization of the grid state."""
        state_chars = {
            AgentState.IDLE: ".",
            AgentState.WORKING: "W",
            AgentState.WAITING: "~",
            AgentState.CRITIQUING: "C",
            AgentState.BLOCKED: "X",
        }
        lines = []
        for y in range(self.height):
            row = []
            for x in range(self.width):
                cell = self.cells.get((x, y))
                if cell is None:
                    row.append(" ")
                else:
                    ch = state_chars.get(cell.state, "?")
                    if cell.has_work:
                        ch = ch.lower() if ch != "." else str(min(len(cell.inbox), 9))
                    row.append(ch)
            lines.append("".join(row))
        return "\n".join(lines)
