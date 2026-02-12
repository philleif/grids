"""Invoke bridge -- connects grid cells to LLM execution and domain validation.

This is the callback passed to tick.run(). When a cell needs to PROCESS,
CRITIQUE, or EMIT, this module routes to the appropriate handler based on
the cell's role and domain.

NKS: the invoke function is the "physics" of the CA. The grid topology
and rule tables are the "rules". Together they produce emergence.
"""

from __future__ import annotations

import json
from typing import Any

from grids.orchestration.grid import AgentCell, CellOutput, WorkFragment
from grids.orchestration.rules import Action
from grids.orchestration.agents import get_llm, _retrieve_context
from grids.knowledge.store import query_store

from langchain_core.messages import HumanMessage, SystemMessage

from rich.console import Console

_console = Console(stderr=True)


def make_invoke_fn(verbose: bool = False):
    """Create the invoke callback for tick.run().

    Returns a function with signature:
        (cell, action, work, neighbors) -> Any
    """

    def invoke_fn(
        cell: AgentCell,
        action: Action,
        work: WorkFragment | None,
        neighbors: list[CellOutput],
    ) -> Any:
        # Class 4 perturbation actions (work may be None)
        if action == Action.CHALLENGE:
            return _invoke_challenge(cell, neighbors, verbose)
        if action == Action.GAP_ANALYSIS:
            return _invoke_gap_analysis(cell, neighbors, verbose)

        if cell.role == "master":
            return _invoke_master(cell, action, work, neighbors, verbose)
        elif cell.role == "research":
            return _invoke_research(cell, action, work, neighbors, verbose)
        elif cell.role == "critique":
            return _invoke_critique(cell, action, work, neighbors, verbose)
        elif cell.role == "execution":
            return _invoke_execution(cell, action, work, neighbors, verbose)
        elif cell.role == "sub":
            return _invoke_sub_agent(cell, action, work, neighbors, verbose)
        return None

    return invoke_fn


def _collect_grid_outputs(grid) -> tuple[dict[str, list[dict]], dict[str, str]]:
    """Gather all cell outputs from a grid, grouped by domain.
    Also extracts project config from execution cells.
    Returns (domain_outputs, project_config)."""
    domain_outputs: dict[str, list[dict]] = {}
    for cell in grid.all_cells():
        if cell.output.content is None:
            continue
        domain_outputs.setdefault(cell.domain, []).append({
            "agent": cell.agent_type,
            "role": cell.role,
            "kind": cell.output.kind,
            "content": cell.output.content,
        })

    project_config = {}
    for cell in grid.cells_by_role("execution"):
        if cell.project_config:
            project_config = cell.project_config
            break

    return domain_outputs, project_config


def _format_domain_outputs(domain_outputs: dict[str, list[dict]], max_per_output: int = 1500) -> str:
    """Format domain outputs into a structured text block."""
    parts = []
    for domain, outputs in sorted(domain_outputs.items()):
        parts.append(f"\n=== DOMAIN: {domain} ===")
        for out in outputs:
            content_str = _content_str(out["content"])[:max_per_output]
            parts.append(f"\n[{out['role']}/{out['agent']}] ({out['kind']}):\n{content_str}")
    return "\n".join(parts)


def consolidate_analysis(grid, brief: str, project_config: dict[str, str] | None = None) -> dict:
    """Phase 1a -> 1b bridge: synthesize domain analyses into a thematic analysis.

    This captures WHAT the domain experts said -- cultural context, design principles,
    UX considerations, editorial voice, strategic positioning. It does NOT specify
    features or screens. That's Phase 1b's job.

    The project_config (type, framework, output_dir) is threaded through so that
    downstream phases respect the seed's technical constraints.

    Runs after Phase 1a grid quiesces. Single LLM call.
    """
    llm = get_llm(temperature=0.3)
    domain_outputs, grid_project_config = _collect_grid_outputs(grid)
    # Prefer explicitly-passed project_config (from seed) over grid-inferred one
    pc = dict(project_config or grid_project_config or {})
    all_domain_output = _format_domain_outputs(domain_outputs)

    project_constraint = ""
    if pc:
        project_constraint = (
            f"\n\nPROJECT CONSTRAINTS (from seed -- these are non-negotiable):\n"
            f"  Type: {pc.get('type', 'not specified')}\n"
            f"  Framework: {pc.get('framework', 'not specified')}\n"
            f"  Output dir: {pc.get('output_dir', 'not specified')}\n"
            f"All recommendations MUST be compatible with these technical constraints.\n"
        )

    messages = [
        SystemMessage(content=(
            "You are a creative director synthesizing expert analyses into a coherent "
            "product vision. Your job is to distill domain expert contributions into "
            "ACTIONABLE PRODUCT INSIGHTS -- not abstract theory.\n\n"
            "For each domain's contribution, extract:\n"
            "1. Key principles that should guide the product (with concrete implications)\n"
            "2. Must-have characteristics (things the product MUST embody)\n"
            "3. Must-avoid pitfalls (things that would undermine the product)\n"
            "4. Reference points (specific products, experiences, or patterns to learn from)\n"
            "5. Tone and voice guidelines (how the product should feel to use)\n\n"
            "Be specific and opinionated. 'The app should feel like texting a smart friend "
            "at 2am' is useful. 'The app should have good UX' is not.\n\n"
            "Output valid JSON with keys: project_config, product_vision, principles (list of "
            "{principle, implication, source_domain}), must_haves, must_avoids, "
            "reference_points, tone_and_voice"
        )),
        HumanMessage(content=(
            f"ORIGINAL BRIEF:\n{brief}\n\n"
            f"{project_constraint}"
            f"DOMAIN EXPERT ANALYSES ({len(domain_outputs)} domains, "
            f"{sum(len(v) for v in domain_outputs.values())} total contributions):\n"
            f"{all_domain_output}\n\n"
            f"Synthesize into a coherent, opinionated product vision."
        )),
    ]

    response = llm.invoke(messages)
    result = _parse_json_or_text(response.content)

    # Ensure project_config is always present in the output, even if the LLM omitted it
    if isinstance(result, dict):
        result["project_config"] = pc

    return result


