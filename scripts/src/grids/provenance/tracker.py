"""Decision tree tracker -- Python-side provenance that mirrors Rust DecisionTree.

Agents log each design decision with influences, alternatives, and rationale.
The tracker generates queryable decision history and design notes markdown.
This is the Python counterpart to libs/layout/src/provenance.rs.
"""

import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DecisionKind(str, Enum):
    LAYOUT = "layout"
    TYPOGRAPHY = "typography"
    COLOR = "color"
    CONTENT = "content"
    COMPOSITION = "composition"
    STYLE_DIRECTION = "style_direction"
    REVISION = "revision"


@dataclass
class Influence:
    source_type: str  # "book", "moodboard", "agent", "user", "prior_decision"
    source_id: str
    description: str
    relevance: str
    weight: float = 1.0

    def to_dict(self) -> dict:
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "description": self.description,
            "relevance": self.relevance,
            "weight": self.weight,
        }


@dataclass
class Alternative:
    description: str
    reason_rejected: str

    def to_dict(self) -> dict:
        return {"description": self.description, "reason_rejected": self.reason_rejected}


@dataclass
class Decision:
    id: str
    agent: str
    kind: DecisionKind
    property_name: str
    value: str
    rationale: str
    influences: list[Influence] = field(default_factory=list)
    alternatives: list[Alternative] = field(default_factory=list)
    confidence: float = 0.8
    parent_id: str | None = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "agent": self.agent,
            "kind": self.kind.value,
            "property": self.property_name,
            "value": self.value,
            "rationale": self.rationale,
            "influences": [i.to_dict() for i in self.influences],
            "alternatives": [a.to_dict() for a in self.alternatives],
            "confidence": self.confidence,
            "parent_id": self.parent_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Decision":
        return cls(
            id=d["id"],
            agent=d["agent"],
            kind=DecisionKind(d["kind"]),
            property_name=d.get("property", ""),
            value=d.get("value", ""),
            rationale=d.get("rationale", ""),
            influences=[Influence(**i) for i in d.get("influences", [])],
            alternatives=[Alternative(**a) for a in d.get("alternatives", [])],
            confidence=d.get("confidence", 0.8),
            parent_id=d.get("parent_id"),
            timestamp=d.get("timestamp", 0),
        )


