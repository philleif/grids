"""Rule Space Search -- systematic enumeration of rule table variants.

NKS Ch. 2-6: The central method of A New Kind of Science is not to design
rules by intuition but to systematically enumerate and test rule sets,
then classify which produce useful behavior.

This module:
1. Defines the state/signal/action space for each role
2. Generates candidate rule tables by mutating baselines
3. Runs short simulations to score each candidate
4. Maintains a registry of tested configurations + scores
5. Evolves toward better-performing rules over runs

Usage:
    from grids.orchestration.rule_search import RuleSearchHarness
    harness = RuleSearchHarness()
    results = harness.search("master", brief="Build a design studio", n_candidates=20)
    best = results[0]  # highest-scoring rule table
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grids.orchestration.rules import (
    AgentState, Signal, Action, RuleTable, RuleEntry,
    master_rules, sub_agent_rules, critique_rules, research_rules,
    execution_rules, generate_rule_table,
)


# The full state/signal/action space
ALL_STATES = list(AgentState)
ALL_SIGNALS = list(Signal)
ALL_ACTIONS = list(Action)

# Constrained action sets per role (not every action makes sense for every role)
ROLE_ACTIONS = {
    "master": [Action.PROCESS, Action.EMIT, Action.CRITIQUE, Action.WAIT,
               Action.ESCALATE, Action.GAP_ANALYSIS],
    "sub": [Action.PROCESS, Action.EMIT, Action.CRITIQUE, Action.WAIT,
            Action.PULL, Action.SPLIT_BATCH, Action.GAP_ANALYSIS],
    "critique": [Action.CRITIQUE, Action.EMIT, Action.WAIT, Action.CHALLENGE],
    "research": [Action.PROCESS, Action.EMIT, Action.WAIT, Action.PULL,
                 Action.GAP_ANALYSIS],
    "execution": [Action.PROCESS, Action.EMIT, Action.WAIT, Action.PULL,
                  Action.PATCH, Action.ESCALATE, Action.GAP_ANALYSIS],
}

# Registry path for persisting tested configurations
REGISTRY_DIR = Path(__file__).resolve().parents[4] / "tmp" / "rule-registry"


@dataclass
class RuleCandidate:
    """A candidate rule table with its evaluation score."""
    rule_table: RuleTable
    role: str
    mutations: list[str] = field(default_factory=list)
    fingerprint: str = ""
    score: float = 0.0
    metrics: dict[str, Any] = field(default_factory=dict)
    generation: int = 0

    def __post_init__(self):
        if not self.fingerprint:
            self.fingerprint = _fingerprint(self.rule_table)


@dataclass
class SearchResult:
    """Results from a rule space search run."""
    role: str
    candidates_tested: int
    best: RuleCandidate | None = None
    all_results: list[RuleCandidate] = field(default_factory=list)
    baseline_score: float = 0.0
    elapsed_seconds: float = 0.0


class RuleSearchHarness:
    """Enumerate, test, and score rule table variants."""

    def __init__(self, registry_dir: Path | None = None):
        self.registry_dir = registry_dir or REGISTRY_DIR
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        self._tested: dict[str, RuleCandidate] = {}
        self._load_registry()

    def search(
        self,
        role: str,
        brief: str = "Build a design studio application",
        n_candidates: int = 20,
        sim_ticks: int = 8,
        mutations_per_candidate: int = 2,
        grid_size: tuple[int, int] = (4, 4),
    ) -> SearchResult:
        """Run a search over rule table variants for a given role.

        1. Start with the baseline rule table
        2. Generate n_candidates mutations
        3. Run short simulations for each
        4. Score and rank
        """
        t0 = time.time()

        # Score the baseline first
        baseline = generate_rule_table(role)
        baseline_score = self._evaluate(baseline, role, brief, sim_ticks, grid_size)
        baseline_candidate = RuleCandidate(
            rule_table=baseline, role=role,
            mutations=["baseline"], score=baseline_score,
            metrics={"source": "baseline"},
        )

        results = [baseline_candidate]

        # Generate and test mutations
        for i in range(n_candidates):
            candidate_rt = self._mutate(baseline, role, mutations_per_candidate)
            fp = _fingerprint(candidate_rt)

            # Skip already-tested configurations
            if fp in self._tested:
                existing = self._tested[fp]
                results.append(existing)
                continue

            score = self._evaluate(candidate_rt, role, brief, sim_ticks, grid_size)
            candidate = RuleCandidate(
                rule_table=candidate_rt, role=role,
                mutations=[f"mutation_{i}"], score=score,
                fingerprint=fp, generation=1,
            )
            results.append(candidate)
            self._tested[fp] = candidate

        # Sort by score descending
        results.sort(key=lambda c: c.score, reverse=True)

        elapsed = time.time() - t0
        self._save_registry()

        return SearchResult(
            role=role,
            candidates_tested=len(results),
            best=results[0] if results else None,
            all_results=results,
            baseline_score=baseline_score,
            elapsed_seconds=round(elapsed, 2),
        )

    def evolve(
        self,
        role: str,
        brief: str = "Build a design studio application",
        generations: int = 3,
        population: int = 10,
        top_k: int = 3,
        sim_ticks: int = 8,
        grid_size: tuple[int, int] = (4, 4),
    ) -> SearchResult:
        """Multi-generation evolutionary search.
        Each generation takes the top-K performers and mutates from them."""
        t0 = time.time()
        all_results: list[RuleCandidate] = []

        # Generation 0: initial random search
        gen0 = self.search(role, brief, n_candidates=population, sim_ticks=sim_ticks,
                           grid_size=grid_size)
        all_results.extend(gen0.all_results)

        parents = gen0.all_results[:top_k]

        for gen in range(1, generations):
            gen_results = []
            for parent in parents:
                for _ in range(population // top_k):
                    child_rt = self._mutate(parent.rule_table, role, mutations_per_candidate=1)
                    fp = _fingerprint(child_rt)
                    if fp in self._tested:
                        gen_results.append(self._tested[fp])
                        continue
                    score = self._evaluate(child_rt, role, brief, sim_ticks, grid_size)
                    child = RuleCandidate(
                        rule_table=child_rt, role=role,
                        mutations=parent.mutations + [f"gen{gen}"],
                        score=score, fingerprint=fp, generation=gen,
                    )
                    gen_results.append(child)
                    self._tested[fp] = child

            all_results.extend(gen_results)
            gen_results.sort(key=lambda c: c.score, reverse=True)
            parents = gen_results[:top_k]

        all_results.sort(key=lambda c: c.score, reverse=True)
        self._save_registry()

        return SearchResult(
            role=role,
            candidates_tested=len(all_results),
            best=all_results[0] if all_results else None,
            all_results=all_results,
            baseline_score=gen0.baseline_score,
            elapsed_seconds=round(time.time() - t0, 2),
        )

    def _mutate(self, base: RuleTable, role: str, mutations_per_candidate: int = 2) -> RuleTable:
        """Generate a mutated variant of a rule table.
        Mutations: change action, change next_state, add rule, remove rule."""
        rt = RuleTable(name=f"{base.name}-mutant", description=base.description)
        rt.rules = [copy.deepcopy(r) for r in base.rules]

        valid_actions = ROLE_ACTIONS.get(role, ALL_ACTIONS)

        for _ in range(mutations_per_candidate):
            mutation_type = random.choice(["change_action", "change_next_state", "add_rule", "remove_rule"])

            if mutation_type == "change_action" and rt.rules:
                rule = random.choice(rt.rules)
                rule.action = random.choice(valid_actions)

            elif mutation_type == "change_next_state" and rt.rules:
                rule = random.choice(rt.rules)
                rule.next_state = random.choice(ALL_STATES)

            elif mutation_type == "add_rule":
                state = random.choice(ALL_STATES)
                signal = random.choice(ALL_SIGNALS)
                # Don't duplicate existing (state, signal) pairs
                if not any(r.state == state and r.signal == signal for r in rt.rules):
                    action = random.choice(valid_actions)
                    next_state = random.choice(ALL_STATES)
                    rt.rules.append(RuleEntry(state, signal, action, next_state))

            elif mutation_type == "remove_rule" and len(rt.rules) > 3:
                # Don't remove too many rules -- keep at least 3
                idx = random.randint(0, len(rt.rules) - 1)
                rt.rules.pop(idx)

        return rt

    def _evaluate(
        self,
        rule_table: RuleTable,
        role: str,
        brief: str,
        sim_ticks: int,
        grid_size: tuple[int, int],
    ) -> float:
        """Score a rule table by running a fast simulation.
        Uses a mock invoke_fn (no LLM calls) to test grid dynamics."""
        from grids.orchestration.grid import AgentGrid, AgentCell, Neighborhood, WorkFragment

        w, h = grid_size
        grid = AgentGrid(width=w, height=h, neighborhood=Neighborhood.MOORE)

        # Place a minimal test grid: target cell + neighbors
        target_pos = (w // 2, h // 2)
        target_cell = AgentCell(
            position=target_pos,
            domain="test",
            agent_type=role,
            role=role,
            rule_table=rule_table,
            wip_limit=4,
        )
        grid.place(target_cell)

        # Place some neighbor cells with various roles
        neighbor_roles = ["sub", "master", "critique", "research"]
        for i, npos in enumerate(grid.neighbor_positions(target_pos)):
            if npos not in grid.cells:
                nrole = neighbor_roles[i % len(neighbor_roles)]
                ncell = AgentCell(
                    position=npos,
                    domain="test",
                    agent_type=nrole,
                    role=nrole,
                    rule_table=generate_rule_table(nrole),
                    wip_limit=3,
                )
                grid.place(ncell)

        # Inject work
        grid.inject(target_pos, WorkFragment(
            id="test-brief",
            kind="brief_chunk",
            content=brief,
            cost_of_delay=5.0,
            job_size=2.0,
        ))

        # Run with mock invoke_fn (no LLM)
        from grids.orchestration.tick import tick as run_tick

        metrics = {
            "items_emitted": 0,
            "actions_taken": 0,
            "transitions": 0,
            "ticks_to_quiescence": sim_ticks,
            "critique_coverage": 0,
            "stale_fires": 0,
        }

        def mock_invoke(cell, action, work, neighbors):
            if action in (Action.WAIT, Action.SKIP):
                return None
            if action == Action.CHALLENGE:
                return {"gaps": ["test gap"], "challenges": ["test challenge"]}
            if action == Action.GAP_ANALYSIS:
                return {"gaps": ["missing component"], "recommendations": ["add tests"]}
            if action == Action.CRITIQUE:
                return {"score": 0.8, "verdict": "approve", "feedback": "looks good"}
            return {"result": "mock output", "kind": work.kind if work else "test"}

        for t in range(sim_ticks):
            result = run_tick(grid, mock_invoke)
            metrics["items_emitted"] += result.items_emitted
            metrics["actions_taken"] += result.actions_taken

            if grid.is_quiescent() and not grid.has_pending_work():
                metrics["ticks_to_quiescence"] = t + 1
                break

        # Score: weighted combination of metrics
        # Higher is better
        score = (
            metrics["items_emitted"] * 3.0          # productivity
            + metrics["actions_taken"] * 1.0         # activity
            - metrics["ticks_to_quiescence"] * 0.5   # penalize fast quiescence
        )

        # Bonus: if quiescence happens late, the system explored more
        if metrics["ticks_to_quiescence"] >= sim_ticks * 0.8:
            score += 2.0  # reward Class 4-like sustained activity

        return round(score, 2)

    def _load_registry(self):
        """Load previously tested configurations from disk."""
        registry_file = self.registry_dir / "registry.json"
        if registry_file.exists():
            try:
                with open(registry_file, "r") as f:
                    data = json.load(f)
                for entry in data.get("tested", []):
                    fp = entry["fingerprint"]
                    rt = RuleTable(name=entry.get("name", ""), description=entry.get("description", ""))
                    for r in entry.get("rules", []):
                        rt.rules.append(RuleEntry(
                            state=AgentState(r["state"]),
                            signal=Signal(r["signal"]),
                            action=Action(r["action"]),
                            next_state=AgentState(r["next_state"]),
                        ))
                    self._tested[fp] = RuleCandidate(
                        rule_table=rt, role=entry.get("role", ""),
                        fingerprint=fp, score=entry.get("score", 0),
                        generation=entry.get("generation", 0),
                    )
            except Exception:
                pass

    def _save_registry(self):
        """Persist tested configurations to disk."""
        registry_file = self.registry_dir / "registry.json"
        data = {"tested": []}
        for fp, candidate in self._tested.items():
            entry = {
                "fingerprint": fp,
                "name": candidate.rule_table.name,
                "description": candidate.rule_table.description,
                "role": candidate.role,
                "score": candidate.score,
                "generation": candidate.generation,
                "rules": [
                    {
                        "state": r.state.value,
                        "signal": r.signal.value,
                        "action": r.action.value,
                        "next_state": r.next_state.value,
                    }
                    for r in candidate.rule_table.rules
                ],
            }
            data["tested"].append(entry)

        # Sort by score for easy inspection
        data["tested"].sort(key=lambda e: e["score"], reverse=True)

        with open(registry_file, "w") as f:
            json.dump(data, f, indent=2)

    def get_best(self, role: str) -> RuleTable | None:
        """Get the best-known rule table for a role from the registry."""
        best = None
        best_score = float("-inf")
        for candidate in self._tested.values():
            if candidate.role == role and candidate.score > best_score:
                best = candidate
                best_score = candidate.score
        return best.rule_table if best else None

    def report(self) -> dict:
        """Summary of the registry."""
        by_role: dict[str, list] = {}
        for c in self._tested.values():
            by_role.setdefault(c.role, []).append(c)

        summary = {}
        for role, candidates in by_role.items():
            candidates.sort(key=lambda c: c.score, reverse=True)
            summary[role] = {
                "tested": len(candidates),
                "best_score": candidates[0].score if candidates else 0,
                "worst_score": candidates[-1].score if candidates else 0,
                "avg_score": round(sum(c.score for c in candidates) / len(candidates), 2) if candidates else 0,
            }
        return summary


def _fingerprint(rt: RuleTable) -> str:
    """Create a deterministic hash of a rule table for deduplication."""
    rules_str = "|".join(
        f"{r.state.value},{r.signal.value},{r.action.value},{r.next_state.value}"
        for r in sorted(rt.rules, key=lambda r: (r.state.value, r.signal.value))
    )
    return hashlib.sha256(rules_str.encode()).hexdigest()[:16]