def consolidate_product_spec(
    grid_1b,
    brief: str,
    project_config: dict[str, str],
    complexity_budget: dict[str, Any] | None = None,
) -> dict:
    """Phase 1b terminal: merge product grid outputs into final implementation spec.

    Takes the Phase 1b grid (product-designer, systems-architect, ux-specifier,
    integration-planner, product-critique) outputs and merges them into a single
    coherent specification ready for Phase 2 execution.

    Two-pass process (GRD-8):
      Pass 1 -- Merge: combine specialist outputs into a unified spec.
      Pass 2 -- Converge: a ruthless product editor cuts scope, resolves
                duplicates, and enforces the complexity budget.
    """
    llm = get_llm(temperature=0.3)
    domain_outputs, _ = _collect_grid_outputs(grid_1b)
    all_output = _format_domain_outputs(domain_outputs, max_per_output=3000)

    project_type = project_config.get("type", "web-app")
    framework = project_config.get("framework", "vanilla JS")
    output_dir = project_config.get("output_dir", "apps/output")

    # --- Pass 1: Merge specialist outputs ---
    messages = [
        SystemMessage(content=(
            "You are a tech lead merging product specifications from multiple specialists "
            "into a single, canonical implementation document.\n\n"
            "You have outputs from:\n"
            "- Product designer (screens and UI elements)\n"
            "- Systems architect (data model and API)\n"
            "- UX specifier (interactions and edge cases)\n"
            "- Integration planner (navigation, data flow, file structure)\n"
            "- Product critique (quality review)\n\n"
            "Merge these into ONE specification. Resolve any conflicts by favoring "
            "the specialist (e.g., data model from systems-architect, screens from "
            "product-designer). Remove duplicates. Ensure consistency.\n\n"
            "The final spec must be implementable without further clarification.\n\n"
            f"CRITICAL -- these technical constraints are NON-NEGOTIABLE (from project seed):\n"
            f"  Target: {project_type}\n"
            f"  Framework: {framework}\n"
            f"  Output directory: {output_dir}\n"
            f"All file paths, imports, dependencies, and architecture decisions MUST use "
            f"the specified framework. Do NOT substitute a different framework.\n\n"
            "Output valid JSON with keys:\n"
            "- overview (string: what this app does, one paragraph)\n"
            "- screens (list of {name, purpose, elements, interactions, states})\n"
            "- data_model (tables with fields, types, relationships)\n"
            "- api_endpoints (list of {method, path, description, request, response})\n"
            "- file_manifest (list of {path, purpose, exports, depends_on})\n"
            "- milestones (list of {name, description, files, acceptance_criteria})\n"
            "- navigation (list of {from, to, trigger})\n"
            "- acceptance_criteria (list of testable conditions)"
        )),
        HumanMessage(content=(
            f"ORIGINAL BRIEF:\n{brief}\n\n"
            f"SPECIALIST OUTPUTS:\n{all_output}\n\n"
            f"Merge into the final implementation specification."
        )),
    ]

    response = llm.invoke(messages)
    merged_spec = _parse_json_or_text(response.content)

    # --- Pass 2: Convergence (GRD-8) ---
    # A ruthless product editor that cuts scope, picks single approaches,
    # and enforces the complexity budget.
    budget = complexity_budget or {}
    max_screens = budget.get("max_screens", 10)
    max_files = budget.get("max_files", 30)

    merged_json = json.dumps(merged_spec, indent=2, default=str) if isinstance(merged_spec, dict) else str(merged_spec)

    converge_messages = [
        SystemMessage(content=(
            "You are a ruthless product editor. Your ONLY job is to CUT.\n\n"
            "You receive a merged product specification that is bloated -- it has too many "
            "screens, duplicate navigation patterns, multiple state management approaches, "
            "and conflicting file paths. Your job is to produce the SMALLEST possible spec "
            "that tests the core product hypothesis.\n\n"
            "RULES:\n"
            f"1. SCREEN BUDGET: Maximum {max_screens} screens. Cut everything else. "
            f"   Merge screens that serve similar purposes. Kill nice-to-haves.\n"
            f"2. ONE NAVIGATION PATTERN: Pick the single best navigation approach. "
            f"   If the spec has Expo Router AND React Navigation, pick ONE. "
            f"   If it has file-based routing AND manual stack config, pick ONE.\n"
            f"3. ONE STATE MANAGEMENT: Pick a single state management approach. "
            f"   No parallel stores. One pattern, applied consistently.\n"
            f"4. FILE BUDGET: Maximum {max_files} route/screen files. Merge or eliminate "
            f"   the rest. Utility/config files don't count against this limit.\n"
            f"5. NO DUPLICATE PATHS: Every file path must be unique. If two specialists "
            f"   proposed different files for the same purpose, pick one.\n"
            f"6. RESOLVE CONTRADICTIONS: If specialists disagree, pick the simpler option.\n\n"
            f"TECHNICAL CONSTRAINTS (non-negotiable):\n"
            f"  Target: {project_type}\n"
            f"  Framework: {framework}\n"
            f"  Output directory: {output_dir}\n\n"
            "Output the same JSON structure as the input, but leaner. Also add:\n"
            "- convergence_decisions (list of {decision, rationale, cut_items}): "
            "  document what you cut and why\n"
            "- navigation_pattern (string): the single chosen navigation approach\n"
            "- state_management_pattern (string): the single chosen state approach"
        )),
        HumanMessage(content=(
            f"ORIGINAL BRIEF (this is what the product MUST do -- cut everything else):\n"
            f"{brief}\n\n"
            f"MERGED SPEC TO CONVERGE:\n{merged_json}\n\n"
            f"Cut this down to a focused, buildable spec. Budget: {max_screens} screens, "
            f"{max_files} route files, ONE navigation pattern, ONE state pattern."
        )),
    ]

    converge_response = llm.invoke(converge_messages)
    result = _parse_json_or_text(converge_response.content)

    # Ensure project_config is embedded in the spec so Phase 2 cells see it
    if isinstance(result, dict):
        result["project_config"] = {
            "type": project_type,
            "framework": framework,
            "output_dir": output_dir,
        }
        result["complexity_budget"] = {
            "max_screens": max_screens,
            "max_files": max_files,
        }

    _console.print(
        f"  [bold green]Convergence pass complete[/bold green]: "
        f"budget={max_screens} screens, {max_files} files"
    )

    return result


def _invoke_master(
    cell: AgentCell,
    action: Action,
    work: WorkFragment | None,
    neighbors: list[CellOutput],
    verbose: bool,
) -> Any:
    """Master agent: decompose briefs into work specs, validate artifacts."""
    if work is None:
        return None

    llm = get_llm(temperature=0.4)
    context = _get_cell_context(cell, work.content if isinstance(work.content, str) else str(work.content))

    if action == Action.PROCESS:
        # Decompose brief into work specification
        messages = [
            SystemMessage(content=_master_system_prompt(cell, context)),
            HumanMessage(content=(
                f"Decompose this into a structured work specification with concrete, "
                f"buildable components. Your domain is: {cell.domain}.\n\n"
                f"Input:\n{_content_str(work.content)}\n\n"
                f"Output JSON with: title, description, components (list of {{name, description, type}}), "
                f"acceptance_criteria (list of testable criteria)."
            )),
        ]
        response = llm.invoke(messages)
        return _parse_json_or_text(response.content)

    elif action == Action.CRITIQUE:
        # Validate an artifact from a neighbor
        neighbor_context = _neighbor_summary(neighbors)
        messages = [
            SystemMessage(content=_master_system_prompt(cell, context)),
            HumanMessage(content=(
                f"Validate this work product from your domain perspective ({cell.domain}).\n\n"
                f"Work product:\n{_content_str(work.content)}\n\n"
                f"Neighbor context:\n{neighbor_context}\n\n"
                f"Score 0-100 and output JSON: "
                f"{{\"score\": N, \"verdict\": \"approve\"|\"iterate\", \"feedback\": \"...\"}}"
            )),
        ]
        response = llm.invoke(messages)
        return _normalize_score(_parse_json_or_text(response.content))

    elif action == Action.EMIT:
        return work.content

    return None


