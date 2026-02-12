"""Validation loop -- orchestrates sub-agent scoring, master aggregation, and iteration."""

import json

from rich.console import Console
from rich.table import Table

from grids.domain.config import DomainConfig
from grids.domain.master import DomainMaster, ValidationResult
from grids.domain.work_orders import WorkOrder, WorkOrderQueue

console = Console(stderr=True)


def validate_artifact(
    master: DomainMaster,
    queue: WorkOrderQueue,
    order: WorkOrder,
    verbose: bool = True,
) -> ValidationResult:
    """Run the full validation loop for a completed work order.

    1. Load artifact from queue
    2. Sub-agents score independently
    3. Master aggregates + applies veto
    4. If approved: mark done. If not: emit iteration work order.
    """
    artifact = queue.load_artifact(order.id)
    if artifact is None:
        raise FileNotFoundError(f"No artifact found for work order {order.id}")

    brief = order.spec.get("description", order.spec.get("title", ""))

    if verbose:
        console.print(f"\n[cyan]Validating work order {order.id} (iteration {order.iteration})[/cyan]")

    result = master.validate(artifact, brief, iteration=order.iteration)

    if verbose:
        _print_validation(result)

    if result.approved:
        queue.mark_approved(order.id)
        if verbose:
            console.print("[bold green]APPROVED[/bold green]")
    else:
        max_iter = master.config.rules.max_iterations
        if order.iteration >= max_iter - 1:
            queue.mark_approved(order.id)
            if verbose:
                console.print(f"[yellow]Max iterations ({max_iter}) reached -- approving with notes.[/yellow]")
        else:
            queue.mark_iterating(order.id)
            new_order = queue.emit_iteration(order, result.feedback)
            if verbose:
                console.print(f"[yellow]ITERATE -> new work order {new_order.id}[/yellow]")

    return result


def _print_validation(result: ValidationResult):
    """Pretty-print validation results."""
    table = Table(title=f"Validation (iteration {result.iteration})", show_lines=True)
    table.add_column("Agent", style="cyan")
    table.add_column("Score", width=8)
    table.add_column("Verdict")
    table.add_column("Feedback", max_width=60)

    for s in result.sub_scores:
        color = "green" if s.verdict == "pass" else "red"
        table.add_row(
            s.agent_name,
            f"{s.score:.2f}",
            f"[{color}]{s.verdict}[/{color}]",
            s.feedback[:60],
        )

    table.add_row(
        "[bold]MASTER[/bold]",
        f"[bold]{result.master_score:.2f}[/bold]",
        f"[bold {'green' if result.approved else 'red'}]"
        f"{'APPROVE' if result.approved else 'ITERATE'}"
        f"[/bold {'green' if result.approved else 'red'}]",
        "",
    )

    console.print(table)
    console.print(f"  Weighted score: {result.weighted_score:.3f}")

    if result.feedback:
        console.print(f"\n[yellow]Feedback:[/yellow]\n{result.feedback[:500]}")
