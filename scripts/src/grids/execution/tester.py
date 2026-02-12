"""Tester execution agent -- validates artifacts against domain expertise.

Takes a completed artifact and runs it through the domain master + sub-agent
validation pipeline. Reports pass/fail and triggers iteration if needed.
"""

import json
import os

from rich.console import Console

from grids.domain.config import DomainConfig
from grids.domain.master import DomainMaster, ValidationResult
from grids.domain.work_orders import WorkOrder, WorkOrderQueue
from grids.domain.validation import validate_artifact

console = Console(stderr=True)


def test_artifact(
    order: WorkOrder,
    artifact: dict,
    queue: WorkOrderQueue,
    master: DomainMaster,
    verbose: bool = True,
) -> ValidationResult:
    """Run domain validation on an artifact and handle the result.

    Returns the ValidationResult. Side effects:
    - If approved: marks work order as approved
    - If not approved and under max iterations: emits iteration work order
    - If not approved and at max iterations: approves with notes
    """
    queue.deposit_artifact(order.id, artifact)
    result = validate_artifact(master, queue, order, verbose=verbose)
    return result


def structural_checks(artifact: dict) -> list[str]:
    """Quick structural validation before sending to domain agents.

    Returns a list of issues found. Empty list = passed.
    """
    issues = []

    if "code" not in artifact:
        issues.append("Artifact missing 'code' field")
        return issues

    code = artifact.get("code", "")
    fmt = artifact.get("format", "raw")

    if not code.strip():
        issues.append("Artifact 'code' is empty")
        return issues

    if fmt == "svg":
        if "<svg" not in code:
            issues.append("SVG artifact does not contain <svg> tag")
        if "</svg>" not in code:
            issues.append("SVG artifact missing closing </svg> tag")

    elif fmt == "latex":
        if "\\begin{document}" not in code:
            issues.append("LaTeX artifact missing \\begin{document}")
        if "\\end{document}" not in code:
            issues.append("LaTeX artifact missing \\end{document}")

    elif fmt == "html":
        if "<html" not in code.lower() and "<!doctype" not in code.lower():
            issues.append("HTML artifact missing <html> or <!DOCTYPE>")

    if artifact.get("parse_error"):
        issues.append(f"Parse warning: {artifact['parse_error']}")

    return issues