def _invoke_research(
    cell: AgentCell,
    action: Action,
    work: WorkFragment | None,
    neighbors: list[CellOutput],
    verbose: bool,
) -> Any:
    """Research agent: retrieve knowledge from ChromaDB, synthesize findings."""
    if work is None:
        return None

    query_text = _content_str(work.content)[:500]
    findings = []

    for coll in cell.knowledge_collections:
        try:
            hits = query_store(query_text, coll, n_results=3)
            for hit in hits:
                findings.append({
                    "source": coll,
                    "section": hit.get("metadata", {}).get("section", ""),
                    "excerpt": hit["text"][:400],
                    "relevance": 1 - (hit.get("distance", 0.5) or 0.5),
                })
        except Exception:
            continue

    if not findings:
        return None

    # Synthesize findings with LLM
    llm = get_llm(temperature=0.3)
    findings_text = "\n---\n".join(
        f"[{f['source']}] (relevance: {f['relevance']:.2f})\n{f['excerpt']}"
        for f in findings[:8]
    )
    messages = [
        SystemMessage(content=(
            f"You are a research agent for the {cell.domain} domain. "
            f"Synthesize these knowledge fragments into actionable findings. "
            f"Focus on principles that directly apply to the task at hand. "
            f"Be concrete and specific, not abstract."
        )),
        HumanMessage(content=(
            f"Task context:\n{query_text}\n\n"
            f"Knowledge fragments:\n{findings_text}\n\n"
            f"Synthesize into 3-5 key findings, each with: principle, source, application."
        )),
    ]
    response = llm.invoke(messages)
    return _parse_json_or_text(response.content)


def _check_relevance(cell: AgentCell, work_content: str) -> float | None:
    """Check if work content is relevant to this cell's specialty.
    Returns a relevance score 0-1, or None if relevance can't be computed.
    Uses embedding similarity between work content and agent's knowledge collections."""
    if not cell.knowledge_collections:
        return None
    try:
        hits = query_store(work_content[:500], cell.knowledge_collections[0], n_results=1)
        if hits:
            distance = hits[0].get("distance", 1.0)
            return max(0.0, 1.0 - (distance or 1.0))
    except Exception:
        pass
    return None


def _invoke_sub_agent(
    cell: AgentCell,
    action: Action,
    work: WorkFragment | None,
    neighbors: list[CellOutput],
    verbose: bool,
) -> Any:
    """Domain sub-agent: process work through specialized lens.
    Phase 1b product cells get specialized prompts focused on concrete features."""
    if work is None:
        return None

    # Phase 1b product cells: specialized prompts for product specification
    if cell.domain == "product":
        return _invoke_product_cell(cell, action, work, neighbors, verbose)

    # Phase 2 consultant cells receiving artifacts: domain-specific review mode
    if cell.agent_type == "consultant" and work and work.kind in ("artifact", "code"):
        return _invoke_consultant_review(cell, action, work, neighbors, verbose)

    # Relevance gating: skip scoring if work is outside this agent's expertise
    if action == Action.CRITIQUE:
        relevance = _check_relevance(cell, _content_str(work.content))
        if relevance is not None and relevance < 0.15:
            if verbose:
                _console.print(
                    f"  [dim]SKIP: {cell.domain}/{cell.agent_type} relevance "
                    f"{relevance:.2f} below threshold for this work[/dim]"
                )
            return None

    llm = get_llm(temperature=0.5)
    context = _get_cell_context(cell, _content_str(work.content)[:300])
    neighbor_context = _neighbor_summary(neighbors)

    if action == Action.CRITIQUE:
        messages = [
            SystemMessage(content=(
                f"You are the {cell.agent_type} specialist in the {cell.domain} domain. "
                f"Your strictness level is {cell.strictness} (higher = stricter).\n\n"
                f"Domain knowledge:\n{context}\n\n"
                f"Evaluate ONLY your narrow specialty. Score 0-100."
            )),
            HumanMessage(content=(
                f"Review this from your {cell.agent_type} perspective:\n\n"
                f"{_content_str(work.content)}\n\n"
                f"Neighbor context:\n{neighbor_context}\n\n"
                f"Output JSON: {{\"score\": N, \"verdict\": \"pass\"|\"fail\", "
                f"\"feedback\": \"specific actionable feedback\"}}"
            )),
        ]
    else:
        messages = [
            SystemMessage(content=(
                f"You are the {cell.agent_type} specialist in the {cell.domain} domain. "
                f"You contribute your specific expertise to collaborative work.\n\n"
                f"Domain knowledge:\n{context}"
            )),
            HumanMessage(content=(
                f"Apply your {cell.agent_type} expertise to this:\n\n"
                f"{_content_str(work.content)}\n\n"
                f"Neighbor context:\n{neighbor_context}\n\n"
                f"Contribute specific, actionable improvements or specifications "
                f"from your specialist perspective."
            )),
        ]

    response = llm.invoke(messages)
    return _normalize_score(_parse_json_or_text(response.content))


# --- Phase 1b: Product specification cells ---

