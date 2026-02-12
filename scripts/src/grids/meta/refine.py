"""System refine meta-agent -- reads reflections, proposes system improvements.

Analyzes patterns across agent reflections and proposes changes:
- New or adjusted rule tables
- Adjusted strictness levels for sub-agents
- New sub-agents for uncovered aspects
- Pipeline optimizations (batch sizes, WIP limits)
"""

import json

from langchain_core.messages import HumanMessage, SystemMessage
from rich.console import Console
from rich.panel import Panel

from grids.meta.reflections import ReflectionJournal
from grids.orchestration.agents import get_llm

console = Console(stderr=True)

REFINE_SYSTEM = """You are the system-refine meta-agent for an emergent design orchestration system.

You analyze agent reflections -- what went well, what didn't, bottlenecks, and suggestions --
to identify systemic improvements.

You can propose changes in these categories:
1. RULE_TABLE: modify agent state machine rules (add/remove/change transitions)
2. STRICTNESS: adjust sub-agent strictness levels (too strict = too many iterations, too lenient = low quality)
3. NEW_SUB_AGENT: propose a new sub-agent to cover an uncovered aspect
4. PIPELINE: adjust batch sizes, WIP limits, or queue configuration
5. SKILL: recommend new skills to acquire or existing skills to improve

Be conservative. Only propose changes supported by clear patterns in the data.
Each proposal must include: category, description, rationale (citing specific reflections), and the concrete change.

Output JSON:
{
  "analysis": "summary of patterns observed",
  "proposals": [
    {"category": "...", "description": "...", "rationale": "...", "change": {...}},
    ...
  ],
  "health_score": 0.0-1.0
}"""


def analyze_and_propose(
    journal: ReflectionJournal,
    verbose: bool = True,
) -> dict:
    """Analyze reflections and propose system improvements."""
    summary = journal.summary()
    recent = journal.recent(20)

    if not recent:
        return {"analysis": "No reflections to analyze.", "proposals": [], "health_score": 1.0}

    if verbose:
        console.print(Panel(
            f"Total reflections: {summary['total_reflections']}\n"
            f"Agents: {', '.join(summary['agents'])}\n"
            f"Total time: {summary['total_time_spent']:.1f}s\n"
            f"Top bottlenecks: {summary['common_bottlenecks'][:3]}",
            title="Reflection Summary",
            border_style="cyan",
        ))

    reflections_str = json.dumps(
        [r.to_dict() for r in recent],
        indent=2, default=str,
    )

    llm = get_llm(temperature=0.3)
    messages = [
        SystemMessage(content=REFINE_SYSTEM),
        HumanMessage(content=(
            f"Reflection summary:\n{json.dumps(summary, indent=2, default=str)}\n\n"
            f"Recent reflections:\n{reflections_str}\n\n"
            "Analyze patterns and propose improvements."
        )),
    ]

    response = llm.invoke(messages)
    text = response.content.strip()

    result = _parse_proposals(text)

    if verbose and result.get("proposals"):
        console.print(f"\n[bold]Proposals ({len(result['proposals'])}):[/bold]")
        for p in result["proposals"]:
            console.print(f"  [{p.get('category', '?')}] {p.get('description', '')}")
            console.print(f"    Rationale: {p.get('rationale', '')[:150]}")

    return result


def apply_proposal(proposal: dict, dry_run: bool = True) -> dict:
    """Apply a system improvement proposal.

    Returns the change made (or what would be made in dry_run mode).
    """
    category = proposal.get("category", "")
    change = proposal.get("change", {})

    if dry_run:
        return {"applied": False, "dry_run": True, "proposal": proposal}

    if category == "STRICTNESS":
        domain_file = change.get("domain_file")
        agent_name = change.get("agent_name")
        new_strictness = change.get("strictness")
        if domain_file and agent_name and new_strictness is not None:
            import yaml
            with open(domain_file) as f:
                config = yaml.safe_load(f)
            for sa in config.get("sub_agents", []):
                if sa.get("name") == agent_name:
                    sa["strictness"] = new_strictness
                    break
            with open(domain_file, "w") as f:
                yaml.dump(config, f, default_flow_style=False)
            return {"applied": True, "category": category, "change": change}

    return {"applied": False, "reason": f"Auto-apply not supported for category: {category}", "proposal": proposal}


def _parse_proposals(text: str) -> dict:
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
        return {"analysis": text, "proposals": [], "health_score": 0.5}
