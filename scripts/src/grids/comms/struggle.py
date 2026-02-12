"""Struggle protocol -- when an agent is unsatisfied, it shows its work.

Not a simple "request more iterations" -- the agent packages:
1. Its current best attempt (rendered if possible)
2. What approaches it tried
3. What's not working and why
4. References it consulted
5. What kind of direction it needs

This creates a struggle report that a human or higher-level agent can respond to.
"""

import json
import os
import time
from dataclasses import dataclass, field

from rich.console import Console
from rich.panel import Panel

from grids.domain.work_orders import WorkOrder, WorkOrderQueue, WorkOrderStatus

console = Console(stderr=True)


@dataclass
class StruggleReport:
    agent: str
    work_order_id: str
    timestamp: float = field(default_factory=time.time)
    current_attempt: dict = field(default_factory=dict)
    screenshot_path: str | None = None
    approaches_tried: list[str] = field(default_factory=list)
    whats_not_working: str = ""
    references_consulted: list[dict] = field(default_factory=list)
    direction_needed: str = ""
    confidence: float = 0.0
    iteration: int = 0

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "work_order_id": self.work_order_id,
            "timestamp": self.timestamp,
            "current_attempt_format": self.current_attempt.get("format", "none"),
            "screenshot_path": self.screenshot_path,
            "approaches_tried": self.approaches_tried,
            "whats_not_working": self.whats_not_working,
            "references_consulted": self.references_consulted,
            "direction_needed": self.direction_needed,
            "confidence": self.confidence,
            "iteration": self.iteration,
        }


def generate_struggle_report(
    agent_name: str,
    work_order: WorkOrder,
    artifact: dict,
    critique_history: list[dict],
    references_used: list[dict] | None = None,
    screenshot_path: str | None = None,
) -> StruggleReport:
    """Generate a struggle report from execution context."""
    from grids.orchestration.agents import get_llm
    from langchain_core.messages import HumanMessage

    approaches = []
    for c in critique_history:
        feedback = c.get("feedback", c.get("critique", {}).get("feedback", ""))
        iteration = c.get("iteration", "?")
        approaches.append(f"Iteration {iteration}: {feedback[:200]}")

    llm = get_llm(temperature=0.3)
    prompt = (
        f"You are the {agent_name} agent. You've been iterating on a task but can't get it right.\n\n"
        f"Task: {json.dumps(work_order.spec, indent=2)[:1000]}\n\n"
        f"Iteration history:\n" + "\n".join(approaches) + "\n\n"
        f"Current attempt format: {artifact.get('format', 'unknown')}\n\n"
        "Explain specifically:\n"
        "1. What's not working (be specific about the design/technical problem)\n"
        "2. What kind of direction you need (aesthetic? technical? structural?)\n\n"
        "Output JSON: {\"whats_not_working\": \"...\", \"direction_needed\": \"...\", \"confidence\": 0.0-1.0}"
    )

    response = llm.invoke([HumanMessage(content=prompt)])
    text = response.content.strip()

    try:
        import re
        blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
        parsed = json.loads(blocks[0] if blocks else text)
    except (json.JSONDecodeError, IndexError):
        parsed = {"whats_not_working": text[:500], "direction_needed": "general guidance", "confidence": 0.1}

    return StruggleReport(
        agent=agent_name,
        work_order_id=work_order.id,
        current_attempt=artifact,
        screenshot_path=screenshot_path,
        approaches_tried=approaches,
        whats_not_working=parsed.get("whats_not_working", ""),
        references_consulted=references_used or [],
        direction_needed=parsed.get("direction_needed", ""),
        confidence=parsed.get("confidence", 0.1),
        iteration=work_order.iteration,
    )


def emit_struggle(
    report: StruggleReport,
    queue: WorkOrderQueue,
    output_dir: str,
    verbose: bool = True,
) -> str:
    """Save struggle report and emit a direction-needed work order."""
    # Save the full report
    report_path = os.path.join(output_dir, "struggle-reports", f"{report.work_order_id}.json")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report.to_dict(), f, indent=2, default=str)

    # Emit a work order for direction
    order = queue.emit_new(
        domain=queue.orders_dir.split("/")[-2],
        kind="direction-needed",
        spec={
            "struggle_report": report_path,
            "agent": report.agent,
            "whats_not_working": report.whats_not_working,
            "direction_needed": report.direction_needed,
            "iteration": report.iteration,
        },
        acceptance_criteria=["Provide clear direction for the struggling agent"],
        cost_of_delay=5.0,
        job_size=0.5,
        parent_id=report.work_order_id,
    )

    if verbose:
        console.print(Panel(
            f"Agent: {report.agent}\n"
            f"Work order: {report.work_order_id}\n"
            f"Confidence: {report.confidence:.0%}\n\n"
            f"[bold]What's not working:[/bold]\n{report.whats_not_working[:300]}\n\n"
            f"[bold]Direction needed:[/bold]\n{report.direction_needed[:300]}",
            title="Struggle Report",
            border_style="red",
        ))

    return report_path