PRODUCT_CELL_PROMPTS = {
    "product-designer": {
        "system": (
            "You are a product designer specifying a mobile app. Your output is SCREENS "
            "and USER FLOWS, not theory or principles.\n\n"
            "For each screen, specify:\n"
            "- Screen name and one-sentence purpose\n"
            "- Every UI element (buttons, inputs, cards, lists, modals)\n"
            "- What each element does when tapped/interacted with\n"
            "- Where each action navigates to\n"
            "- Empty state, loading state, error state\n"
            "- What data is displayed and where it comes from\n\n"
            "Be EXHAUSTIVE. If you don't specify it, it won't get built.\n"
            "Output JSON: {\"screens\": [{\"name\", \"purpose\", \"elements\": [{\"type\", "
            "\"label\", \"action\", \"navigates_to\"}], \"states\": {\"empty\", \"loading\", "
            "\"error\", \"populated\"}, \"data_sources\": []}]}"
        ),
        "human": (
            "Design every screen for this app. Think through the complete user journey "
            "from first open to daily use. Include onboarding, core loop, settings, "
            "and monetization screens.\n\n"
            "Input:\n{content}\n\nNeighbor specs:\n{neighbors}\n\n"
            "Output the complete screen inventory."
        ),
    },
    "systems-architect": {
        "system": (
            "You are a systems architect specifying the technical backbone of a mobile app.\n\n"
            "Produce:\n"
            "1. DATA MODEL: Every table/entity with fields, types, relationships, and indexes.\n"
            "   If Supabase: include RLS policies and realtime subscriptions needed.\n"
            "2. API ENDPOINTS: Method, path, request body, response shape, auth requirements.\n"
            "3. AUTHENTICATION: Flow, providers, session management.\n"
            "4. REAL-TIME: What needs websockets/subscriptions vs polling.\n"
            "5. PAYMENTS: Subscription tiers, what's gated, payment flow.\n"
            "6. THIRD-PARTY: External services needed (SMS, voice, push notifications).\n\n"
            "Output JSON: {\"data_model\": {\"tables\": [...]}, \"api_endpoints\": [...], "
            "\"auth\": {...}, \"realtime\": [...], \"payments\": {...}, \"third_party\": [...]}"
        ),
        "human": (
            "Design the complete technical architecture for this app. Every table, "
            "every endpoint, every integration.\n\n"
            "Input:\n{content}\n\nNeighbor specs:\n{neighbors}\n\n"
            "Output the complete technical specification."
        ),
    },
    "ux-specifier": {
        "system": (
            "You are a UX specifier focused on INTERACTIONS and EDGE CASES. "
            "You don't design screens (the product designer does that). "
            "You specify HOW things behave.\n\n"
            "For each interaction, specify:\n"
            "- Exact gesture/input (tap, long press, swipe, type)\n"
            "- Animation/transition (slide, fade, modal, bottom sheet)\n"
            "- Feedback (haptic, sound, visual change)\n"
            "- Error handling (what if it fails, what does the user see)\n"
            "- Accessibility (screen reader label, minimum tap target)\n"
            "- Edge cases (empty input, too long, no network, rate limits)\n\n"
            "Output JSON: {\"interactions\": [{\"trigger\", \"screen\", \"element\", "
            "\"behavior\", \"animation\", \"feedback\", \"error_handling\", \"accessibility\", "
            "\"edge_cases\": []}]}"
        ),
        "human": (
            "Specify every interaction behavior, animation, and edge case for this app. "
            "Consider the screens and architecture your neighbors have designed.\n\n"
            "Input:\n{content}\n\nNeighbor specs:\n{neighbors}\n\n"
            "Output the complete interaction specification."
        ),
    },
    "integration-planner": {
        "system": (
            "You are an integration planner. You specify how screens CONNECT "
            "and how data FLOWS between features.\n\n"
            "Produce:\n"
            "1. NAVIGATION MAP: Screen-to-screen connections with trigger and transition type.\n"
            "2. DATA FLOW: How data moves from input to storage to display.\n"
            "   For each flow: source -> transform -> destination.\n"
            "3. STATE MANAGEMENT: What state is global vs local, what persists.\n"
            "4. NOTIFICATION FLOW: What triggers push/in-app notifications.\n"
            "5. FILE STRUCTURE: Map features to file paths. Which component lives where.\n\n"
            "Output JSON: {\"navigation\": [{\"from\", \"to\", \"trigger\", \"transition\"}], "
            "\"data_flows\": [{\"name\", \"source\", \"transform\", \"destination\"}], "
            "\"state_management\": {...}, \"notifications\": [...], "
            "\"file_structure\": [{\"path\", \"purpose\", \"contains\": []}]}"
        ),
        "human": (
            "Map out every connection between screens, every data flow, "
            "and the file structure for the codebase. Consider what the product "
            "designer and systems architect have specified.\n\n"
            "Input:\n{content}\n\nNeighbor specs:\n{neighbors}\n\n"
            "Output the complete integration plan."
        ),
    },
    "scope-convergence": {
        "system": (
            "You are a scope convergence agent. Your job is NOT to add features -- "
            "it is to REMOVE them. You identify redundancies, contradictions, and "
            "scope creep in the product spec being built by your neighbors.\n\n"
            "You act as a ruthless editor:\n"
            "1. IDENTIFY REDUNDANCIES: duplicate screens serving the same purpose, "
            "   parallel navigation systems, multiple state management approaches.\n"
            "2. IDENTIFY CONTRADICTIONS: conflicting file paths, incompatible data models, "
            "   navigation patterns that don't connect.\n"
            "3. ENFORCE BUDGET: the product has a screen budget and file budget. "
            "   Flag anything that exceeds it.\n"
            "4. PICK WINNERS: when two approaches exist for the same thing, recommend "
            "   which one to keep and which to cut.\n\n"
            "You are different from the product-critique cell (which evaluates quality). "
            "You evaluate SCOPE. Quality can be perfect and scope can still be wrong.\n\n"
            "Output JSON: {\"redundancies\": [{\"items\": [...], \"recommendation\": \"keep X, cut Y\", "
            "\"rationale\": \"...\"}], \"contradictions\": [{\"conflict\": \"...\", \"resolution\": \"...\"}], "
            "\"budget_violations\": [{\"metric\": \"screens|files|patterns\", \"count\": N, "
            "\"budget\": N, \"cuts\": [...]}], \"scope_verdict\": \"converged|needs_cuts\", "
            "\"proposed_cuts\": [\"specific item to remove\"]}"
        ),
        "human": (
            "Review what your neighbors have produced so far. Identify all redundancies, "
            "contradictions, and budget violations. Be specific about what to cut.\n\n"
            "Input:\n{content}\n\nNeighbor specs:\n{neighbors}\n\n"
            "Output your scope analysis."
        ),
    },
}


def _invoke_product_cell(
    cell: AgentCell,
    action: Action,
    work: WorkFragment,
    neighbors: list[CellOutput],
    verbose: bool,
) -> Any:
    """Phase 1b product cell: produce concrete features, screens, or architecture."""
    llm = get_llm(temperature=0.4)
    neighbor_context = _neighbor_summary(neighbors, max_items=6)
    content = _content_str(work.content)

    prompts = PRODUCT_CELL_PROMPTS.get(cell.agent_type)
    if not prompts:
        # Fallback for unknown product cell types
        prompts = PRODUCT_CELL_PROMPTS.get("product-designer")

    # Get project config
    project_type = cell.project_config.get("type", "mobile-app")
    framework = cell.project_config.get("framework", "expo + react native")

    system_prompt = prompts["system"] + f"\n\nTarget: {project_type}\nFramework: {framework}"

    # Inject complexity budget for scope-convergence cells (GRD-8)
    if cell.agent_type == "scope-convergence":
        budget_json = cell.project_config.get("complexity_budget", "")
        if budget_json:
            try:
                budget = json.loads(budget_json)
            except (json.JSONDecodeError, TypeError):
                budget = {}
        else:
            budget = {}
        max_screens = budget.get("max_screens", 10)
        max_files = budget.get("max_files", 30)
        system_prompt += (
            f"\n\nCOMPLEXITY BUDGET (from seed config -- enforce these limits):\n"
            f"  Max screens: {max_screens}\n"
            f"  Max route/screen files: {max_files}\n"
        )

    human_prompt = prompts["human"].format(content=content, neighbors=neighbor_context)

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_prompt),
    ]

    response = llm.invoke(messages)
    return _parse_json_or_text(response.content)


