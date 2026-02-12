"""CLI for visual feedback tools."""

import argparse
import json
import os
import sys

from rich.console import Console

console = Console(stderr=True)


def capture_main():
    """Capture a rendered artifact (SVG/HTML file) to PNG screenshot."""
    parser = argparse.ArgumentParser(description="Capture artifact to PNG screenshot")
    parser.add_argument("input", help="Path to SVG or HTML file")
    parser.add_argument("--output", "-o", default=None, help="Output PNG path")
    parser.add_argument("--width", "-w", type=int, default=1200, help="Viewport width")
    parser.add_argument("--height", type=int, default=900, help="Viewport height")
    args = parser.parse_args()

    from grids.visual.capture import capture_file

    output = args.output or args.input.rsplit(".", 1)[0] + ".png"

    console.print(f"[cyan]Capturing {args.input} -> {output}[/cyan]")
    result = capture_file(args.input, output, args.width, args.height)
    console.print(f"[green]Saved: {result}[/green]")


def critique_main():
    """Run visual critique on a screenshot."""
    parser = argparse.ArgumentParser(description="Critique a design screenshot")
    parser.add_argument("screenshot", help="Path to PNG screenshot")
    parser.add_argument("--brief", "-b", required=True, help="Creative brief")
    parser.add_argument("--model", default=None, help="LLM model override")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    from grids.visual.critique import visual_critique

    console.print(f"[cyan]Critiquing {args.screenshot}...[/cyan]")
    result = visual_critique(
        screenshot_path=args.screenshot,
        brief=args.brief,
        model=args.model,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        from grids.visual.loop import _print_critique
        _print_critique(result, 1)
        print(json.dumps(result, indent=2))


def visual_loop_main():
    """Run the visual iteration loop on an artifact JSON."""
    parser = argparse.ArgumentParser(
        description="Visual feedback loop: capture -> critique -> revise -> repeat"
    )
    parser.add_argument("artifact", help="Path to artifact JSON file")
    parser.add_argument("config", help="Path to domain YAML config file")
    parser.add_argument("--brief", "-b", default=None, help="Creative brief override")
    parser.add_argument("--max-iterations", "-i", type=int, default=3, help="Max visual iterations")
    parser.add_argument("--threshold", "-t", type=float, default=0.75, help="Approval score threshold")
    parser.add_argument("--output", "-o", default=None, help="Output directory")
    parser.add_argument("--model", default=None, help="LLM model override")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress progress output")
    args = parser.parse_args()

    from grids.domain.config import load_domain
    from grids.domain.work_orders import WorkOrder
    from grids.visual.loop import visual_iteration_loop

    config = load_domain(args.config)

    with open(args.artifact, "r") as f:
        artifact = json.load(f)

    # Reconstruct a minimal WorkOrder for the loop
    order = WorkOrder(
        id=artifact.get("work_order_id", "manual"),
        domain=config.domain.name,
        kind="code",
        spec={"title": args.brief or artifact.get("title", ""), "description": args.brief or ""},
        acceptance_criteria=[],
    )

    output_dir = args.output or os.path.join("tmp", config.domain.name, "visual-loop")

    result = visual_iteration_loop(
        artifact=artifact,
        order=order,
        config=config,
        output_dir=output_dir,
        max_iterations=args.max_iterations,
        approval_threshold=args.threshold,
        model=args.model,
        verbose=not args.quiet,
    )

    # Save result
    result_path = os.path.join(output_dir, "visual-loop-result.json")
    os.makedirs(os.path.dirname(result_path), exist_ok=True)
    with open(result_path, "w") as f:
        json.dump(result.to_dict(), f, indent=2, default=str)

    console.print(f"\n[green]Result saved: {result_path}[/green]")
    print(json.dumps(result.to_dict(), indent=2, default=str))
