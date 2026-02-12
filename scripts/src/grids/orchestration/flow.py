"""Reinertsen-inspired flow management -- queues, WIP limits, batch sizing, WSJF.

Models the economic framework from Principles of Product Development Flow:
- Work items have Cost of Delay (urgency/value scoring)
- WIP limits per agent type prevent bottlenecks
- Batch size controls for throughput optimization
- Cycle time tracking for feedback and improvement
- WSJF (Weighted Shortest Job First) prioritization
"""

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Priority(str, Enum):
    URGENT = "urgent"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


@dataclass
class WorkItem:
    """A unit of work flowing through the creative pipeline."""
    id: str
    kind: str  # e.g., "brief", "research_result", "concept", "layout_spec", "critique"
    payload: dict[str, Any]
    priority: Priority = Priority.NORMAL
    cost_of_delay: float = 1.0  # higher = more urgent ($/week of delay)
    job_size: float = 1.0  # estimated effort (1.0 = average)
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    iteration: int = 0
    parent_id: str | None = None

    @property
    def wsjf_score(self) -> float:
        """Weighted Shortest Job First: CoD / job_size. Higher = do first."""
        if self.job_size <= 0:
            return float("inf")
        return self.cost_of_delay / self.job_size

    @property
    def cycle_time(self) -> float | None:
        if self.started_at and self.completed_at:
            return self.completed_at - self.started_at
        return None

    @property
    def lead_time(self) -> float | None:
        if self.completed_at:
            return self.completed_at - self.created_at
        return None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "priority": self.priority.value,
            "cost_of_delay": self.cost_of_delay,
            "job_size": self.job_size,
            "wsjf_score": self.wsjf_score,
            "iteration": self.iteration,
            "cycle_time": self.cycle_time,
            "lead_time": self.lead_time,
        }


class FlowQueue:
    """A priority queue for work items with WIP limits and metrics."""

    def __init__(self, name: str, wip_limit: int = 3, batch_size: int = 1):
        self.name = name
        self.wip_limit = wip_limit
        self.batch_size = batch_size
        self._queue: list[WorkItem] = []
        self._in_progress: list[WorkItem] = []
        self._completed: list[WorkItem] = []

    @property
    def queue_size(self) -> int:
        return len(self._queue)

    @property
    def wip(self) -> int:
        return len(self._in_progress)

    @property
    def has_capacity(self) -> bool:
        return self.wip < self.wip_limit

    @property
    def is_empty(self) -> bool:
        return len(self._queue) == 0

    def enqueue(self, item: WorkItem):
        self._queue.append(item)
        self._sort_by_wsjf()

    def _sort_by_wsjf(self):
        self._queue.sort(key=lambda x: x.wsjf_score, reverse=True)

    def pull(self) -> WorkItem | None:
        """Pull next item if WIP allows. Returns None if at limit or queue empty."""
        if not self.has_capacity or not self._queue:
            return None
        item = self._queue.pop(0)
        item.started_at = time.time()
        self._in_progress.append(item)
        return item

    def pull_batch(self) -> list[WorkItem]:
        """Pull up to batch_size items."""
        batch = []
        for _ in range(self.batch_size):
            item = self.pull()
            if item is None:
                break
            batch.append(item)
        return batch

    def complete(self, item_id: str) -> WorkItem | None:
        """Mark an in-progress item as complete."""
        for i, item in enumerate(self._in_progress):
            if item.id == item_id:
                item.completed_at = time.time()
                self._completed.append(item)
                self._in_progress.pop(i)
                return item
        return None

    def metrics(self) -> dict:
        completed_times = [i.cycle_time for i in self._completed if i.cycle_time]
        avg_cycle = sum(completed_times) / len(completed_times) if completed_times else 0
        return {
            "name": self.name,
            "queue_size": self.queue_size,
            "wip": self.wip,
            "wip_limit": self.wip_limit,
            "completed": len(self._completed),
            "avg_cycle_time": round(avg_cycle, 3),
            "batch_size": self.batch_size,
        }


class FlowController:
    """Manages the full pipeline of queues between agents."""

    def __init__(self):
        self.queues: dict[str, FlowQueue] = {}
        self._item_counter = 0

    def add_queue(self, name: str, wip_limit: int = 3, batch_size: int = 1):
        self.queues[name] = FlowQueue(name, wip_limit, batch_size)

    def next_id(self) -> str:
        self._item_counter += 1
        return f"work-{self._item_counter:04d}"

    def submit(self, queue_name: str, kind: str, payload: dict,
               priority: Priority = Priority.NORMAL,
               cost_of_delay: float = 1.0, job_size: float = 1.0,
               parent_id: str | None = None) -> WorkItem:
        item = WorkItem(
            id=self.next_id(),
            kind=kind,
            payload=payload,
            priority=priority,
            cost_of_delay=cost_of_delay,
            job_size=job_size,
            parent_id=parent_id,
        )
        self.queues[queue_name].enqueue(item)
        return item

    def transfer(self, item: WorkItem, from_queue: str, to_queue: str, new_kind: str | None = None):
        """Complete item in one queue and enqueue a derived item in the next."""
        self.queues[from_queue].complete(item.id)
        new_item = WorkItem(
            id=self.next_id(),
            kind=new_kind or item.kind,
            payload=item.payload,
            priority=item.priority,
            cost_of_delay=item.cost_of_delay,
            job_size=item.job_size,
            parent_id=item.id,
            iteration=item.iteration,
        )
        self.queues[to_queue].enqueue(new_item)
        return new_item

    def iterate(self, item: WorkItem, queue_name: str, feedback: dict):
        """Send item back through a queue for another iteration (critique loop)."""
        self.queues[queue_name].complete(item.id)
        new_item = WorkItem(
            id=self.next_id(),
            kind=item.kind,
            payload={**item.payload, "feedback": feedback},
            priority=item.priority,
            cost_of_delay=item.cost_of_delay * 1.2,  # delay cost increases with iterations
            job_size=item.job_size * 0.7,  # revisions are usually smaller
            parent_id=item.id,
            iteration=item.iteration + 1,
        )
        self.queues[queue_name].enqueue(new_item)
        return new_item

    def dashboard(self) -> dict:
        return {
            "queues": {name: q.metrics() for name, q in self.queues.items()},
            "total_items": self._item_counter,
        }