def _invoke_consultant_review(
    cell: AgentCell,
    action: Action,
    work: WorkFragment,
    neighbors: list[CellOutput],
    verbose: bool,
) -> Any:
    """Domain consultant reviewing execution artifacts.

    Consultant cells apply domain-specific principles to review generated code.
    Output is an enrichment fragment that propagates back to execution cells
    for potential patching.

    Design-domain consultants emit CONCRETE component specs (GRD-9) instead of
    generic recommendations, including specific UI kit components and layout
    compositions per screen.
    """
    if cell.domain == "design":
        return _invoke_design_consultant_review(cell, action, work, neighbors, verbose)

    llm = get_llm(temperature=0.3)
    context = _get_cell_context(cell, _content_str(work.content)[:300])
    neighbor_context = _neighbor_summary(neighbors)

    # Extract domain context from cell's previous work (injected during seed_phase2)
    domain_context = _extract_domain_context(cell)

    messages = [
        SystemMessage(content=(
            f"You are a domain consultant for {cell.domain}. You review generated code "
            f"and artifacts through the lens of your domain expertise.\n\n"
            f"Your job is NOT generic code review. You evaluate whether the artifact "
            f"embodies the {cell.domain} domain principles correctly. Look for:\n"
            f"- Domain principles that are violated or missing\n"
            f"- Opportunities to better express domain values in the implementation\n"
            f"- Specific, actionable patches grounded in your domain knowledge\n\n"
            f"Domain knowledge:\n{context}\n\n"
            f"Domain-specific context:\n{domain_context if domain_context else '(use knowledge base)'}"
        )),
        HumanMessage(content=(
            f"Review this execution artifact from your {cell.domain} expertise:\n\n"
            f"{_content_str(work.content)}\n\n"
            f"Neighbor context:\n{neighbor_context}\n\n"
            f"Output JSON: {{\"domain\": \"{cell.domain}\", "
            f"\"review_type\": \"domain_consultation\", "
            f"\"findings\": [{{\"principle\": \"...\", \"issue\": \"...\", \"suggestion\": \"...\"}}], "
            f"\"patches\": [{{\"target\": \"file or component\", \"change\": \"specific modification\", "
            f"\"rationale\": \"domain principle this serves\"}}], "
            f"\"overall_alignment\": 0.0-1.0}}"
        )),
    ]

    response = llm.invoke(messages)
    return _parse_json_or_text(response.content)


def _extract_domain_context(cell: AgentCell) -> str:
    """Extract domain-specific context from a consultant cell's previous output."""
    domain_context = ""
    if cell.output.content and isinstance(cell.output.content, dict):
        principles = cell.output.content.get("domain_principles", [])
        if principles:
            domain_context = "\n".join(
                f"- {p.get('principle', '')}: {p.get('implication', '')}"
                for p in principles
            )
        must_haves = cell.output.content.get("must_haves", [])
        if must_haves:
            domain_context += "\nMust-haves: " + ", ".join(str(m) for m in must_haves)
        must_avoids = cell.output.content.get("must_avoids", [])
        if must_avoids:
            domain_context += "\nMust-avoids: " + ", ".join(str(m) for m in must_avoids)
    return domain_context


# --- UI kit component catalogs (GRD-9) ---

UI_KIT_CATALOGS: dict[str, str] = {
    "tamagui": (
        "=== TAMAGUI COMPONENT CATALOG ===\n"
        "Layout: XStack, YStack, ZStack, ScrollView, Separator, Spacer\n"
        "Typography: H1, H2, H3, H4, H5, H6, Paragraph, SizableText, Label\n"
        "Forms: Button, Input, TextArea, Checkbox, RadioGroup, Select, Slider, Switch, ToggleGroup\n"
        "Feedback: Spinner, Progress, Toast (via @tamagui/toast)\n"
        "Overlay: Dialog, AlertDialog, Popover, Sheet (bottom sheet), Tooltip\n"
        "Navigation: Tabs, ListItem, Group, Accordion\n"
        "Media: Avatar, Card, Image, LinearGradient\n"
        "Animation: AnimatePresence (enter/exit animations), animation prop on any component\n"
        "  - Animation presets: bouncy, lazy, quick, quicker, slowest\n"
        "  - Supports enterStyle, exitStyle, pressStyle, hoverStyle, focusStyle\n"
        "Theming: Theme, useTheme, ThemeableStack, tokens ($size, $color, $space, $radius, $zIndex)\n"
        "  - Named themes: light, dark, plus sub-themes (e.g., 'blue', 'red', 'green')\n"
        "  - Token access: $color.accent, $size.md, $space.4, $radius.md\n"
        "  - Font tokens: $heading, $body, $mono\n"
        "Advanced: Adapt (responsive breakpoints), Square, Circle, Anchor, Unspaced\n"
        "Key patterns:\n"
        "  - Sheet: pull-up bottom sheet with Handle, Overlay, Frame, ScrollView children\n"
        "  - Dialog: Dialog.Trigger + Dialog.Portal + Dialog.Overlay + Dialog.Content\n"
        "  - Toast: ToastProvider + Toast + Toast.Title + Toast.Description + Toast.Action\n"
        "  - AnimatePresence: wrap conditional renders for enter/exit transitions\n"
        "  - Tabs: Tabs + Tabs.List + Tabs.Tab + Tabs.Content\n"
        "  - Card: Card + Card.Header + Card.Footer with elevate/bordered props\n"
    ),
}

# Fallback for unknown frameworks
UI_KIT_CATALOG_FALLBACK = (
    "No specific component catalog available. Specify components by name, "
    "props, and layout structure using the project's UI framework."
)


def _detect_ui_framework(cell: AgentCell) -> str | None:
    """Detect the UI framework from the cell's project config or neighbors."""
    framework = cell.project_config.get("framework", "")
    fw_lower = framework.lower()
    for kit_name in UI_KIT_CATALOGS:
        if kit_name in fw_lower:
            return kit_name
    # Check neighbor outputs for framework hints
    if cell.output.content and isinstance(cell.output.content, dict):
        pc = cell.output.content.get("project_config", {})
        if isinstance(pc, dict):
            fw = pc.get("framework", "").lower()
            for kit_name in UI_KIT_CATALOGS:
                if kit_name in fw:
                    return kit_name
    return None


def _get_ui_catalog(cell: AgentCell) -> str:
    """Get the UI component catalog for the cell's project framework."""
    kit = _detect_ui_framework(cell)
    if kit and kit in UI_KIT_CATALOGS:
        return UI_KIT_CATALOGS[kit]
    return UI_KIT_CATALOG_FALLBACK


