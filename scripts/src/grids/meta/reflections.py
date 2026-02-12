"""Reflections -- post-task agent introspection and journaling.

After each task, agents log what went well, what didn't, bottlenecks,
and time spent. These feed into the system-refine meta-agent.
"""

import json
import os
import time
from dataclasses import dataclass, field

from rich.console import Console

console = Console(stderr=True)


@dataclass
class Reflection:
    agent: str
    task_id: str
    timestamp: float = field(default_factory=time.time)
    went_well: list[str] = field(default_factory=list)
    went_poorly: list[str] = field(default_factory=list)
    bottlenecks: list[str] = field(default_factory=list)
    time_spent: float = 0.0
    iterations_needed: int = 0
    confidence: float = 0.5
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "task_id": self.task_id,
            "timestamp": self.timestamp,
            "went_well": self.went_well,
            "went_poorly": self.went_poorly,
            "bottlenecks": self.bottlenecks,
            "time_spent": self.time_spent,
            "iterations_needed": self.iterations_needed,
            "confidence": self.confidence,
            "suggestions": self.suggestions,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Reflection":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class ReflectionJournal:
    """Collects and queries agent reflections across sessions."""

    def __init__(self, journal_dir: str = "tmp/reflections"):
        self.journal_dir = journal_dir
        os.makedirs(journal_dir, exist_ok=True)

    def add(self, reflection: Reflection):
        path = os.path.join(
            self.journal_dir,
            f"{reflection.agent}-{int(reflection.timestamp)}.json",
        )
        with open(path, "w") as f:
            json.dump(reflection.to_dict(), f, indent=2)

    def all(self) -> list[Reflection]:
        reflections = []
        for f in sorted(os.listdir(self.journal_dir)):
            if f.endswith(".json"):
                path = os.path.join(self.journal_dir, f)
                with open(path) as fh:
                    reflections.append(Reflection.from_dict(json.load(fh)))
        return reflections

    def by_agent(self, agent: str) -> list[Reflection]:
        return [r for r in self.all() if r.agent == agent]

    def recent(self, n: int = 10) -> list[Reflection]:
        return sorted(self.all(), key=lambda r: r.timestamp, reverse=True)[:n]

    def common_bottlenecks(self, n: int = 10) -> list[tuple[str, int]]:
        """Find the most frequently reported bottlenecks."""
        counts: dict[str, int] = {}
        for r in self.all():
            for b in r.bottlenecks:
                b_lower = b.lower().strip()
                counts[b_lower] = counts.get(b_lower, 0) + 1
        return sorted(counts.items(), key=lambda x: x[1], reverse=True)[:n]

    def avg_iterations_by_agent(self) -> dict[str, float]:
        agent_iters: dict[str, list[int]] = {}
        for r in self.all():
            agent_iters.setdefault(r.agent, []).append(r.iterations_needed)
        return {
            agent: sum(iters) / len(iters)
            for agent, iters in agent_iters.items()
            if iters
        }

    def summary(self) -> dict:
        all_r = self.all()
        return {
            "total_reflections": len(all_r),
            "agents": list(set(r.agent for r in all_r)),
            "common_bottlenecks": self.common_bottlenecks(5),
            "avg_iterations": self.avg_iterations_by_agent(),
            "total_time_spent": sum(r.time_spent for r in all_r),
        }


def reflect_on_execution(
    agent_name: str,
    task_id: str,
    history: list[dict],
    elapsed: float,
    iterations: int,
    approved: bool,
    journal: ReflectionJournal | None = None,
) -> Reflection:
    """Generate a reflection from execution history using LLM."""
    from grids.orchestration.agents import get_llm
    from langchain_core.messages import HumanMessage

    llm = get_llm(temperature=0.2)

    history_str = json.dumps(history[-10:], indent=2, default=str)
    prompt = (
        f"You are the {agent_name} agent reflecting on task {task_id}.\n"
        f"Time spent: {elapsed:.1f}s, Iterations: {iterations}, Approved: {approved}\n\n"
        f"Execution history:\n{history_str}\n\n"
        "Reflect on this task. Output JSON:\n"
        "{\n"
        '  "went_well": ["..."],\n'
        '  "went_poorly": ["..."],\n'
        '  "bottlenecks": ["..."],\n'
        '  "confidence": 0.0-1.0,\n'
        '  "suggestions": ["improvements for next time"]\n'
        "}"
    )

    response = llm.invoke([HumanMessage(content=prompt)])
    text = response.content.strip()

    try:
        import re
        blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
        parsed = json.loads(blocks[0] if blocks else text)
    except (json.JSONDecodeError, IndexError):
        parsed = {"went_well": [], "went_poorly": [text[:200]], "bottlenecks": [], "confidence": 0.5, "suggestions": []}

    reflection = Reflection(
        agent=agent_name,
        task_id=task_id,
        went_well=parsed.get("went_well", []),
        went_poorly=parsed.get("went_poorly", []),
        bottlenecks=parsed.get("bottlenecks", []),
        time_spent=elapsed,
        iterations_needed=iterations,
        confidence=parsed.get("confidence", 0.5),
        suggestions=parsed.get("suggestions", []),
    )

    if journal:
        journal.add(reflection)

    return reflection
