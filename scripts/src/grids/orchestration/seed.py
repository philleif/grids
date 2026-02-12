"""Seed patterns -- initial conditions for the agent grid.

NKS: same rules + different initial conditions = different emergent behavior.
A seed defines: grid size, agent placement, rule table assignments, and
initial work item distribution.

Seeds are YAML-configurable so you can explore the outcome space.
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any

import yaml

from grids.domain.config import DomainConfig, SubAgentConfig, load_domain
from grids.orchestration.grid import AgentGrid, AgentCell, Neighborhood, WorkFragment
from grids.orchestration.rules import generate_rule_table, AgentState


DOMAINS_DIR = Path(__file__).resolve().parents[4] / "domains"


def load_available_domains() -> dict[str, DomainConfig]:
    """Load all domain configs from the domains/ directory."""
    configs = {}
    for path in sorted(DOMAINS_DIR.glob("*.yaml")):
        try:
            cfg = load_domain(path)
            configs[cfg.domain.name] = cfg
        except Exception:
            continue
    return configs


def filter_relevant_agents(
    brief: str,
    config: DomainConfig,
    threshold: float = 0.3,
) -> DomainConfig:
    """Remove sub-agents whose aspect is irrelevant to the brief.
    Uses embedding cosine similarity for fast, cheap filtering.
    Agents below threshold are excluded from grid placement."""
    if not brief or not config.sub_agents:
        return config

    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np

        model = SentenceTransformer("all-MiniLM-L6-v2")
        # Use brief + domain context for better signal. Long briefs dilute similarity
        # so we also encode the domain description for cross-reference.
        domain_hint = config.domain.description[:200]
        brief_query = f"{brief[:800]}\n\nDomain: {domain_hint}"
        brief_embedding = model.encode([brief_query])[0]

        scored: list[tuple[SubAgentConfig, float]] = []

        for sa in config.sub_agents:
            concepts_str = ", ".join(sa.concepts[:10]) if sa.concepts else ""
            sa_text = f"{sa.name}: {sa.aspect}. Concepts: {concepts_str}"
            sa_embedding = model.encode([sa_text])[0]
            similarity = float(np.dot(brief_embedding, sa_embedding) / (
                np.linalg.norm(brief_embedding) * np.linalg.norm(sa_embedding)
            ))
            scored.append((sa, similarity))

        # Sort by relevance descending
        scored.sort(key=lambda x: x[1], reverse=True)

        # Always keep at least 2 sub-agents per domain (the seed explicitly
        # requested this domain, so gutting it defeats the purpose).
        # Beyond the minimum, apply the threshold.
        min_keep = min(2, len(scored))
        relevant: list[SubAgentConfig] = []
        excluded: list[tuple[str, float]] = []

        for i, (sa, similarity) in enumerate(scored):
            if i < min_keep or similarity >= threshold:
                relevant.append(sa)
            else:
                excluded.append((sa.name, similarity))

        if excluded:
            from rich.console import Console
            console = Console(stderr=True)
            domain_name = config.domain.name
            for name, score in excluded:
                console.print(
                    f"  [dim]Filtered out {domain_name}/{name} "
                    f"(relevance={score:.2f} < {threshold})[/dim]"
                )

        # Return a modified copy -- don't mutate the original
        filtered = copy.deepcopy(config)
        filtered.sub_agents = relevant
        return filtered

    except ImportError:
        return config


def seed_from_domains(
    domains: list[str] | None = None,
    grid_width: int | None = None,
    grid_height: int | None = None,
    neighborhood: Neighborhood = Neighborhood.MOORE,
    include_execution: bool = True,
    project_config: dict[str, str] | None = None,
    min_domain_coverage: int = 2,
    brief_text: str | None = None,
    relevance_threshold: float = 0.3,
) -> AgentGrid:
    """Build a grid seeded from domain YAML configs.

    Layout strategy:
    - Each domain gets a column (or region) in the grid
    - Master at the top of each domain column
    - Sub-agents below
    - Research agents on the left edge
    - Execution agents on the right edge
    - Critique agents adjacent to execution

    This gives a natural flow: research (left) -> domain knowledge (center) -> execution (right)
    with critique feedback loops through neighbor adjacency.
    """
    all_configs = load_available_domains()

    if domains is None:
        domains = list(all_configs.keys())
    else:
        domains = [d for d in domains if d in all_configs]

    if not domains:
        raise ValueError("No valid domains found")

    # Filter irrelevant sub-agents per brief (Change 3: project-adaptive domains)
    if brief_text:
        for d in domains:
            all_configs[d] = filter_relevant_agents(brief_text, all_configs[d], relevance_threshold)

    # Calculate grid dimensions (adaptive after filtering)
    max_sub_agents = max(len(all_configs[d].sub_agents) for d in domains) if domains else 0
    n_domains = len(domains)

    # Width: 1 (research) + n_domains + 1 (critique) + 1 (execution) + 1 (critique-right)
    # Execution in the CENTER so it has Moore-neighborhood reach to domain columns
    # Layout: [research | domain cols ... | execution | critique]
    # Center execution column = n_domains // 2 + 1
    w = grid_width or (n_domains + 3)
    # Height: 1 (master row) + max sub-agents + 1 (buffer)
    h = grid_height or (max_sub_agents + 2)

    grid = AgentGrid(width=w, height=h, neighborhood=neighborhood)

    # Split domains into left and right halves around the center execution column
    exec_col = n_domains // 2 + 1  # center of domain columns
    left_domains = domains[:exec_col - 1]   # domains to left of execution
    right_domains = domains[exec_col - 1:]  # domains to right (and adjacent to) execution

    # Place research agents in column 0
    _place_research_column(grid, 0, h, domains, all_configs)

    # Place left domain columns (1 through exec_col-1)
    for i, domain_name in enumerate(left_domains):
        col = i + 1
        config = all_configs[domain_name]
        _place_domain_column(grid, col, config)

    # Place execution agents in CENTER column (adjacent to most domain masters)
    if include_execution:
        _place_execution_column(grid, exec_col, h,
                                project_config=project_config or {},
                                min_domain_coverage=min_domain_coverage)

    # Place right domain columns (exec_col+1 onward)
    for i, domain_name in enumerate(right_domains):
        col = exec_col + 1 + i
        if col >= w - 1:  # leave room for critique column
            break
        config = all_configs[domain_name]
        _place_domain_column(grid, col, config)

    # Place critique agents in last column (WIP=5 to handle burst arrivals)
    crit_col = w - 1
    _place_critique_column(grid, crit_col, h, domains)

    return grid


def seed_from_yaml(seed_path: str | Path) -> tuple[AgentGrid, dict]:
    """Load a seed configuration from a YAML file.

    Seed YAML format:
    ```yaml
    grid:
      width: 8
      height: 6
      neighborhood: moore

    domains:
      - design
      - production-tech
      - editorial

    project:
      type: tauri-desktop
      framework: vite + vanilla JS
      output_dir: apps/my-app

    complexity_budget:       # GRD-8: scope convergence limits
      max_screens: 8
      max_files: 25

    execution:
      min_domain_coverage: 2

    initial_work:
      - target: masters
        content: "Build a calling cards design studio"
        kind: brief_chunk
        cost_of_delay: 5.0
    ```
    """
    path = Path(seed_path)
    with open(path, "r", encoding="utf-8") as f:
        seed_config = yaml.safe_load(f)

    grid_cfg = seed_config.get("grid", {})
    domains = seed_config.get("domains", None)
    project_config = seed_config.get("project", {})
    execution_config = seed_config.get("execution", {})

    # Extract brief text for relevance filtering
    brief_text = None
    for w in seed_config.get("initial_work", []):
        if w.get("kind") == "brief_chunk" and w.get("content"):
            brief_text = w["content"]
            break

    grid = seed_from_domains(
        domains=domains,
        grid_width=grid_cfg.get("width"),
        grid_height=grid_cfg.get("height"),
        neighborhood=Neighborhood(grid_cfg.get("neighborhood", "moore")),
        include_execution=seed_config.get("include_execution", True),
        project_config=project_config,
        min_domain_coverage=execution_config.get("min_domain_coverage", 2),
        brief_text=brief_text,
        relevance_threshold=execution_config.get("relevance_threshold", 0.3),
    )

    # Inject initial work items
    initial_work = seed_config.get("initial_work", [])
    for work_cfg in initial_work:
        fragment = WorkFragment(
            id=f"seed-{int(time.time())}-{work_cfg.get('kind', 'brief')}",
            kind=work_cfg.get("kind", "brief_chunk"),
            content=work_cfg.get("content", ""),
            cost_of_delay=work_cfg.get("cost_of_delay", 1.0),
            job_size=work_cfg.get("job_size", 1.0),
        )
        target = work_cfg.get("target", "masters")
        if target == "masters":
            grid.inject_broadcast(fragment, role="master")
        elif target == "research":
            grid.inject_broadcast(fragment, role="research")
        elif target == "all":
            grid.inject_broadcast(fragment)
        else:
            # target is "domain:name" format
            if ":" in target:
                domain, _ = target.split(":", 1)
                grid.inject_broadcast(fragment, domain=domain)
            else:
                grid.inject_broadcast(fragment, domain=target)

    return grid, seed_config


def inject_brief(grid: AgentGrid, brief: str, cost_of_delay: float = 5.0):
    """Inject a brief into all master cells. The standard entry point."""
    fragment = WorkFragment(
        id=f"brief-{int(time.time())}",
        kind="brief_chunk",
        content=brief,
        cost_of_delay=cost_of_delay,
        job_size=2.0,
    )
    grid.inject_broadcast(fragment, role="master")


def seed_phase1b(
    domain_analysis: dict | str,
    brief: str,
    project_config: dict[str, str] | None = None,
    neighborhood: Neighborhood = Neighborhood.MOORE,
    complexity_budget: dict[str, Any] | None = None,
) -> AgentGrid:
    """Build a Phase 1b mini-grid: product specification from domain analysis.

    Grid (5x3) with product-focused roles:
    - product-designer: screens, flows, interactions, user journeys
    - systems-architect: data model, API design, technical architecture
    - product-critique: reviews spec against domain analysis for gaps
    - ux-specifier: detailed interaction specs, state variations, edge cases
    - integration-planner: how screens connect, navigation, data flow between features
    - scope-convergence: identifies redundancies, contradictions, budget violations (GRD-8)

    These cells iterate 3-5 ticks to refine the product spec before
    a final consolidation call merges their output.
    """
    w, h = 5, 3
    grid = AgentGrid(width=w, height=h, neighborhood=neighborhood)
    pc = dict(project_config or {})
    budget = dict(complexity_budget or {})

    # Row 0: product-designer (center), systems-architect (left), ux-specifier (right)
    grid.place(AgentCell(
        position=(1, 0),
        domain="product",
        agent_type="systems-architect",
        role="sub",
        rule_table=generate_rule_table("sub", strictness=0.8),
        wip_limit=4,
        project_config=pc,
    ))
    grid.place(AgentCell(
        position=(2, 0),
        domain="product",
        agent_type="product-designer",
        role="master",
        rule_table=generate_rule_table("master"),
        wip_limit=8,
        project_config=pc,
    ))
    grid.place(AgentCell(
        position=(3, 0),
        domain="product",
        agent_type="ux-specifier",
        role="sub",
        rule_table=generate_rule_table("sub", strictness=0.85),
        wip_limit=4,
        project_config=pc,
    ))

    # Row 1: integration-planner (center), critique (right), scope-convergence (left)
    # GRD-8: scope-convergence cell adjacent to all spec-producing cells.
    # Its rule table uses high-strictness sub rules so it proactively critiques
    # neighbor output, but its invoke handler focuses on scope, not quality.
    grid.place(AgentCell(
        position=(1, 1),
        domain="product",
        agent_type="scope-convergence",
        role="sub",
        rule_table=generate_rule_table("sub", strictness=0.95),
        wip_limit=4,
        project_config={**pc, **({"complexity_budget": json.dumps(budget)} if budget else {})},
    ))
    grid.place(AgentCell(
        position=(2, 1),
        domain="product",
        agent_type="integration-planner",
        role="sub",
        rule_table=generate_rule_table("sub", strictness=0.8),
        wip_limit=4,
        project_config=pc,
    ))
    grid.place(AgentCell(
        position=(3, 1),
        domain="product",
        agent_type="product-critique",
        role="critique",
        rule_table=generate_rule_table("critique"),
        wip_limit=5,
        strictness=0.9,
        project_config=pc,
    ))

    # Inject the domain analysis as initial work
    analysis_content = domain_analysis if isinstance(domain_analysis, dict) else {"analysis": domain_analysis}
    # Include brief so cells have the original context
    analysis_content_with_brief = {
        "brief": brief,
        "domain_analysis": analysis_content,
        "project_config": pc,
    }

    spec_fragment = WorkFragment(
        id=f"phase1b-analysis-{int(time.time())}",
        kind="brief_chunk",
        content=analysis_content_with_brief,
        cost_of_delay=5.0,
        job_size=2.0,
    )

    # Broadcast to all cells
    grid.inject_broadcast(spec_fragment)

    return grid


def seed_phase2(
    consolidated_spec: dict | str,
    project_config: dict[str, str] | None = None,
    domains: list[str] | None = None,
    neighborhood: Neighborhood = Neighborhood.MOORE,
    activate_consultants: bool = True,
    domain_analysis: dict | str | None = None,
) -> AgentGrid:
    """Build a Phase 2 grid: execution-focused, seeded with consolidated spec.

    Layout: smaller grid with execution cells in center, critique cells adjacent,
    and a few domain sub-agents for consultation during code review.

    Phase 2 cells:
    - 3 execution cells (coder, tester, runner) -- the primary actors
    - 2-3 critique cells for code review
    - 1 master cell to coordinate and track completeness
    - Optional domain consultants (1 per domain, for review only)
    """
    n_domains = len(domains) if domains else 0
    # Compact grid: execution needs to be adjacent to critique
    w = max(4, n_domains + 3)
    h = 3

    grid = AgentGrid(width=w, height=h, neighborhood=neighborhood)
    pc = dict(project_config or {})

    # Center column: execution cells (coder, tester, runner)
    exec_col = w // 2
    for i, etype in enumerate(["coder", "tester", "runner"]):
        cell = AgentCell(
            position=(exec_col, i),
            domain="execution",
            agent_type=etype,
            role="execution",
            rule_table=generate_rule_table("execution"),
            wip_limit=6,
            min_domain_coverage=0,  # no gate in phase 2 -- spec is already consolidated
            project_config=pc,
        )
        grid.place(cell)

    # Left of execution: coordination master
    master_col = max(0, exec_col - 1)
    master_cell = AgentCell(
        position=(master_col, 0),
        domain="coordination",
        agent_type="master",
        role="master",
        rule_table=generate_rule_table("master"),
        wip_limit=8,
    )
    grid.place(master_cell)

    # Right of execution: critique cells
    crit_col = min(w - 1, exec_col + 1)
    for i, crit_domain in enumerate(["code-quality", "ux-review"]):
        if i >= h:
            break
        cell = AgentCell(
            position=(crit_col, i),
            domain=crit_domain,
            agent_type="critique",
            role="critique",
            rule_table=generate_rule_table("critique"),
            wip_limit=5,
            strictness=0.85,
        )
        grid.place(cell)

    # Optional domain consultants (far edges, for domain-specific artifact review)
    if domains and activate_consultants:
        all_configs = load_available_domains()
        col = 0
        for i, d in enumerate(domains[:3]):  # max 3 consultants
            if (col, i) in grid.cells:
                continue
            if i >= h:
                break
            cfg = all_configs.get(d)
            if not cfg:
                continue
            cell = AgentCell(
                position=(col, i),
                domain=d,
                agent_type="consultant",
                role="sub",
                rule_table=generate_rule_table("sub"),
                wip_limit=3,
                knowledge_collections=cfg.master.knowledge_collections if cfg else [],
            )
            grid.place(cell)

            # Inject domain analysis summary so consultants have context for reviews
            if domain_analysis:
                analysis_content = domain_analysis if isinstance(domain_analysis, dict) else {"analysis": domain_analysis}
                # Extract domain-specific principles if available
                domain_context = analysis_content
                if isinstance(analysis_content, dict):
                    principles = analysis_content.get("principles", [])
                    domain_principles = [p for p in principles if p.get("source_domain") == d] if principles else []
                    domain_context = {
                        "domain": d,
                        "domain_principles": domain_principles,
                        "product_vision": analysis_content.get("product_vision", ""),
                        "must_haves": analysis_content.get("must_haves", []),
                        "must_avoids": analysis_content.get("must_avoids", []),
                        "tone_and_voice": analysis_content.get("tone_and_voice", ""),
                    }
                grid.inject(cell.position, WorkFragment(
                    id=f"phase2-domain-context-{d}-{int(time.time())}",
                    kind="work_spec",
                    content=domain_context,
                    cost_of_delay=2.0,
                    job_size=1.0,
                    tags={"domain_context": "true"},
                ))

    # Inject the consolidated spec into execution cells and master
    spec_content = consolidated_spec if isinstance(consolidated_spec, str) else json.dumps(consolidated_spec, indent=2)
    spec_fragment = WorkFragment(
        id=f"phase2-spec-{int(time.time())}",
        kind="work_spec",
        content=consolidated_spec,
        cost_of_delay=5.0,
        job_size=3.0,
    )

    # Master gets the spec to coordinate
    grid.inject(master_cell.position, WorkFragment(
        id=f"{spec_fragment.id}-master",
        kind=spec_fragment.kind,
        content=spec_fragment.content,
        cost_of_delay=spec_fragment.cost_of_delay,
        job_size=spec_fragment.job_size,
    ))

    # Extract acceptance criteria from the consolidated spec for the tester cell
    acceptance_criteria = []
    if isinstance(consolidated_spec, dict):
        acceptance_criteria = consolidated_spec.get("acceptance_criteria", [])

    # Each execution cell gets the spec (tester gets acceptance criteria injected)
    for cell in grid.cells_by_role("execution"):
        content = spec_fragment.content
        if cell.agent_type == "tester" and acceptance_criteria:
            # Wrap spec + criteria so the tester sees both
            content = {
                "spec": consolidated_spec,
                "acceptance_criteria": acceptance_criteria,
            }
        grid.inject(cell.position, WorkFragment(
            id=f"{spec_fragment.id}-{cell.agent_type}",
            kind=spec_fragment.kind,
            content=content,
            cost_of_delay=spec_fragment.cost_of_delay,
            job_size=spec_fragment.job_size,
        ))

    return grid


# --- Internal placement helpers ---

def _place_research_column(
    grid: AgentGrid,
    col: int,
    height: int,
    domains: list[str],
    configs: dict[str, DomainConfig],
):
    """Place research agents in a column. One per domain (or shared)."""
    for i, domain_name in enumerate(domains):
        row = min(i, height - 1)
        config = configs[domain_name]
        collections = config.master.knowledge_collections + [f"{domain_name}-web"]

        cell = AgentCell(
            position=(col, row),
            domain=domain_name,
            agent_type="research",
            role="research",
            rule_table=generate_rule_table("research"),
            wip_limit=4,
            knowledge_collections=collections,
        )
        grid.place(cell)

    # Fill remaining rows with general research cells
    placed = len(domains)
    for row in range(placed, min(height, placed + 2)):
        cell = AgentCell(
            position=(col, row),
            domain="general",
            agent_type="research",
            role="research",
            rule_table=generate_rule_table("research"),
            wip_limit=4,
            knowledge_collections=["nks", "devflow"],
        )
        grid.place(cell)


def _place_domain_column(grid: AgentGrid, col: int, config: DomainConfig):
    """Place a domain's master + sub-agents in a column."""
    domain_name = config.domain.name
    collections = config.master.knowledge_collections

    # Row 0: master agent (WIP=8 to avoid inbox jam from sub-agent critiques)
    master_cell = AgentCell(
        position=(col, 0),
        domain=domain_name,
        agent_type="master",
        role="master",
        rule_table=generate_rule_table("master"),
        wip_limit=8,
        knowledge_collections=collections,
        strictness=config.rules.master_veto_threshold,
    )
    grid.place(master_cell)

    # Rows 1+: sub-agents
    for i, sa in enumerate(config.sub_agents):
        row = i + 1
        if row >= grid.height:
            break

        sa_collections = list(collections)
        # Add web collection for this domain
        web_coll = f"{domain_name}-web"
        if web_coll not in sa_collections:
            sa_collections.append(web_coll)

        cell = AgentCell(
            position=(col, row),
            domain=domain_name,
            agent_type=sa.name,
            role="sub",
            rule_table=generate_rule_table("sub", sa.strictness),
            wip_limit=3,
            knowledge_collections=sa_collections,
            strictness=sa.strictness,
        )
        grid.place(cell)


def _place_execution_column(
    grid: AgentGrid,
    col: int,
    height: int,
    project_config: dict[str, str] | None = None,
    min_domain_coverage: int = 2,
):
    """Place execution agents (coder, tester, runner).
    These produce working software, not specifications."""
    exec_types = ["coder", "tester", "runner"]
    for i, etype in enumerate(exec_types):
        if i >= height:
            break
        cell = AgentCell(
            position=(col, i),
            domain="execution",
            agent_type=etype,
            role="execution",
            rule_table=generate_rule_table("execution"),
            wip_limit=6,  # high enough to accept broadcasts from multiple domains
            min_domain_coverage=min_domain_coverage,
            project_config=dict(project_config or {}),
        )
        grid.place(cell)


def _place_critique_column(grid: AgentGrid, col: int, height: int, domains: list[str]):
    """Place critique agents. One per domain for cross-domain review.
    WIP=5 to handle burst arrivals (all masters emit on same tick)."""
    for i, domain_name in enumerate(domains):
        if i >= height:
            break
        cell = AgentCell(
            position=(col, i),
            domain=domain_name,
            agent_type="critique",
            role="critique",
            rule_table=generate_rule_table("critique"),
            wip_limit=5,
            strictness=0.9,
        )
        grid.place(cell)