class DecisionTracker:
    """Tracks design decisions across a session. Python mirror of Rust DecisionTree."""

    def __init__(self, project_id: str):
        self.project_id = project_id
        self.decisions: list[Decision] = []
        self._index: dict[str, int] = {}
        self._counter = 0

    def next_id(self) -> str:
        self._counter += 1
        return f"d-{self._counter:04d}"

    def add(self, decision: Decision):
        idx = len(self.decisions)
        self._index[decision.id] = idx
        self.decisions.append(decision)

    def log(
        self,
        agent: str,
        kind: DecisionKind,
        property_name: str,
        value: str,
        rationale: str,
        influences: list[Influence] | None = None,
        alternatives: list[Alternative] | None = None,
        confidence: float = 0.8,
        parent_id: str | None = None,
    ) -> Decision:
        """Convenience: create and add a decision in one call."""
        d = Decision(
            id=self.next_id(),
            agent=agent,
            kind=kind,
            property_name=property_name,
            value=value,
            rationale=rationale,
            influences=influences or [],
            alternatives=alternatives or [],
            confidence=confidence,
            parent_id=parent_id,
        )
        self.add(d)
        return d

    def get(self, decision_id: str) -> Decision | None:
        idx = self._index.get(decision_id)
        return self.decisions[idx] if idx is not None else None

    def lineage(self, decision_id: str) -> list[Decision]:
        """Walk ancestors from a decision back to root(s)."""
        chain = []
        current = decision_id
        while current:
            d = self.get(current)
            if d is None:
                break
            chain.append(d)
            current = d.parent_id
        return chain

    def by_agent(self, agent: str) -> list[Decision]:
        return [d for d in self.decisions if d.agent == agent]

    def by_kind(self, kind: DecisionKind) -> list[Decision]:
        return [d for d in self.decisions if d.kind == kind]

    def influenced_by(self, source_id: str) -> list[Decision]:
        return [
            d for d in self.decisions
            if any(i.source_id == source_id for i in d.influences)
        ]

    def search(self, query: str) -> list[Decision]:
        """Simple text search across rationale and values."""
        q = query.lower()
        return [
            d for d in self.decisions
            if q in d.rationale.lower() or q in d.value.lower() or q in d.property_name.lower()
        ]

    def to_design_notes(self) -> str:
        """Generate markdown design notes document."""
        md = [f"# Design Notes: {self.project_id}\n"]
        for d in self.decisions:
            md.append(f"## {d.id}: {d.property_name} = {d.value}")
            md.append(f"**Agent:** {d.agent} | **Kind:** {d.kind.value} | **Confidence:** {d.confidence:.0%}\n")
            md.append(f"**Rationale:** {d.rationale}\n")
            if d.influences:
                md.append("**Influences:**")
                for inf in d.influences:
                    md.append(f"- [{inf.source_type}] {inf.description} (weight: {inf.weight:.1f})")
                md.append("")
            if d.alternatives:
                md.append("**Alternatives considered:**")
                for alt in d.alternatives:
                    md.append(f"- ~~{alt.description}~~ -- {alt.reason_rejected}")
                md.append("")
            md.append("---\n")
        return "\n".join(md)

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "decisions": [d.to_dict() for d in self.decisions],
        }

    def save(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str) -> "DecisionTracker":
        with open(path) as f:
            data = json.load(f)
        tracker = cls(data["project_id"])
        for dd in data.get("decisions", []):
            tracker.add(Decision.from_dict(dd))
        tracker._counter = len(tracker.decisions)
        return tracker

    def to_rust_json(self) -> dict:
        """Export in the format consumed by Rust provenance::DecisionTree."""
        rust_decisions = []
        for d in self.decisions:
            rust_influences = []
            for inf in d.influences:
                source_map = {
                    "book": {"type": "Book", "title": inf.description, "chunk_id": inf.source_id, "excerpt": inf.relevance},
                    "moodboard": {"type": "Moodboard", "board_id": "", "ref_id": inf.source_id, "description": inf.description},
                    "agent": {"type": "AgentKnowledge", "agent": inf.source_id, "skill": "", "note": inf.description},
                    "user": {"type": "UserDirection", "input": inf.description},
                    "prior_decision": {"type": "PriorDecision", "decision_id": inf.source_id},
                }
                rust_influences.append({
                    "source": source_map.get(inf.source_type, {"type": "UserDirection", "input": inf.description}),
                    "relevance": inf.relevance,
                    "weight": inf.weight,
                })

            kind_map = {
                DecisionKind.LAYOUT: {"type": "Layout", "property": d.property_name, "value": d.value},
                DecisionKind.TYPOGRAPHY: {"type": "Typography", "property": d.property_name, "value": d.value},
                DecisionKind.COLOR: {"type": "Color", "property": d.property_name, "value": d.value},
                DecisionKind.CONTENT: {"type": "Content", "property": d.property_name, "value": d.value},
                DecisionKind.COMPOSITION: {"type": "Composition", "description": d.value},
                DecisionKind.STYLE_DIRECTION: {"type": "StyleDirection", "description": d.value},
                DecisionKind.REVISION: {"type": "Revision", "original_decision_id": d.parent_id or "", "reason": d.rationale},
            }

            rust_decisions.append({
                "id": d.id,
                "parent_id": d.parent_id,
                "timestamp": str(d.timestamp),
                "agent": d.agent,
                "kind": kind_map.get(d.kind, {"type": "Composition", "description": d.value}),
                "rationale": d.rationale,
                "influences": rust_influences,
                "alternatives_considered": [a.to_dict() for a in d.alternatives],
                "confidence": d.confidence,
            })

        return {"project_id": self.project_id, "decisions": rust_decisions}
