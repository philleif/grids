"""Execution runner -- poll loop that picks up work orders and processes them.

The runner is the main entry point for execution. It:
1. Polls the work order queue (WSJF-sorted)
2. Picks up the highest-priority pending order
3. Runs structural checks
4. Calls the coder agent to produce an artifact
5. Calls the tester agent to validate
6. Handles approve/iterate outcomes
7. Saves all outputs to the artifacts directory

Can run as a one-shot (process all pending) or a daemon (poll continuously).
"""

import json
import os
import time

from rich.console import Console
from rich.panel import Panel

from grids.domain.config import DomainConfig, load_domain
from grids.domain.master import DomainMaster
from grids.domain.work_orders import WorkOrder, WorkOrderQueue, WorkOrderStatus
from grids.execution.coder import execute_work_order
from grids.execution.tester import test_artifact, structural_checks
from grids.visual.loop import visual_iteration_loop

console = Console(stderr=True)


class ExecutionRunner:
    """Processes work orders from a domain's queue."""

    def __init__(
        self,
        config: DomainConfig,
        queue_dir: str = "tmp",
        model: str | None = None,
        verbose: bool = True,
        visual_critique: bool = True,
        visual_max_iterations: int = 2,
    ):
        self.config = config
        self.master = DomainMaster(config)
        self.queue = WorkOrderQueue(queue_dir, config.domain.name)
        self.model = model
        self.verbose = verbose
        self.visual_critique = visual_critique
        self.visual_max_iterations = visual_max_iterations
        self.stats = {
            "processed": 0,
            "approved": 0,
            "iterated": 0,
            "failed": 0,
            "visual_passes": 0,
        }

    def process_one(self) -> dict | None:
        """Pick up and process the next pending work order.

        Returns the artifact dict if processed, None if queue is empty.
        """
        pending = self.queue.list_pending()
        if not pending:
            if self.verbose:
                console.print("[dim]No pending work orders.[/dim]")
            return None

        order = pending[0]
        return self._execute_order(order)

    def process_all(self) -> list[dict]:
        """Process all pending work orders in WSJF order."""
        results = []
        while True:
            result = self.process_one()
            if result is None:
                break
            results.append(result)
        return results

    def run_daemon(self, poll_interval: float = 5.0, max_idle: int = 60):
        """Poll for work orders continuously.

        Stops after max_idle seconds of no new work.
        """
        idle_time = 0
        if self.verbose:
            console.print(Panel(
                f"Domain: {self.config.domain.name}\n"
                f"Poll interval: {poll_interval}s\n"
                f"Max idle: {max_idle}s",
                title="Execution Runner (daemon)",
                border_style="cyan",
            ))

        while idle_time < max_idle:
            result = self.process_one()
            if result is None:
                idle_time += poll_interval
                if self.verbose:
                    console.print(f"  [dim]Idle ({idle_time:.0f}s / {max_idle}s)...[/dim]", end="\r")
                time.sleep(poll_interval)
            else:
                idle_time = 0

        if self.verbose:
            console.print(f"\n[yellow]Max idle time reached. Shutting down.[/yellow]")
            self._print_stats()

    def _execute_order(self, order: WorkOrder) -> dict:
        """Full execution cycle for one work order."""
        if self.verbose:
            console.print(Panel(
                f"ID: {order.id}\n"
                f"Kind: {order.kind}\n"
                f"Iteration: {order.iteration}\n"
                f"WSJF: {order.wsjf_score:.2f}\n"
                f"Title: {order.spec.get('title', 'untitled')}",
                title="Executing Work Order",
                border_style="green",
            ))

        self.queue.pick_up(order.id)

        # Step 1: Generate artifact
        if self.verbose:
            console.print("[cyan]  Generating artifact...[/cyan]")

        try:
            artifact = execute_work_order(order, self.config, model=self.model)
        except Exception as e:
            console.print(f"[red]  Coder failed: {e}[/red]")
            self.stats["failed"] += 1
            return {"error": str(e), "work_order_id": order.id}

        # Step 2: Structural checks
        issues = structural_checks(artifact)
        if issues:
            if self.verbose:
                for issue in issues:
                    console.print(f"  [yellow]Structural: {issue}[/yellow]")

        # Step 3: Save raw artifact
        artifact_path = self._save_artifact(order, artifact)
        if self.verbose:
            console.print(f"  [green]Artifact saved: {artifact_path}[/green]")

        # Step 4: Save rendered output (SVG/LaTeX/HTML file)
        self._save_rendered(order, artifact)

        # Step 4.5: Visual critique loop (if enabled and artifact is visual)
        if self.visual_critique and artifact.get("format") in ("svg", "html", "latex"):
            if self.verbose:
                console.print("[cyan]  Running visual critique loop...[/cyan]")
            try:
                visual_dir = os.path.join(
                    os.path.dirname(self.queue.artifacts_dir),
                    "visual-loop",
                )
                vis_result = visual_iteration_loop(
                    artifact=artifact,
                    order=order,
                    config=self.config,
                    output_dir=visual_dir,
                    max_iterations=self.visual_max_iterations,
                    model=self.model,
                    verbose=self.verbose,
                )
                if vis_result.approved:
                    artifact = vis_result.final_artifact
                    self.stats["visual_passes"] += 1
                    if self.verbose:
                        console.print(f"  [green]Visual approved (score: {vis_result.final_score:.2f})[/green]")
                else:
                    artifact = vis_result.final_artifact
                    if self.verbose:
                        console.print(f"  [yellow]Visual: best effort (score: {vis_result.final_score:.2f})[/yellow]")
            except Exception as e:
                if self.verbose:
                    console.print(f"  [yellow]Visual critique skipped: {e}[/yellow]")

        # Step 5: Domain validation
        if self.verbose:
            console.print("[cyan]  Running domain validation...[/cyan]")

        try:
            result = test_artifact(order, artifact, self.queue, self.master, verbose=self.verbose)
            self.stats["processed"] += 1

            if result.approved:
                self.stats["approved"] += 1
                if self.verbose:
                    console.print(f"  [bold green]APPROVED (score: {result.weighted_score:.3f})[/bold green]")
            else:
                self.stats["iterated"] += 1
                if self.verbose:
                    console.print(f"  [yellow]ITERATE (score: {result.weighted_score:.3f})[/yellow]")

        except Exception as e:
            console.print(f"[red]  Validation failed: {e}[/red]")
            self.stats["failed"] += 1

        return artifact

    def _save_artifact(self, order: WorkOrder, artifact: dict) -> str:
        """Save the full artifact JSON."""
        path = os.path.join(
            self.queue.artifacts_dir,
            f"{order.id}.json",
        )
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(artifact, f, indent=2)
        return path

    def _save_rendered(self, order: WorkOrder, artifact: dict):
        """Save the rendered artifact as its native format file."""
        code = artifact.get("code", "")
        fmt = artifact.get("format", "raw")
        if not code or fmt == "raw":
            return

        ext_map = {"svg": ".svg", "latex": ".tex", "html": ".html", "raw": ".txt"}
        ext = ext_map.get(fmt, ".txt")

        rendered_dir = os.path.join(
            os.path.dirname(self.queue.artifacts_dir),
            "rendered",
        )
        os.makedirs(rendered_dir, exist_ok=True)
        path = os.path.join(rendered_dir, f"{order.id}{ext}")

        with open(path, "w", encoding="utf-8") as f:
            f.write(code)

        if self.verbose:
            console.print(f"  [green]Rendered: {path}[/green]")

    def _print_stats(self):
        """Print execution statistics."""
        console.print(Panel(
            f"Processed: {self.stats['processed']}\n"
            f"Approved: {self.stats['approved']}\n"
            f"Iterated: {self.stats['iterated']}\n"
            f"Failed: {self.stats['failed']}",
            title="Execution Stats",
            border_style="cyan",
        ))