def _invoke_design_consultant_review(
    cell: AgentCell,
    action: Action,
    work: WorkFragment,
    neighbors: list[CellOutput],
    verbose: bool,
) -> Any:
    """Design-domain consultant: emit CONCRETE component specs (GRD-9).

    Instead of generic "ensure visual hierarchy" feedback, produces structured
    JSON with specific UI kit components, props, layout compositions, and
    theme token overrides per screen.
    """
    llm = get_llm(temperature=0.3)
    context = _get_cell_context(cell, _content_str(work.content)[:300])
    neighbor_context = _neighbor_summary(neighbors)
    domain_context = _extract_domain_context(cell)
    ui_catalog = _get_ui_catalog(cell)
    kit_name = _detect_ui_framework(cell) or "the project's UI framework"

    messages = [
        SystemMessage(content=(
            f"You are a design consultant producing CONCRETE component specifications "
            f"for a {kit_name}-based application. You do NOT give generic advice like "
            f"'ensure visual hierarchy' or 'improve spacing'. Instead, you specify EXACTLY "
            f"which components to use, what props to set, and how to compose them.\n\n"
            f"AVAILABLE COMPONENTS:\n{ui_catalog}\n\n"
            f"Design knowledge:\n{context}\n\n"
            f"Domain context:\n{domain_context if domain_context else '(use design knowledge base)'}\n\n"
            f"Your output must be structured JSON that the coder can consume directly "
            f"without interpretation. Every suggestion must name a specific component "
            f"from the catalog above."
        )),
        HumanMessage(content=(
            f"Review this execution artifact and produce concrete component specs:\n\n"
            f"{_content_str(work.content)}\n\n"
            f"Neighbor context:\n{neighbor_context}\n\n"
            f"Output JSON with this EXACT structure:\n"
            f"{{\n"
            f"  \"domain\": \"design\",\n"
            f"  \"review_type\": \"design_component_spec\",\n"
            f"  \"ui_framework\": \"{kit_name}\",\n"
            f"  \"screen_specs\": [\n"
            f"    {{\n"
            f"      \"screen\": \"ScreenName\",\n"
            f"      \"layout\": \"ScrollView > YStack\" or similar composition,\n"
            f"      \"components\": [\n"
            f"        {{\n"
            f"          \"type\": \"Sheet|Dialog|AnimatePresence|Card|Tabs|etc\",\n"
            f"          \"purpose\": \"what this component does in context\",\n"
            f"          \"props\": {{\"key\": \"value\"}},\n"
            f"          \"children\": [\"child components or content\"],\n"
            f"          \"animation\": \"bouncy|lazy|quick|null\",\n"
            f"          \"styles\": {{\"enterStyle\": {{}}, \"pressStyle\": {{}}}}\n"
            f"        }}\n"
            f"      ],\n"
            f"      \"tokens\": {{\"headerFont\": \"$heading\", \"accentColor\": \"$color.accent\"}},\n"
            f"      \"theme\": \"light|dark|blue|etc or null\"\n"
            f"    }}\n"
            f"  ],\n"
            f"  \"global_tokens\": {{\n"
            f"    \"typography\": {{\"heading\": \"...\", \"body\": \"...\"}},\n"
            f"    \"colors\": {{\"accent\": \"...\", \"background\": \"...\"}},\n"
            f"    \"spacing\": {{\"page_padding\": \"...\", \"section_gap\": \"...\"}},\n"
            f"    \"animation\": {{\"default_preset\": \"bouncy|lazy|quick\"}}\n"
            f"  }},\n"
            f"  \"patches\": [\n"
            f"    {{\n"
            f"      \"target\": \"file or component path\",\n"
            f"      \"change\": \"specific code-level modification\",\n"
            f"      \"rationale\": \"design principle this serves\",\n"
            f"      \"component\": \"specific {kit_name} component to use\"\n"
            f"    }}\n"
            f"  ],\n"
            f"  \"overall_alignment\": 0.0-1.0\n"
            f"}}\n\n"
            f"RULES:\n"
            f"- Every component in screen_specs MUST exist in the catalog above\n"
            f"- At least one screen MUST use a non-trivial component (Sheet, Dialog, "
            f"Toast, AnimatePresence, Tabs, or Accordion)\n"
            f"- Props must be valid for the specified component\n"
            f"- Token references must use $ prefix ($color.X, $size.X, $space.X)\n"
            f"- Be harsh: if a screen is visually empty, spec the components it needs"
        )),
    ]

    response = llm.invoke(messages)
    return _parse_json_or_text(response.content)


def _invoke_critique(
    cell: AgentCell,
    action: Action,
    work: WorkFragment | None,
    neighbors: list[CellOutput],
    verbose: bool,
) -> Any:
    """Critique agent: evaluate quality, coherence, approve or iterate."""
    if work is None:
        return None

    llm = get_llm(temperature=0.2)
    neighbor_context = _neighbor_summary(neighbors)

    messages = [
        SystemMessage(content=(
            f"You are a critique agent for the {cell.domain} domain. "
            f"Your job: evaluate whether work is ready to ship or needs iteration.\n"
            f"Strictness: {cell.strictness}. Be proportionally rigorous.\n\n"
            f"Score on: correctness, completeness, quality, adherence to domain principles.\n"
            f"Consider what your neighbors have produced for context."
        )),
        HumanMessage(content=(
            f"Evaluate:\n{_content_str(work.content)}\n\n"
            f"Iteration: {work.iteration}\n"
            f"Neighbor outputs:\n{neighbor_context}\n\n"
            f"Output JSON: {{\"score\": 0-100, \"verdict\": \"approve\"|\"iterate\", "
            f"\"feedback\": \"specific revision instructions if iterating\"}}"
        )),
    ]

    response = llm.invoke(messages)
    result = _normalize_score(_parse_json_or_text(response.content))

    # If iterating, the tick scheduler will handle re-queuing with Reinertsen economics
    return result


def _build_tester_prompt(work: WorkFragment | None, project_type: str, framework: str) -> str:
    """Build tester system prompt, injecting acceptance criteria when available."""
    base = (
        f"You are a QA engineer. Verify the code produced by the coder agent.\n"
        f"Project type: {project_type}. Framework: {framework}.\n"
        f"Check: syntax validity, import correctness, basic functionality.\n"
    )

    # Extract acceptance criteria if the work fragment carries them
    criteria: list = []
    if work and isinstance(work.content, dict):
        criteria = work.content.get("acceptance_criteria", [])

    if criteria:
        checklist = "\n".join(f"  - [ ] {c}" for c in criteria)
        base += (
            f"\nACCEPTANCE CRITERIA (from product spec -- evaluate each one explicitly):\n"
            f"{checklist}\n\n"
            f"For EACH criterion above, state whether it passes or fails with evidence.\n"
            f"Your verdict must be grounded in these criteria, not a generic assessment.\n"
        )

    base += (
        f"Output JSON: {{\"tests\": [{{\"name\": \"...\", \"pass\": true/false, \"details\": \"...\"}}], "
        f"\"criteria_results\": [{{\"criterion\": \"...\", \"pass\": true/false, \"evidence\": \"...\"}}], "
        f"\"summary\": \"...\", \"verdict\": \"pass\"|\"fail\"}}"
    )
    return base


