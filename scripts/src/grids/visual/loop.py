"""Visual iteration loop -- render, capture, critique, revise, repeat.

This is the automated visual feedback loop:
1. Artifact (SVG/HTML) is rendered to PNG via Playwright
2. Screenshot is fed to vision LLM for critique
3. If approved: done
4. If iterate: critique feedback is sent back to the coder agent
5. Coder produces revised artifact
6. Repeat until approved or max iterations reached

This runs independently of the domain validation loop -- it's a visual QA pass
that catches issues the text-based domain agents can't see.
"""

import json
import os
import time

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from grids.domain.config import DomainConfig
from grids.domain.work_orders import WorkOrder
from grids.execution.coder import execute_work_order
from grids.visual.capture import capture_artifact
from grids.visual.critique import visual_critique

console = Console(stderr=True)


class VisualIterationResult:
    """Result of the visual iteration loop."""

    def __init__(
        self,
        final_artifact: dict,
        iterations: list[dict],
        approved: bool,
        final_score: float,
        screenshots: list[str],
    ):
        self.final_artifact = final_artifact
        self.iterations = iterations
        self.approved = approved
        self.final_score = final_score
        self.screenshots = screenshots

    def to_dict(self) -> dict:
        return {
            "approved": self.approved,
            "final_score": self.final_score,
            "total_iterations": len(self.iterations),
            "iterations": self.iterations,
            "screenshots": self.screenshots,
        }


def visual_iteration_loop(
    artifact: dict,
    order: WorkOrder,
    config: DomainConfig,
    output_dir: str,
    max_iterations: int = 3,
    approval_threshold: float = 75,
    model: str | None = None,
    verbose: bool = True,
) -> VisualIterationResult:
    """Run the visual feedback loop on an artifact.

    Returns a VisualIterationResult with the final artifact and iteration history.
    """
    screenshots_dir = os.path.join(output_dir, "screenshots")
    os.makedirs(screenshots_dir, exist_ok=True)

    brief = order.spec.get("description", order.spec.get("title", ""))
    current_artifact = artifact
    iterations = []
    screenshots = []

    for i in range(max_iterations):
        if verbose:
            console.print(f"\n[cyan]Visual iteration {i + 1}/{max_iterations}[/cyan]")

        # Step 1: Capture screenshot
        screenshot_path = capture_artifact(
            current_artifact,
            screenshots_dir,
        )

        if screenshot_path is None:
            if verbose:
                console.print("[yellow]  Cannot capture -- format not visual[/yellow]")
            break

        # Rename to include iteration number
        base, ext = os.path.splitext(screenshot_path)
        iter_path = f"{base}_iter{i + 1}{ext}"
        os.rename(screenshot_path, iter_path)
        screenshots.append(iter_path)

        if verbose:
            console.print(f"  [green]Screenshot: {iter_path}[/green]")

        # Step 2: Visual critique
        if verbose:
            console.print("  [cyan]Running visual critique...[/cyan]")

        critique = visual_critique(
            screenshot_path=iter_path,
            brief=brief,
            design_notes=current_artifact.get("design_notes", []),
            model=model,
            iteration=i,
        )

        iteration_record = {
            "iteration": i + 1,
            "timestamp": time.time(),
            "screenshot": iter_path,
            "critique": critique,
        }

        if verbose:
            _print_critique(critique, i + 1)

        # Step 3: Check verdict
        overall_score = critique.get("overall_score", 0.0)
        verdict = critique.get("verdict", "iterate")

        if verdict == "approve" or overall_score >= approval_threshold:
            iteration_record["action"] = "approved"
            iterations.append(iteration_record)
            if verbose:
                console.print(f"  [bold green]APPROVED (score: {overall_score:.2f})[/bold green]")
            return VisualIterationResult(
                final_artifact=current_artifact,
                iterations=iterations,
                approved=True,
                final_score=overall_score,
                screenshots=screenshots,
            )

        # Step 4: Revise
        iteration_record["action"] = "revising"
        iterations.append(iteration_record)

        if i < max_iterations - 1:
            if verbose:
                console.print("  [cyan]Revising artifact...[/cyan]")

            # Inject visual feedback into the work order for the coder
            visual_feedback = critique.get("feedback", "")
            priority_changes = critique.get("priority_changes", [])
            changes_str = "\n".join(f"- {c}" for c in priority_changes)

            revised_order = WorkOrder(
                id=f"{order.id}-visual-{i + 1}",
                domain=order.domain,
                kind=order.kind,
                spec=order.spec,
                acceptance_criteria=order.acceptance_criteria,
                priority=order.priority,
                cost_of_delay=order.cost_of_delay * 1.1,
                job_size=order.job_size * 0.6,
                iteration=order.iteration + i + 1,
                parent_id=order.id,
                feedback=(
                    f"VISUAL CRITIQUE (iteration {i + 1}):\n"
                    f"{visual_feedback}\n\n"
                    f"Priority changes:\n{changes_str}"
                ),
            )

            try:
                current_artifact = execute_work_order(revised_order, config, model=model)
            except Exception as e:
                if verbose:
                    console.print(f"  [red]Revision failed: {e}[/red]")
                break

    # Exhausted iterations
    final_score = iterations[-1]["critique"].get("overall_score", 0.0) if iterations else 0.0
    if verbose:
        console.print(f"\n[yellow]Max visual iterations reached (score: {final_score:.2f})[/yellow]")

    return VisualIterationResult(
        final_artifact=current_artifact,
        iterations=iterations,
        approved=False,
        final_score=final_score,
        screenshots=screenshots,
    )


def _print_critique(critique: dict, iteration: int):
    """Pretty-print a visual critique."""
    scores = critique.get("scores", {})
    if scores:
        table = Table(title=f"Visual Critique (iteration {iteration})")
        table.add_column("Dimension")
        table.add_column("Score", width=8)
        for dim, score in scores.items():
            color = "green" if score >= 0.75 else "yellow" if score >= 0.5 else "red"
            table.add_row(dim, f"[{color}]{score:.2f}[/{color}]")
        overall = critique.get("overall_score", 0.0)
        color = "green" if overall >= 0.75 else "yellow" if overall >= 0.5 else "red"
        table.add_row("[bold]OVERALL[/bold]", f"[bold {color}]{overall:.2f}[/bold {color}]")
        console.print(table)

    feedback = critique.get("feedback", "")
    if feedback:
        console.print(f"  [dim]{feedback[:300]}[/dim]")
