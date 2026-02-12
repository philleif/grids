"""Work order queue -- domain agents emit, execution agents consume.

Work orders are JSON files written to a queue directory. Execution agents
(separate processes) pick them up, produce artifacts, and deposit results.
This is the Reinertsen-pure approach: decoupled queues, no shared process state.
"""

import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from grids.orchestration.flow import Priority


class WorkOrderStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    VALIDATING = "validating"
    APPROVED = "approved"
    ITERATING = "iterating"


@dataclass
class WorkOrder:
    id: str
    domain: str
    kind: str  # "code", "test", "deploy", "research"
    spec: dict[str, Any]
    acceptance_criteria: list[str]
    priority: Priority = Priority.NORMAL
    cost_of_delay: float = 1.0
    job_size: float = 1.0
    iteration: int = 0
    parent_id: str | None = None
    status: WorkOrderStatus = WorkOrderStatus.PENDING
    created_at: float = field(default_factory=time.time)
    feedback: str = ""

    @property
    def wsjf_score(self) -> float:
        if self.job_size <= 0:
            return float("inf")
        return self.cost_of_delay / self.job_size

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "domain": self.domain,
            "kind": self.kind,
            "spec": self.spec,
            "acceptance_criteria": self.acceptance_criteria,
            "priority": self.priority.value,
            "cost_of_delay": self.cost_of_delay,
            "job_size": self.job_size,
            "wsjf_score": self.wsjf_score,
            "iteration": self.iteration,
            "parent_id": self.parent_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "feedback": self.feedback,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorkOrder":
        return cls(
            id=data["id"],
            domain=data["domain"],
            kind=data["kind"],
            spec=data["spec"],
            acceptance_criteria=data.get("acceptance_criteria", []),
            priority=Priority(data.get("priority", "normal")),
            cost_of_delay=data.get("cost_of_delay", 1.0),
            job_size=data.get("job_size", 1.0),
            iteration=data.get("iteration", 0),
            parent_id=data.get("parent_id"),
            status=WorkOrderStatus(data.get("status", "pending")),
            created_at=data.get("created_at", time.time()),
            feedback=data.get("feedback", ""),
        )


class WorkOrderQueue:
    """File-based work order queue. Domain agents write, execution agents read."""

    def __init__(self, base_dir: str, domain: str):
        self.orders_dir = os.path.join(base_dir, domain, "work-orders")
        self.artifacts_dir = os.path.join(base_dir, domain, "artifacts")
        self._counter = 0
        os.makedirs(self.orders_dir, exist_ok=True)
        os.makedirs(self.artifacts_dir, exist_ok=True)

    def next_id(self) -> str:
        self._counter += 1
        return f"wo-{int(time.time())}-{self._counter:04d}"

    def emit(self, order: WorkOrder) -> Path:
        """Write a work order to the queue directory."""
        path = Path(self.orders_dir) / f"{order.id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(order.to_dict(), f, indent=2)
        return path

    def emit_new(
        self,
        domain: str,
        kind: str,
        spec: dict,
        acceptance_criteria: list[str],
        priority: Priority = Priority.NORMAL,
        cost_of_delay: float = 1.0,
        job_size: float = 1.0,
        parent_id: str | None = None,
        iteration: int = 0,
        feedback: str = "",
    ) -> WorkOrder:
        """Create and emit a new work order."""
        order = WorkOrder(
            id=self.next_id(),
            domain=domain,
            kind=kind,
            spec=spec,
            acceptance_criteria=acceptance_criteria,
            priority=priority,
            cost_of_delay=cost_of_delay,
            job_size=job_size,
            parent_id=parent_id,
            iteration=iteration,
            feedback=feedback,
        )
        self.emit(order)
        return order

    def emit_iteration(self, original: WorkOrder, feedback: str) -> WorkOrder:
        """Emit a new work order for an iteration (Reinertsen economics applied)."""
        return self.emit_new(
            domain=original.domain,
            kind=original.kind,
            spec={**original.spec, "previous_feedback": feedback},
            acceptance_criteria=original.acceptance_criteria,
            priority=original.priority,
            cost_of_delay=original.cost_of_delay * 1.2,
            job_size=original.job_size * 0.7,
            parent_id=original.id,
            iteration=original.iteration + 1,
            feedback=feedback,
        )

    def list_pending(self) -> list[WorkOrder]:
        """List all pending work orders, sorted by WSJF (highest first)."""
        orders = []
        for f in Path(self.orders_dir).glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                order = WorkOrder.from_dict(data)
                if order.status == WorkOrderStatus.PENDING:
                    orders.append(order)
            except (json.JSONDecodeError, KeyError):
                continue
        orders.sort(key=lambda o: o.wsjf_score, reverse=True)
        return orders

    def pick_up(self, order_id: str) -> WorkOrder | None:
        """Mark a work order as in-progress."""
        return self._update_status(order_id, WorkOrderStatus.IN_PROGRESS)

    def deposit_artifact(self, order_id: str, artifact: dict) -> Path:
        """Execution agent deposits its output artifact."""
        path = Path(self.artifacts_dir) / f"{order_id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(artifact, f, indent=2)
        self._update_status(order_id, WorkOrderStatus.VALIDATING)
        return path

    def load_artifact(self, order_id: str) -> dict | None:
        """Load an artifact by work order ID."""
        path = Path(self.artifacts_dir) / f"{order_id}.json"
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def mark_approved(self, order_id: str) -> WorkOrder | None:
        return self._update_status(order_id, WorkOrderStatus.APPROVED)

    def mark_iterating(self, order_id: str) -> WorkOrder | None:
        return self._update_status(order_id, WorkOrderStatus.ITERATING)

    def _update_status(self, order_id: str, status: WorkOrderStatus) -> WorkOrder | None:
        path = Path(self.orders_dir) / f"{order_id}.json"
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["status"] = status.value
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return WorkOrder.from_dict(data)
