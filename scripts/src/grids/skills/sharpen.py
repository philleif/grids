"""Sharpen the axe -- pre-task skill acquisition and preparation.

Before each execution cycle, searches for relevant skills and self-installs
new ones to make the upcoming work more efficient.
"""

import json

from langchain_core.messages import HumanMessage, SystemMessage
from rich.console import Console

from grids.knowledge.store import query_all
from grids.orchestration.agents import get_llm
from grids.skills.registry import Skill, SkillRegistry

console = Console(stderr=True)

SHARPEN_PROMPT = """You are a skill acquisition agent. Before starting a new task, you identify
what tools, techniques, and knowledge would make the work more efficient.

Given a task description and existing skills, recommend:
1. Which existing skills are most relevant (list by name)
2. New skills that should be acquired (with full definitions)

A skill is a reusable capability with:
- name: short identifier
- description: what it does
- domain: which domain it belongs to
- tools: list of {name, description, parameters} tool definitions
- validation_rules: list of rules to check output quality
- examples: list of {input, output} examples
- dependencies: Python packages or system tools needed

Output JSON:
{
  "relevant_existing": ["skill-name-1", ...],
  "new_skills": [{ full skill definition }, ...],
  "preparation_notes": "any setup steps needed"
}"""


def sharpen(
    task_description: str,
    domain: str | None = None,
    registry: SkillRegistry | None = None,
    verbose: bool = True,
) -> dict:
    """Run the sharpen-the-axe step before a task.

    Returns dict with relevant skills and any newly installed ones.
    """
    registry = registry or SkillRegistry()

    existing = registry.list_all()
    existing_summary = "\n".join(
        f"- {s.name}: {s.description} (domain: {s.domain}, used: {s.use_count}x)"
        for s in existing
    ) or "No skills installed yet."

    # Search knowledge base for relevant techniques
    knowledge_hits = query_all(task_description, n_results=3)
    knowledge_context = ""
    for coll, hits in knowledge_hits.items():
        for hit in hits:
            knowledge_context += f"[{coll}] {hit['text'][:200]}\n"

    llm = get_llm(temperature=0.3)
    messages = [
        SystemMessage(content=SHARPEN_PROMPT),
        HumanMessage(content=(
            f"Task: {task_description}\n"
            f"Domain: {domain or 'general'}\n\n"
            f"Existing skills:\n{existing_summary}\n\n"
            f"Relevant knowledge:\n{knowledge_context}\n\n"
            "What skills should I use or acquire?"
        )),
    ]

    response = llm.invoke(messages)
    text = response.content.strip()

    result = _parse_sharpen_response(text)

    # Record usage of relevant existing skills
    for name in result.get("relevant_existing", []):
        registry.record_use(name)

    # Install new skills
    installed = []
    for skill_data in result.get("new_skills", []):
        try:
            skill = Skill(
                name=skill_data.get("name", "unnamed"),
                description=skill_data.get("description", ""),
                domain=skill_data.get("domain", domain or "general"),
                tools=skill_data.get("tools", []),
                validation_rules=skill_data.get("validation_rules", []),
                examples=skill_data.get("examples", []),
                dependencies=skill_data.get("dependencies", []),
            )
            path = registry.install(skill)
            installed.append(skill.name)
            if verbose:
                console.print(f"  [green]Installed skill: {skill.name}[/green]")
        except Exception as e:
            if verbose:
                console.print(f"  [yellow]Failed to install skill: {e}[/yellow]")

    result["installed"] = installed
    return result


def _parse_sharpen_response(text: str) -> dict:
    import re
    try:
        blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
        for block in blocks:
            try:
                return json.loads(block)
            except json.JSONDecodeError:
                continue
        return json.loads(text)
    except json.JSONDecodeError:
        return {
            "relevant_existing": [],
            "new_skills": [],
            "preparation_notes": text,
        }
