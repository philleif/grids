"""Wolfram-inspired rule system -- simple local rules that produce emergent behavior.

Each agent operates on a rule table: (current_state, input_signal) -> (action, next_state).
Rules are local (agents only see their own queue and immediate neighbors), deterministic
given the same inputs, and composable (swap rule sets to explore different creative behaviors).

Inspired by NKS: complex output emerges from many simple agent interactions, not from
one god-agent with a massive system prompt.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentState(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    WAITING = "waiting"
    CRITIQUING = "critiquing"
    BLOCKED = "blocked"


class Signal(str, Enum):
    NEW_ITEM = "new_item"
    QUEUE_FULL = "queue_full"
    QUEUE_EMPTY = "queue_empty"
    CRITIQUE_NEEDED = "critique_needed"
    ITERATION_DONE = "iteration_done"
    BATCH_COMPLETE = "batch_complete"
    NEIGHBOR_IDLE = "neighbor_idle"
    DEADLINE_NEAR = "deadline_near"
    INSUFFICIENT_COVERAGE = "insufficient_coverage"
    STALE = "stale"


class Action(str, Enum):
    PROCESS = "process"
    EMIT = "emit"
    CRITIQUE = "critique"
    WAIT = "wait"
    PULL = "pull"
    SPLIT_BATCH = "split_batch"
    ESCALATE = "escalate"
    SKIP = "skip"
    PATCH = "patch"
    CHALLENGE = "challenge"
    GAP_ANALYSIS = "gap_analysis"


@dataclass
class RuleEntry:
    state: AgentState
    signal: Signal
    action: Action
    next_state: AgentState
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RuleTable:
    """A named set of rules for an agent type."""
    name: str
    description: str
    rules: list[RuleEntry] = field(default_factory=list)

    def add(self, state: AgentState, signal: Signal, action: Action, next_state: AgentState, **meta):
        self.rules.append(RuleEntry(state, signal, action, next_state, meta))

    def lookup(self, state: AgentState, signal: Signal) -> RuleEntry | None:
        for rule in self.rules:
            if rule.state == state and rule.signal == signal:
                return rule
        return None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "rules": [
                {
                    "state": r.state.value,
                    "signal": r.signal.value,
                    "action": r.action.value,
                    "next_state": r.next_state.value,
                    "metadata": r.metadata,
                }
                for r in self.rules
            ],
        }


# --- Built-in rule tables ---

def research_rules() -> RuleTable:
    """Rules for the Research agent -- gathers references and context."""
    rt = RuleTable("research", "Gather references, search knowledge base, find visual/textual sources")
    rt.add(AgentState.IDLE, Signal.NEW_ITEM, Action.PROCESS, AgentState.WORKING)
    rt.add(AgentState.WORKING, Signal.BATCH_COMPLETE, Action.EMIT, AgentState.IDLE)
    rt.add(AgentState.WORKING, Signal.QUEUE_FULL, Action.EMIT, AgentState.WAITING)
    rt.add(AgentState.WAITING, Signal.NEIGHBOR_IDLE, Action.EMIT, AgentState.IDLE)
    rt.add(AgentState.IDLE, Signal.QUEUE_EMPTY, Action.WAIT, AgentState.IDLE)
    rt.add(AgentState.WORKING, Signal.DEADLINE_NEAR, Action.EMIT, AgentState.IDLE, note="ship what you have")
    rt.add(AgentState.IDLE, Signal.STALE, Action.GAP_ANALYSIS, AgentState.WORKING,
           note="anti-quiescence: find gaps while neighbors are active")
    return rt


def concept_rules() -> RuleTable:
    """Rules for the Concept agent -- develops ideas from research into structured concepts."""
    rt = RuleTable("concept", "Transform research into structured creative concepts")
    rt.add(AgentState.IDLE, Signal.NEW_ITEM, Action.PROCESS, AgentState.WORKING)
    rt.add(AgentState.WORKING, Signal.BATCH_COMPLETE, Action.EMIT, AgentState.IDLE)
    rt.add(AgentState.WORKING, Signal.CRITIQUE_NEEDED, Action.CRITIQUE, AgentState.CRITIQUING)
    rt.add(AgentState.CRITIQUING, Signal.ITERATION_DONE, Action.EMIT, AgentState.IDLE)
    rt.add(AgentState.IDLE, Signal.QUEUE_EMPTY, Action.PULL, AgentState.IDLE, note="pull from research queue")
    rt.add(AgentState.IDLE, Signal.STALE, Action.GAP_ANALYSIS, AgentState.WORKING,
           note="anti-quiescence: find conceptual gaps")
    return rt


def layout_rules() -> RuleTable:
    """Rules for the Layout agent -- arranges content on grid-based pages."""
    rt = RuleTable("layout", "Place content blocks on grid, manage visual rhythm")
    rt.add(AgentState.IDLE, Signal.NEW_ITEM, Action.PROCESS, AgentState.WORKING)
    rt.add(AgentState.WORKING, Signal.BATCH_COMPLETE, Action.EMIT, AgentState.IDLE)
    rt.add(AgentState.WORKING, Signal.CRITIQUE_NEEDED, Action.CRITIQUE, AgentState.CRITIQUING)
    rt.add(AgentState.CRITIQUING, Signal.ITERATION_DONE, Action.PROCESS, AgentState.WORKING, note="revise layout")
    rt.add(AgentState.IDLE, Signal.QUEUE_EMPTY, Action.WAIT, AgentState.IDLE)
    # Rhythm rule: if previous spread was text-heavy, introduce a visual break
    rt.add(AgentState.WORKING, Signal.NEIGHBOR_IDLE, Action.SPLIT_BATCH, AgentState.WORKING,
           note="break monotony -- if neighbor is idle, consider visual variety")
    rt.add(AgentState.IDLE, Signal.STALE, Action.GAP_ANALYSIS, AgentState.WORKING,
           note="anti-quiescence: look for layout gaps")
    return rt


def critique_rules() -> RuleTable:
    """Rules for the Critique agent -- reviews output quality and coherence."""
    rt = RuleTable("critique", "Evaluate quality, coherence, and alignment with brief")
    rt.add(AgentState.IDLE, Signal.NEW_ITEM, Action.CRITIQUE, AgentState.CRITIQUING)
    rt.add(AgentState.CRITIQUING, Signal.BATCH_COMPLETE, Action.EMIT, AgentState.IDLE)
    rt.add(AgentState.CRITIQUING, Signal.DEADLINE_NEAR, Action.EMIT, AgentState.IDLE, note="good enough")
    rt.add(AgentState.IDLE, Signal.QUEUE_EMPTY, Action.WAIT, AgentState.IDLE)
    rt.add(AgentState.IDLE, Signal.STALE, Action.CHALLENGE, AgentState.CRITIQUING,
           note="anti-quiescence: challenge neighbors when idle too long")
    return rt


ALL_RULE_TABLES = {
    "research": research_rules,
    "concept": concept_rules,
    "layout": layout_rules,
    "critique": critique_rules,
}


# --- Auto-generation from domain YAML ---

def master_rules() -> RuleTable:
    """Rules for a domain master agent -- specifies work, aggregates, vetoes."""
    rt = RuleTable("master", "Domain master: decompose briefs, validate, veto")
    rt.add(AgentState.IDLE, Signal.NEW_ITEM, Action.PROCESS, AgentState.WORKING,
           note="decompose brief into work fragments")
    rt.add(AgentState.WORKING, Signal.BATCH_COMPLETE, Action.EMIT, AgentState.IDLE)
    rt.add(AgentState.WORKING, Signal.CRITIQUE_NEEDED, Action.CRITIQUE, AgentState.CRITIQUING,
           note="validate incoming artifact from sub-agents")
    rt.add(AgentState.CRITIQUING, Signal.ITERATION_DONE, Action.EMIT, AgentState.IDLE)
    rt.add(AgentState.CRITIQUING, Signal.BATCH_COMPLETE, Action.EMIT, AgentState.IDLE)
    rt.add(AgentState.IDLE, Signal.QUEUE_EMPTY, Action.WAIT, AgentState.IDLE)
    rt.add(AgentState.IDLE, Signal.NEIGHBOR_IDLE, Action.WAIT, AgentState.IDLE)
    rt.add(AgentState.WORKING, Signal.DEADLINE_NEAR, Action.EMIT, AgentState.IDLE,
           note="ship best available")
    rt.add(AgentState.IDLE, Signal.STALE, Action.GAP_ANALYSIS, AgentState.WORKING,
           note="anti-quiescence: re-examine brief for missed angles")
    return rt


def sub_agent_rules(strictness: float = 0.8) -> RuleTable:
    """Rules for a domain sub-agent. Strictness affects critique threshold.
    High strictness -> more likely to trigger CRITIQUE on neighbor output."""
    rt = RuleTable("sub_agent", f"Domain sub-agent (strictness={strictness})")
    rt.add(AgentState.IDLE, Signal.NEW_ITEM, Action.PROCESS, AgentState.WORKING)
    rt.add(AgentState.WORKING, Signal.BATCH_COMPLETE, Action.EMIT, AgentState.IDLE)
    # High-strictness agents critique more aggressively
    if strictness >= 0.85:
        rt.add(AgentState.IDLE, Signal.NEIGHBOR_IDLE, Action.CRITIQUE, AgentState.CRITIQUING,
               note="proactively review neighbor output (high strictness)")
        rt.add(AgentState.WORKING, Signal.CRITIQUE_NEEDED, Action.CRITIQUE, AgentState.CRITIQUING)
    else:
        rt.add(AgentState.WORKING, Signal.CRITIQUE_NEEDED, Action.CRITIQUE, AgentState.CRITIQUING)
        rt.add(AgentState.IDLE, Signal.NEIGHBOR_IDLE, Action.PULL, AgentState.IDLE,
               note="pull work from neighbor if available")
    rt.add(AgentState.CRITIQUING, Signal.ITERATION_DONE, Action.EMIT, AgentState.IDLE)
    rt.add(AgentState.CRITIQUING, Signal.BATCH_COMPLETE, Action.EMIT, AgentState.IDLE)
    rt.add(AgentState.IDLE, Signal.QUEUE_EMPTY, Action.WAIT, AgentState.IDLE)
    rt.add(AgentState.WORKING, Signal.NEIGHBOR_IDLE, Action.SPLIT_BATCH, AgentState.WORKING,
           note="share work with idle neighbor")
    rt.add(AgentState.WORKING, Signal.DEADLINE_NEAR, Action.EMIT, AgentState.IDLE)
    rt.add(AgentState.BLOCKED, Signal.NEW_ITEM, Action.PROCESS, AgentState.WORKING,
           note="unblock on new input")
    rt.add(AgentState.IDLE, Signal.STALE, Action.GAP_ANALYSIS, AgentState.WORKING,
           note="anti-quiescence: look for gaps in domain coverage")
    return rt


def execution_rules() -> RuleTable:
    """Rules for execution agents (coder, tester, runner)."""
    rt = RuleTable("execution", "Build and test artifacts from specifications")
    rt.add(AgentState.IDLE, Signal.NEW_ITEM, Action.PROCESS, AgentState.WORKING)
    rt.add(AgentState.WORKING, Signal.BATCH_COMPLETE, Action.EMIT, AgentState.IDLE)
    rt.add(AgentState.WORKING, Signal.CRITIQUE_NEEDED, Action.ESCALATE, AgentState.WAITING,
           note="send to critique neighbor")
    rt.add(AgentState.WAITING, Signal.ITERATION_DONE, Action.PROCESS, AgentState.WORKING,
           note="revise based on critique feedback")
    rt.add(AgentState.IDLE, Signal.QUEUE_EMPTY, Action.PULL, AgentState.IDLE)
    rt.add(AgentState.WORKING, Signal.NEIGHBOR_IDLE, Action.SPLIT_BATCH, AgentState.WORKING)
    rt.add(AgentState.WORKING, Signal.DEADLINE_NEAR, Action.EMIT, AgentState.IDLE)
    rt.add(AgentState.IDLE, Signal.STALE, Action.PULL, AgentState.IDLE,
           note="anti-quiescence: pull work from busy neighbors")
    return rt


def generate_rule_table(role: str, strictness: float = 0.8) -> RuleTable:
    """Generate a rule table for any agent role.
    NKS: each cell type has its own simple rule set."""
    if role == "master":
        return master_rules()
    elif role == "critique":
        return critique_rules()
    elif role == "research":
        return research_rules()
    elif role == "execution":
        return execution_rules()
    else:
        return sub_agent_rules(strictness)


ALL_RULE_TABLES["master"] = master_rules
ALL_RULE_TABLES["sub_agent"] = sub_agent_rules
ALL_RULE_TABLES["execution"] = execution_rules
