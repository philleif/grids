"""Agent wrapper -- binds Wolfram rule tables to actual LLM agent execution.

Each agent is a state machine. Before invoking the LLM, the wrapper:
1. Reads the current signal from queue/environment state
2. Looks up (current_state, signal) in the rule table
3. Executes the prescribed action (or skips if no matching rule)
4. Transitions to the next state

This makes agent behavior swappable by changing rule sets, not prompts.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from rich.console import Console

from grids.orchestration.rules import (
    Action,
    AgentState,
    RuleEntry,
    RuleTable,
    Signal,
    ALL_RULE_TABLES,
)
from grids.orchestration.flow import FlowController, FlowQueue, WorkItem

console = Console(stderr=True)


@dataclass
class AgentContext:
    """Runtime context for a rule-driven agent."""
    name: str
    state: AgentState = AgentState.IDLE
    rule_table: RuleTable | None = None
    history: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=lambda: {
        "transitions": 0,
        "llm_calls": 0,
        "items_processed": 0,
        "time_working": 0.0,
    })

    def transition(self, signal: Signal) -> RuleEntry | None:
        """Look up and apply a rule transition."""
        if self.rule_table is None:
            return None
        rule = self.rule_table.lookup(self.state, signal)
        if rule is None:
            return None

        old_state = self.state
        self.state = rule.next_state
        self.metrics["transitions"] += 1
        self.history.append({
            "timestamp": time.time(),
            "from_state": old_state.value,
            "signal": signal.value,
            "action": rule.action.value,
            "to_state": rule.next_state.value,
            "metadata": rule.metadata,
        })
        return rule


def detect_signal(
    agent_name: str,
    flow: FlowController | None,
    has_new_item: bool = False,
    batch_done: bool = False,
    critique_needed: bool = False,
) -> Signal:
    """Detect the current signal from environment state."""
    if has_new_item:
        return Signal.NEW_ITEM
    if batch_done:
        return Signal.BATCH_COMPLETE
    if critique_needed:
        return Signal.CRITIQUE_NEEDED

    if flow is not None:
        queue = flow.queues.get(agent_name)
        if queue is not None:
            if queue.is_empty:
                return Signal.QUEUE_EMPTY
            if not queue.has_capacity:
                return Signal.QUEUE_FULL

        # Check if any neighbor queue's agent is idle
        queue_names = list(flow.queues.keys())
        idx = queue_names.index(agent_name) if agent_name in queue_names else -1
        if idx >= 0:
            for neighbor_idx in [idx - 1, idx + 1]:
                if 0 <= neighbor_idx < len(queue_names):
                    neighbor = flow.queues[queue_names[neighbor_idx]]
                    if neighbor.wip == 0 and not neighbor.is_empty:
                        return Signal.NEIGHBOR_IDLE

    return Signal.NEW_ITEM


def should_invoke_llm(action: Action) -> bool:
    """Determine if the prescribed action requires an LLM call."""
    return action in (Action.PROCESS, Action.CRITIQUE, Action.EMIT)


def should_skip(action: Action) -> bool:
    """Actions that mean 'do nothing this cycle'."""
    return action in (Action.WAIT, Action.SKIP)


class RuleDrivenAgent:
    """Wraps an LLM agent with Wolfram rule-based state management."""

    def __init__(
        self,
        name: str,
        invoke_fn: Callable[[str, str, str], str],
        flow: FlowController | None = None,
    ):
        self.name = name
        self.invoke_fn = invoke_fn
        self.flow = flow

        rule_factory = ALL_RULE_TABLES.get(name)
        rule_table = rule_factory() if rule_factory else None
        self.ctx = AgentContext(name=name, rule_table=rule_table)

    def step(
        self,
        input_text: str,
        brief: str = "",
        signal_override: Signal | None = None,
        **kwargs,
    ) -> tuple[str | None, Action]:
        """Execute one step of the agent's state machine.

        Returns (output, action_taken).
        Output is None if the agent decided to wait/skip.
        """
        signal = signal_override or detect_signal(
            self.name,
            self.flow,
            has_new_item=bool(input_text),
        )

        rule = self.ctx.transition(signal)

        if rule is None:
            return None, Action.WAIT

        if should_skip(rule.action):
            return None, rule.action

        if rule.action == Action.SPLIT_BATCH:
            return None, Action.SPLIT_BATCH

        if rule.action == Action.ESCALATE:
            return None, Action.ESCALATE

        if should_invoke_llm(rule.action):
            start = time.time()
            self.ctx.metrics["llm_calls"] += 1
            output = self.invoke_fn(self.name, input_text, brief)
            self.ctx.metrics["time_working"] += time.time() - start
            self.ctx.metrics["items_processed"] += 1
            return output, rule.action

        if rule.action == Action.PULL:
            return None, Action.PULL

        return None, rule.action

    @property
    def state(self) -> AgentState:
        return self.ctx.state

    @property
    def is_idle(self) -> bool:
        return self.ctx.state == AgentState.IDLE

    @property
    def metrics(self) -> dict:
        return self.ctx.metrics

    @property
    def history(self) -> list[dict]:
        return self.ctx.history
