"""Coder execution agent -- consumes work orders, produces artifacts.

Generates design artifacts (SVG, LaTeX, HTML) by calling the LLM with
the work specification and domain knowledge context. Deposits results
as JSON artifacts for domain validation.
"""

import json
import os
import re

from langchain_core.messages import HumanMessage, SystemMessage

from grids.domain.config import DomainConfig
from grids.domain.work_orders import WorkOrder, WorkOrderQueue, WorkOrderStatus
from grids.knowledge.store import query_store
from grids.orchestration.agents import get_llm
from grids.bridge import parse_layout_output, layout_to_rust_json

CODER_SYSTEM = """You are an execution agent in an emergent design orchestration system.

Your job: turn structured work specifications into concrete design artifacts.

You produce artifacts in these formats:
- SVG: for visual layouts, illustrations, cards, posters
- LaTeX: for typographic layouts, print-ready documents
- HTML/CSS: for screen-based designs, interactive prototypes

Rules:
- Follow the specification precisely -- every acceptance criterion must be addressed
- Ground your design decisions in the domain knowledge provided
- Use proper typographic hierarchy, grid alignment, and color standards
- Output a JSON object with: format, code (the SVG/LaTeX/HTML string), design_notes, decisions
- Each decision in your design_notes should cite which reference or principle informed it
- Be meticulous about craft details (kerning hints, baseline alignment, color values)
"""

ITERATION_ADDENDUM = """This is iteration {iteration} of this work order.

Previous feedback from domain validation:
{feedback}

Address every point in the feedback. Do not repeat the same mistakes.
"""


def _retrieve_domain_context(config: DomainConfig, spec: dict, n: int = 5) -> str:
    """Pull relevant knowledge from domain collections for this spec."""
    query = f"{spec.get('title', '')} {spec.get('description', '')}"[:400]
    parts = []
    for coll in config.master.knowledge_collections:
        try:
            hits = query_store(query, coll, n_results=n)
            for hit in hits:
                source = hit.get("metadata", {}).get("source", coll)
                section = hit.get("metadata", {}).get("section", "")
                excerpt = hit["text"][:500]
                parts.append(f"[{source} / {section}]\n{excerpt}")
        except Exception:
            continue
    return "\n---\n".join(parts)


def execute_work_order(
    order: WorkOrder,
    config: DomainConfig,
    model: str | None = None,
) -> dict:
    """Execute a single work order and return the artifact."""
    llm = get_llm(model=model, temperature=0.5)

    context = _retrieve_domain_context(config, order.spec)

    messages = [SystemMessage(content=CODER_SYSTEM)]

    if context:
        messages.append(HumanMessage(
            content=f"Domain knowledge for this task:\n\n{context}"
        ))

    spec_str = json.dumps(order.spec, indent=2)
    criteria_str = "\n".join(f"- {c}" for c in order.acceptance_criteria)

    prompt = (
        f"Work specification:\n{spec_str}\n\n"
        f"Acceptance criteria:\n{criteria_str}\n\n"
    )

    if order.iteration > 0 and order.feedback:
        prompt += ITERATION_ADDENDUM.format(
            iteration=order.iteration,
            feedback=order.feedback,
        )

    prompt += (
        "\nProduce the artifact now. Output ONLY a JSON object with these fields:\n"
        '- "format": "svg" | "latex" | "html"\n'
        '- "code": the full artifact code as a string\n'
        '- "design_notes": list of {decision, rationale, reference} objects\n'
        '- "acceptance_responses": object mapping each criterion to how it was addressed\n'
    )

    messages.append(HumanMessage(content=prompt))

    response = llm.invoke(messages)
    text = response.content.strip()

    artifact = _parse_artifact(text, order)
    return artifact


def _parse_artifact(text: str, order: WorkOrder) -> dict:
    """Parse the LLM response into a structured artifact."""
    try:
        if "```" in text:
            blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
            for block in blocks:
                try:
                    parsed = json.loads(block)
                    if isinstance(parsed, dict) and "code" in parsed:
                        return _enrich_artifact(parsed, order)
                except json.JSONDecodeError:
                    continue

        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return _enrich_artifact(parsed, order)
    except json.JSONDecodeError:
        pass

    return {
        "work_order_id": order.id,
        "domain": order.domain,
        "format": "raw",
        "code": text,
        "design_notes": [],
        "acceptance_responses": {},
        "parse_error": "Could not parse structured artifact from LLM response",
    }


def _enrich_artifact(parsed: dict, order: WorkOrder) -> dict:
    """Add metadata to a parsed artifact."""
    parsed["work_order_id"] = order.id
    parsed["domain"] = order.domain
    parsed["iteration"] = order.iteration
    if "format" not in parsed:
        parsed["format"] = _detect_format(parsed.get("code", ""))
    return parsed


def _detect_format(code: str) -> str:
    """Guess artifact format from code content."""
    if "<svg" in code[:200]:
        return "svg"
    if "\\documentclass" in code[:200]:
        return "latex"
    if "<html" in code[:200] or "<!DOCTYPE" in code[:200]:
        return "html"
    return "raw"
