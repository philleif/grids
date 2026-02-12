"""CLI for execution agents."""

import argparse
import json
import sys

from rich.console import Console

from grids.domain.config import load_domain
from grids.execution.runner import ExecutionRunner

console = Console(stderr=True)


def execute_main():
    """Run execution agents against a domain's work order queue."""
    parser = argparse.ArgumentParser(
        description="Execute pending work orders for a domain"
    )
    parser.add_argument("config", help="Path to domain YAML config file")
    parser.add_argument(
        "--mode", "-m",
        choices=["one", "all", "daemon"],
        default="all",
        help="one = process single order, all = drain queue, daemon = poll continuously",
    )
    parser.add_argument("--queue-dir", default="tmp", help="Base directory for work order queues")
    parser.add_argument("--model", default=None, help="LLM model override")
    parser.add_argument("--poll-interval", type=float, default=5.0, help="Daemon poll interval (seconds)")
    parser.add_argument("--max-idle", type=int, default=60, help="Daemon max idle time before shutdown (seconds)")
    parser.add_argument("--no-visual", action="store_true", help="Skip visual critique loop")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress progress output")
    args = parser.parse_args()

    config = load_domain(args.config)
    runner = ExecutionRunner(
        config=config,
        queue_dir=args.queue_dir,
        model=args.model,
        verbose=not args.quiet,
        visual_critique=not args.no_visual,
    )

    if args.mode == "one":
        result = runner.process_one()
        if result:
            print(json.dumps(result, indent=2, default=str))
        else:
            console.print("[dim]No pending work orders.[/dim]")
            sys.exit(0)

    elif args.mode == "all":
        results = runner.process_all()
        if not results:
            console.print("[dim]No pending work orders.[/dim]")
        else:
            console.print(f"\n[bold green]Processed {len(results)} work orders.[/bold green]")
        runner._print_stats()

    elif args.mode == "daemon":
        runner.run_daemon(
            poll_interval=args.poll_interval,
            max_idle=args.max_idle,
        )


def run_full_cycle():
    """Convenience: specify + execute + validate in one command."""
    parser = argparse.ArgumentParser(
        description="Full cycle: specify a task, execute it, validate the result"
    )
    parser.add_argument("config", help="Path to domain YAML config file")
    parser.add_argument("request", help="What to build (text or @file)")
    parser.add_argument("--queue-dir", default="tmp", help="Base directory for queues")
    parser.add_argument("--model", default=None, help="LLM model override")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress progress output")
    args = parser.parse_args()

    from grids.domain.master import DomainMaster
    from grids.domain.work_orders import WorkOrderQueue
    from grids.orchestration.flow import Priority
    import os

    config = load_domain(args.config)
    master = DomainMaster(config)
    queue = WorkOrderQueue(args.queue_dir, config.domain.name)
    verbose = not args.quiet

    # Read request
    request = args.request
    if request.startswith("@") and os.path.isfile(request[1:]):
        with open(request[1:], "r") as f:
            request = f.read().strip()

    # Step 1: Domain specify
    if verbose:
        console.print("[cyan]Step 1: Generating work specification...[/cyan]")
    spec = master.specify(request)
    if verbose:
        console.print(f"  Title: {spec.title}")
        console.print(f"  Components: {len(spec.components)}")
        console.print(f"  Criteria: {len(spec.acceptance_criteria)}")

    # Step 2: Emit work order
    order = queue.emit_new(
        domain=config.domain.name,
        kind="code",
        spec=spec.to_dict(),
        acceptance_criteria=spec.acceptance_criteria,
        priority=Priority.HIGH,
        cost_of_delay=3.0,
        job_size=spec.estimated_size,
    )
    if verbose:
        console.print(f"  Emitted work order: {order.id}")

    # Step 3: Execute
    runner = ExecutionRunner(
        config=config,
        queue_dir=args.queue_dir,
        model=args.model,
        verbose=verbose,
    )
    results = runner.process_all()
    runner._print_stats()

    if results:
        print(json.dumps(results[-1], indent=2, default=str))
