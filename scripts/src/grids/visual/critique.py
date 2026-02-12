"""Visual critique agent -- feeds screenshots to a vision LLM for design feedback.

This is the "eyes" of the system. It takes a rendered screenshot of an artifact
and asks a vision-capable LLM (Claude) to critique the visual quality against
the original brief and domain principles.
"""

import json

from langchain_core.messages import HumanMessage, SystemMessage

from grids.orchestration.agents import get_llm
from grids.visual.capture import screenshot_to_base64

VISUAL_CRITIQUE_SYSTEM = """You are a visual design critic in an emergent orchestration system.

You are looking at a screenshot of a design artifact produced by an AI agent.
Your job is to critique its visual quality with the precision of a senior art director.

Evaluate on these dimensions:
1. TYPOGRAPHY: hierarchy, spacing, readability, font choices, alignment
2. COMPOSITION: balance, rhythm, grid adherence, use of whitespace
3. COLOR: palette coherence, contrast, accessibility
4. CRAFT: precision of execution, attention to detail, professional finish
5. INTENT: does it fulfill the creative brief? Does it communicate clearly?

Rules:
- Be specific and actionable. "The heading is too large" not "could be improved"
- Reference design principles when relevant (grid theory, typographic scale, etc.)
- Score each dimension 0.0-1.0
- Give an overall verdict: "approve" (ship it) or "iterate" (needs revision)
- If iterating, list exactly what to change, in priority order
- Be strict but fair. Real design work has high standards.
"""


def visual_critique(
    screenshot_path: str,
    brief: str,
    design_notes: list[dict] | None = None,
    model: str | None = None,
    iteration: int = 0,
) -> dict:
    """Critique a screenshot using vision LLM.

    Returns a structured critique dict with scores and feedback.
    """
    llm = get_llm(model=model, temperature=0.3)
    b64 = screenshot_to_base64(screenshot_path)

    messages = [SystemMessage(content=VISUAL_CRITIQUE_SYSTEM)]

    content_parts = [
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{b64}",
            },
        },
        {
            "type": "text",
            "text": f"Creative brief: {brief}\n\nIteration: {iteration}\n\n",
        },
    ]

    if design_notes:
        notes_str = "\n".join(
            f"- {d.get('decision', '')}: {d.get('rationale', '')}"
            for d in design_notes[:10]
        )
        content_parts.append({
            "type": "text",
            "text": f"Design decisions made by the agent:\n{notes_str}\n\n",
        })

    content_parts.append({
        "type": "text",
        "text": (
            "Critique this design. Output ONLY a JSON object:\n"
            "{\n"
            '  "scores": {"typography": 0.0-1.0, "composition": 0.0-1.0, "color": 0.0-1.0, "craft": 0.0-1.0, "intent": 0.0-1.0},\n'
            '  "overall_score": 0.0-1.0,\n'
            '  "verdict": "approve" | "iterate",\n'
            '  "feedback": "specific, actionable critique",\n'
            '  "priority_changes": ["most important change first", ...]\n'
            "}\n"
        ),
    })

    messages.append(HumanMessage(content=content_parts))

    response = llm.invoke(messages)
    text = response.content.strip()

    return _parse_critique(text)


def _parse_critique(text: str) -> dict:
    """Parse the vision LLM critique response."""
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
            "scores": {},
            "overall_score": 0.5,
            "verdict": "iterate",
            "feedback": text,
            "priority_changes": [],
            "parse_error": "Could not parse structured critique",
        }