def _search_current_practices(framework: str, project_type: str) -> str:
    """Search the web for current versions and best practices of project dependencies.

    Execution cells call this before coding so they use up-to-date APIs
    rather than relying on the LLM's training data cutoff.
    """
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return ""

    # Extract individual technology names from the framework string
    # e.g. "expo react-native + tamagui" -> ["expo", "react-native", "tamagui"]
    techs = [t.strip().strip("+").strip() for t in framework.replace("+", ",").split(",")]
    techs = [t for t in techs if t and len(t) > 1]

    # Also search for the project type pattern
    queries = [f"{t} latest version getting started 2026" for t in techs]
    if project_type:
        queries.append(f"{project_type} {framework} best practices 2026")

    results = []
    try:
        with DDGS() as ddgs:
            for q in queries[:5]:  # cap at 5 queries to limit latency
                for r in ddgs.text(q, max_results=3):
                    results.append(f"[{r.get('title', '')}] {r.get('body', '')[:200]}")
    except Exception as e:
        _console.print(f"[dim]Web search for current practices failed: {e}[/dim]")
        return ""

    if not results:
        return ""

    summary = "\n".join(results[:10])
    _console.print(f"  [dim]Fetched {len(results)} current-practice snippets for: {', '.join(techs)}[/dim]")
    return (
        f"\n\nCURRENT BEST PRACTICES (from web search -- use these versions and patterns, "
        f"not your training data defaults):\n{summary}\n"
    )


def _invoke_execution(
    cell: AgentCell,
    action: Action,
    work: WorkFragment | None,
    neighbors: list[CellOutput],
    verbose: bool,
) -> Any:
    """Execution agent: turn specs into working software.
    The goal is a usable interactive program, not a description of one."""
    if work is None:
        return None

    # Handle PATCH action: apply late enrichment to existing artifact
    if action == Action.PATCH:
        return _invoke_execution_patch(cell, work, neighbors, verbose)

    llm = get_llm(temperature=0.4)
    neighbor_context = _neighbor_summary(neighbors)

    # Project config from seed (e.g., project_type, framework, output_dir)
    project_type = cell.project_config.get("type", "web-app")
    framework = cell.project_config.get("framework", "vanilla JS + HTML")
    output_dir = cell.project_config.get("output_dir", "apps/output")

    # Search for current versions and best practices (first iteration only,
    # to avoid redundant searches on rework loops)
    current_practices = ""
    if work and work.iteration == 0 and cell.agent_type in ("coder", "runner"):
        current_practices = _search_current_practices(framework, project_type)

    # Inject UI component catalog so the coder knows what's available (GRD-9)
    ui_catalog_section = ""
    fw_lower = framework.lower()
    for kit_name, catalog in UI_KIT_CATALOGS.items():
        if kit_name in fw_lower:
            ui_catalog_section = (
                f"\n\nAVAILABLE UI COMPONENTS ({kit_name.upper()}):\n{catalog}\n"
                f"USE these components by name. Do not invent custom replacements "
                f"for components that already exist in the catalog above. Prefer "
                f"Sheet over custom bottom drawers, Dialog over custom modals, "
                f"AnimatePresence for enter/exit transitions, Tabs for tabbed content, "
                f"Card for card layouts, Toast for ephemeral notifications.\n"
            )
            break

    agent_focus = {
        "coder": (
            f"You are a software engineer. Your job is to produce WORKING CODE FILES "
            f"that implement the design specification as interactive software.\n"
            f"Project type: {project_type}. Framework: {framework}.\n"
            f"Output dir: {output_dir}.\n\n"
            f"Output JSON: {{\"files\": [{{\"path\": \"relative/path.ext\", \"content\": \"...\"}}], "
            f"\"entrypoint\": \"path/to/main\", \"dependencies\": [\"pkg1\", \"pkg2\"]}}\n\n"
            f"The goal is a usable interactive program that a user can run to create/manipulate "
            f"assets described in the specification. NOT the assets themselves. NOT a description "
            f"of a program. WORKING CODE."
            f"{ui_catalog_section}"
            f"{current_practices}"
        ),
        "tester": _build_tester_prompt(work, project_type, framework),
        "runner": (
            f"You are a build/deploy engineer. Assemble the final application.\n"
            f"Project type: {project_type}. Framework: {framework}.\n"
            f"Output dir: {output_dir}.\n"
            f"Produce: package.json/Cargo.toml/pyproject.toml as needed, build scripts, "
            f"configuration files, README with run instructions.\n"
            f"For Expo/React Native projects you MUST include: app.json, babel.config.js, "
            f"metro.config.js, tsconfig.json, and a valid package.json with all dependencies.\n"
            f"Output JSON: {{\"files\": [{{\"path\": \"...\", \"content\": \"...\"}}], "
            f"\"build_command\": \"...\", \"run_command\": \"...\"}}"
            f"{current_practices}"
        ),
    }

    messages = [
        SystemMessage(content=(
            f"{agent_focus.get(cell.agent_type, 'Execute the specified work.')}\n\n"
            f"If you see critique feedback from neighbors, incorporate it.\n"
            f"If you see rework requests, address the specific feedback."
        )),
        HumanMessage(content=(
            f"Specification:\n{_content_str(work.content)}\n\n"
            f"Iteration: {work.iteration}\n"
            f"Neighbor context:\n{neighbor_context}\n\n"
            f"Produce the working software."
        )),
    ]

    response = llm.invoke(messages)
    return _parse_json_or_text(response.content)


def _invoke_execution_patch(
    cell: AgentCell,
    work: WorkFragment,
    neighbors: list[CellOutput],
    verbose: bool,
) -> Any:
    """Apply late-arriving domain enrichment to an existing artifact.
    Targeted modifications only -- do not rewrite from scratch.
    Design enrichments (GRD-9) include concrete component specs that should
    be applied directly using the UI kit's actual components."""
    llm = get_llm(temperature=0.3)
    existing = _content_str(cell.output.content) if cell.output.content else "(no existing artifact)"
    enrichment = _content_str(work.content)
    neighbor_context = _neighbor_summary(neighbors)

    # Inject UI catalog when patching with design enrichment (GRD-9)
    ui_catalog_hint = ""
    is_design_enrichment = (
        isinstance(work.content, dict)
        and work.content.get("review_type") == "design_component_spec"
    )
    if is_design_enrichment:
        framework = cell.project_config.get("framework", "")
        for kit_name, catalog in UI_KIT_CATALOGS.items():
            if kit_name in framework.lower():
                ui_catalog_hint = (
                    f"\n\nThe design enrichment references specific {kit_name} components. "
                    f"Here is the component catalog for reference:\n{catalog}\n"
                    f"Apply the screen_specs and patches using these exact component names.\n"
                )
                break

    messages = [
        SystemMessage(content=(
            "You are patching an existing software artifact with new domain insights. "
            "Make targeted modifications only. Do not rewrite from scratch. "
            "Preserve the existing structure and only incorporate the new enrichment. "
            "Output the same JSON format as the original artifact with changes applied."
            f"{ui_catalog_hint}"
        )),
        HumanMessage(content=(
            f"Existing artifact:\n{existing}\n\n"
            f"New enrichment to incorporate:\n{enrichment}\n\n"
            f"Neighbor context:\n{neighbor_context}\n\n"
            f"Output the modified artifact."
        )),
    ]

    response = llm.invoke(messages)
    return _parse_json_or_text(response.content)


