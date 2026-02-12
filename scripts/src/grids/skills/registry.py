"""Skill registry -- tracks installed skills, their usage, and metadata.

Skills are structured YAML files that describe a reusable capability:
tool definitions, validation rules, example outputs, and domain context.
"""

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from rich.console import Console

console = Console(stderr=True)

DEFAULT_SKILLS_DIR = "skills"


@dataclass
class Skill:
    name: str
    description: str
    domain: str
    tools: list[dict] = field(default_factory=list)
    validation_rules: list[str] = field(default_factory=list)
    examples: list[dict] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    installed_at: float = field(default_factory=time.time)
    last_used: float = 0
    use_count: int = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "domain": self.domain,
            "tools": self.tools,
            "validation_rules": self.validation_rules,
            "examples": self.examples,
            "dependencies": self.dependencies,
            "installed_at": self.installed_at,
            "last_used": self.last_used,
            "use_count": self.use_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Skill":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class SkillRegistry:
    """Manages installed skills for the agent system."""

    def __init__(self, skills_dir: str = DEFAULT_SKILLS_DIR):
        self.skills_dir = skills_dir
        os.makedirs(skills_dir, exist_ok=True)
        self._index_path = os.path.join(skills_dir, "_index.json")
        self._index: dict[str, Skill] = {}
        self._load_index()

    def _load_index(self):
        if os.path.exists(self._index_path):
            with open(self._index_path) as f:
                data = json.load(f)
            for name, sd in data.items():
                self._index[name] = Skill.from_dict(sd)

    def _save_index(self):
        with open(self._index_path, "w") as f:
            json.dump({n: s.to_dict() for n, s in self._index.items()}, f, indent=2)

    def install(self, skill: Skill) -> Path:
        """Install a skill to the registry."""
        skill_path = Path(self.skills_dir) / f"{skill.name}.yaml"
        with open(skill_path, "w") as f:
            yaml.dump(skill.to_dict(), f, default_flow_style=False)
        self._index[skill.name] = skill
        self._save_index()
        return skill_path

    def get(self, name: str) -> Skill | None:
        return self._index.get(name)

    def list_all(self) -> list[Skill]:
        return list(self._index.values())

    def search(self, query: str) -> list[Skill]:
        q = query.lower()
        return [
            s for s in self._index.values()
            if q in s.name.lower() or q in s.description.lower() or q in s.domain.lower()
        ]

    def record_use(self, name: str):
        if name in self._index:
            self._index[name].use_count += 1
            self._index[name].last_used = time.time()
            self._save_index()

    def for_domain(self, domain: str) -> list[Skill]:
        return [s for s in self._index.values() if s.domain == domain]

    def most_used(self, n: int = 10) -> list[Skill]:
        return sorted(self._index.values(), key=lambda s: s.use_count, reverse=True)[:n]
