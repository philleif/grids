"""LangGraph state graph -- the orchestration topology.

Defines the state machine:
  Brief -> Research -> Concept -> Layout -> Critique -> (iterate or done)

Each node is a rule-driven agent that:
1. Checks its Wolfram rule table for the appropriate action
2. Pulls relevant knowledge from ChromaDB
3. Produces output for the next agent in the pipeline

The flow controller manages WIP limits and WSJF prioritization across nodes.
"""

import json
from typing import Annotated, TypedDict

from langgraph.graph import StateGraph, END

from grids.orchestration.agents import invoke_agent
from grids.orchestration.agent_wrapper import RuleDrivenAgent, detect_signal
from grids.orchestration.flow import FlowController, Priority
from grids.orchestration.rules import Signal, Action

# Module-level flow controller and rule-driven agents, initialized by compile_graph()
_flow: FlowController | None = None
_agents: dict[str, RuleDrivenAgent] = {}


class CreativeState(TypedDict):
    brief: str
    research: str
    concepts: str
    layout: str
    critique: str
    iteration: int
    max_iterations: int
    status: str  # "running", "approved", "max_iterations"
    history: list[dict]
    flow_metrics: dict


def _log_step(state: CreativeState, agent: str, result: str, action: Action) -> list[dict]:
    history = list(state.get("history", []))
    agent_ctx = _agents.get(agent)
    history.append({
        "agent": agent,
        "iteration": state["iteration"],
        "action": action.value,
        "state": agent_ctx.state.value if agent_ctx else "unknown",
        "output_preview": result[:200] if result else "",
    })
    return history


def research_node(state: CreativeState) -> dict:
    """Research agent: brief -> research findings."""
    agent = _agents.get("research")
    input_text = f"Research the following creative brief and find relevant patterns, references, and source material:\n\n{state['brief']}"

    if agent:
        result, action = agent.step(input_text, brief=state["brief"], signal_override=Signal.NEW_ITEM)
        if result is None:
            result = ""
    else:
        result = invoke_agent("research", input_text=input_text, brief=state["brief"])
        action = Action.PROCESS

    if _flow and "research" in _flow.queues:
        item = _flow.submit("research", "research_result", {"brief": state["brief"]}, priority=Priority.HIGH, cost_of_delay=5.0)
        _flow.queues["research"]._in_progress.append(item)

    return {"research": result, "history": _log_step(state, "research", result, action)}


def concept_node(state: CreativeState) -> dict:
    """Concept agent: research -> structured concepts."""
    feedback_note = ""
    if state.get("critique") and state["iteration"] > 0:
        feedback_note = f"\n\nPrevious critique feedback (iteration {state['iteration']}):\n{state['critique']}"

    input_text = f"Transform these research findings into structured creative concepts:{feedback_note}\n\nResearch findings:\n{state['research']}"

    agent = _agents.get("concept")
    signal = Signal.NEW_ITEM if state["iteration"] == 0 else Signal.ITERATION_DONE

    if agent:
        result, action = agent.step(input_text, brief=state["brief"], signal_override=signal)
        if result is None:
            result = ""
    else:
        result = invoke_agent("concept", input_text=input_text, brief=state["brief"])
        action = Action.PROCESS

    return {"concepts": result, "history": _log_step(state, "concept", result, action)}


def layout_node(state: CreativeState) -> dict:
    """Layout agent: concepts -> grid-based page layouts."""
    feedback_note = ""
    if state.get("critique") and state["iteration"] > 0:
        feedback_note = f"\n\nPrevious critique feedback (iteration {state['iteration']}):\n{state['critique']}"

    input_text = f"Arrange these concepts into grid-based page layouts:{feedback_note}\n\nConcepts:\n{state['concepts']}"

    agent = _agents.get("layout")
    if agent:
        result, action = agent.step(input_text, brief=state["brief"], signal_override=Signal.NEW_ITEM)
        if result is None:
            result = ""
    else:
        result = invoke_agent("layout", input_text=input_text, brief=state["brief"])
        action = Action.PROCESS

    return {"layout": result, "history": _log_step(state, "layout", result, action)}