def _invoke_challenge(
    cell: AgentCell,
    neighbors: list[CellOutput],
    verbose: bool,
) -> Any:
    """Class 4 perturbation: inject a challenge into the neighborhood.
    'What hasn't been considered? What assumptions are wrong?'
    Prevents premature quiescence by provoking reconsideration."""
    llm = get_llm(temperature=0.6)  # slightly higher temp for creative challenge
    neighbor_context = _neighbor_summary(neighbors)

    messages = [
        SystemMessage(content=(
            f"You are a provocateur agent for the {cell.domain} domain. "
            f"Your job is to prevent groupthink and premature convergence. "
            f"Look at what your neighbors have produced and find what's MISSING, "
            f"what assumptions are WRONG, and what alternative approaches exist."
        )),
        HumanMessage(content=(
            f"Current neighborhood output:\n{neighbor_context}\n\n"
            f"Generate a challenge: identify 2-3 specific gaps, blind spots, or "
            f"untested assumptions in the current work. Be concrete and actionable. "
            f"Output JSON: {{\"gaps\": [\"...\"], \"challenges\": [\"...\"], "
            f"\"alternative_approaches\": [\"...\"]}}"
        )),
    ]
    response = llm.invoke(messages)
    return _parse_json_or_text(response.content)


def _invoke_gap_analysis(
    cell: AgentCell,
    neighbors: list[CellOutput],
    verbose: bool,
) -> Any:
    """Class 4 perturbation: analyze what the brief asked for vs what's been produced.
    Research cells generate new queries; master/execution cells identify missing components."""
    llm = get_llm(temperature=0.3)
    neighbor_context = _neighbor_summary(neighbors)
    context = _get_cell_context(cell, neighbor_context[:300]) if cell.knowledge_collections else ""

    if cell.role == "research":
        messages = [
            SystemMessage(content=(
                f"You are a research agent for {cell.domain}. "
                f"Analyze what your neighbors have produced and identify knowledge gaps. "
                f"Generate new research queries for areas not yet covered.\n\n"
                f"Domain knowledge:\n{context}"
            )),
            HumanMessage(content=(
                f"Neighbor outputs so far:\n{neighbor_context}\n\n"
                f"What knowledge areas are missing? What hasn't been researched yet? "
                f"Output 3-5 new research findings focused on gaps."
            )),
        ]
    elif cell.role == "execution":
        messages = [
            SystemMessage(content=(
                f"You are an execution agent ({cell.agent_type}). "
                f"Review the current artifact and neighbor feedback to identify "
                f"missing components, unimplemented features, or integration gaps."
            )),
            HumanMessage(content=(
                f"Current output:\n{_content_str(cell.output.content) if cell.output.content else '(none)'}\n\n"
                f"Neighbor context:\n{neighbor_context}\n\n"
                f"What's missing from the current build? Identify specific gaps and output "
                f"a work spec for the missing pieces."
            )),
        ]
    else:
        messages = [
            SystemMessage(content=(
                f"You are the {cell.agent_type} agent in {cell.domain}. "
                f"Re-evaluate the current state of work from your perspective. "
                f"What aspects of the brief haven't been adequately addressed?\n\n"
                f"Domain knowledge:\n{context}"
            )),
            HumanMessage(content=(
                f"Neighbor outputs:\n{neighbor_context}\n\n"
                f"Identify gaps in the current work from your {cell.agent_type} perspective. "
                f"Output a structured analysis with specific recommendations."
            )),
        ]

    response = llm.invoke(messages)
    return _parse_json_or_text(response.content)


# --- Helpers ---

def _get_cell_context(cell: AgentCell, query: str, n: int = 3) -> str:
    """Retrieve knowledge context for a cell. LOCAL ONLY -- cell's own collections."""
    parts = []
    for coll in cell.knowledge_collections[:4]:  # limit collections per query
        try:
            hits = query_store(query, coll, n_results=n)
            for hit in hits:
                section = hit.get("metadata", {}).get("section", "")
                excerpt = hit["text"][:300]
                parts.append(f"[{coll}/{section}] {excerpt}")
        except Exception:
            continue
    return "\n---\n".join(parts) if parts else "(no knowledge context available)"


def _neighbor_summary(neighbors: list[CellOutput], max_items: int = 4) -> str:
    """Summarize neighbor outputs. This is the ONLY external info a cell sees."""
    active = [n for n in neighbors if n.content is not None and n.kind]
    if not active:
        return "(no neighbor output)"
    parts = []
    for n in active[:max_items]:
        content_preview = _content_str(n.content)[:200]
        parts.append(f"[{n.kind} @ tick {n.tick}, state={n.state.value}] {content_preview}")
    return "\n".join(parts)


def _master_system_prompt(cell: AgentCell, context: str) -> str:
    return (
        f"You are the master agent for the {cell.domain} domain.\n"
        f"You decompose briefs into structured work, validate artifacts, "
        f"and have veto power over sub-agent outputs.\n\n"
        f"Domain knowledge:\n{context}"
    )


def _content_str(content: Any) -> str:
    """Safely convert any content to string."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return json.dumps(content, indent=2)[:3000]
    if isinstance(content, list):
        return json.dumps(content, indent=2)[:3000]
    return str(content)[:3000]


def _normalize_score(result: Any) -> Any:
    """Normalize critique scores to 0-100 scale.
    Legacy 0-1 scores (< 1.0) are multiplied by 100."""
    if not isinstance(result, dict) or "score" not in result:
        return result
    score = result["score"]
    if isinstance(score, (int, float)) and score <= 1.0 and score >= 0.0:
        result["score"] = round(score * 100, 1)
    return result


def _parse_json_or_text(text: str) -> Any:
    """Try to parse JSON from LLM response, fall back to raw text."""
    text = text.strip()
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try extracting from code fence
    if "```" in text:
        for block in text.split("```"):
            block = block.strip()
            if block.startswith("json"):
                block = block[4:].strip()
            try:
                return json.loads(block)
            except json.JSONDecodeError:
                continue
    # Fall back to text
    return text