def critique_node(state: CreativeState) -> dict:
    """Critique agent: layout -> verdict (approve/iterate)."""
    input_text = f"Evaluate this layout against the original brief.\n\nBrief: {state['brief']}\n\nLayout:\n{state['layout']}"

    agent = _agents.get("critique")
    if agent:
        result, action = agent.step(input_text, brief=state["brief"], signal_override=Signal.NEW_ITEM)
        if result is None:
            result = ""
    else:
        result = invoke_agent("critique", input_text=input_text, brief=state["brief"])
        action = Action.CRITIQUE

    return {"critique": result, "history": _log_step(state, "critique", result, action)}


def should_iterate(state: CreativeState) -> str:
    """Routing function: check critique verdict to decide next step."""
    critique = state.get("critique", "")
    iteration = state.get("iteration", 0)
    max_iter = state.get("max_iterations", 3)

    if iteration >= max_iter:
        return "done"

    # Try to parse verdict from critique JSON
    try:
        # Look for JSON in the critique response
        for line in critique.split("\n"):
            line = line.strip()
            if line.startswith("{"):
                parsed = json.loads(line)
                if parsed.get("verdict") == "approve":
                    return "done"
                break
        # Also check for the word "approve" case-insensitively
        if '"approve"' in critique.lower() or '"verdict": "approve"' in critique.lower():
            return "done"
    except (json.JSONDecodeError, KeyError):
        pass

    # Check for approve in a more lenient way
    if "APPROVE" in critique.upper() and "ITERATE" not in critique.upper():
        return "done"

    return "iterate"


def increment_iteration(state: CreativeState) -> dict:
    return {"iteration": state["iteration"] + 1}


def collect_flow_metrics(state: CreativeState) -> dict:
    """Snapshot flow controller and agent metrics."""
    metrics = {}
    if _flow:
        metrics["flow"] = _flow.dashboard()
    metrics["agents"] = {
        name: {"state": a.state.value, **a.metrics}
        for name, a in _agents.items()
    }
    return metrics


def mark_approved(state: CreativeState) -> dict:
    return {"status": "approved", "flow_metrics": collect_flow_metrics(state)}


def build_graph() -> StateGraph:
    """Build the LangGraph creative orchestration graph."""
    graph = StateGraph(CreativeState)

    graph.add_node("research", research_node)
    graph.add_node("concept", concept_node)
    graph.add_node("layout", layout_node)
    graph.add_node("critique", critique_node)
    graph.add_node("increment", increment_iteration)
    graph.add_node("finalize", mark_approved)

    graph.set_entry_point("research")
    graph.add_edge("research", "concept")
    graph.add_edge("concept", "layout")
    graph.add_edge("layout", "critique")

    graph.add_conditional_edges(
        "critique",
        should_iterate,
        {
            "iterate": "increment",
            "done": "finalize",
        },
    )
    graph.add_edge("increment", "concept")
    graph.add_edge("finalize", END)

    return graph


def compile_graph(use_rules: bool = True):
    """Compile and return the runnable graph.

    If use_rules=True (default), initializes the module-level flow controller
    and rule-driven agent wrappers so that graph nodes use Wolfram state machines.
    """
    global _flow, _agents

    if use_rules:
        _flow = FlowController()
        _flow.add_queue("research", wip_limit=2, batch_size=1)
        _flow.add_queue("concept", wip_limit=2, batch_size=1)
        _flow.add_queue("layout", wip_limit=1, batch_size=1)
        _flow.add_queue("critique", wip_limit=1, batch_size=1)

        for name in ("research", "concept", "layout", "critique"):
            _agents[name] = RuleDrivenAgent(
                name=name,
                invoke_fn=lambda n, text, brief: invoke_agent(n, input_text=text, brief=brief),
                flow=_flow,
            )
    else:
        _flow = None
        _agents = {}

    return build_graph().compile()
