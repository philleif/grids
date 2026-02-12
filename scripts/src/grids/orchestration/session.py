"""Session runner -- manages a creative production run end-to-end.

Ties together:
- LangGraph orchestration (graph.py)
- Flow management (flow.py)
- Rule-based agent behavior (rules.py)
- Knowledge retrieval (knowledge store)

Usage:
    from grids.orchestration.session import run_session
    result = run_session("Create a zine about cellular automata and creative flow")
"""

import argparse
import json
import os
import sys
import time

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress

from grids.orchestration.graph import compile_graph, CreativeState

console = Console(stderr=True)


def run_session(
    brief: str,
    max_iterations: int = 3,
    output_dir: str | None = None,
    model: str | None = None,
    verbose: bool = True,
) -> dict:
    """Run a full creative production session."""
    session_id = f"session-{int(time.time())}"
    output_dir = output_dir or os.path.join("tmp", session_id)
    os.makedirs(output_dir, exist_ok=True)

    if verbose:
        console.print(Panel(brief, title="Creative Brief", border_style="cyan"))
        console.print(f"  Session: {session_id}")
        console.print(f"  Max iterations: {max_iterations}")
        console.print(f"  Model: {model}")
        console.print(f"  Output: {output_dir}/")
        console.print()

    # Build and compile the graph (initializes flow controller + rule-driven agents)
    graph = compile_graph(use_rules=True)

    initial_state: CreativeState = {
        "brief": brief,
        "research": "",
        "concepts": "",
        "layout": "",
        "critique": "",
        "iteration": 0,
        "max_iterations": max_iterations,
        "status": "running",
        "history": [],
        "flow_metrics": {},
    }

    if verbose:
        console.print("[bold]Starting orchestration graph...[/bold]\n")

    start_time = time.time()
    final_state = None

    # Stream events from the graph
    for event in graph.stream(initial_state):
        for node_name, node_output in event.items():
            if verbose:
                console.print(f"  [cyan]{node_name}[/cyan] completed (iteration {node_output.get('iteration', initial_state.get('iteration', 0))})")

                # Show preview of output
                for key in ("research", "concepts", "layout", "critique"):
                    if key in node_output and node_output[key]:
                        preview = node_output[key][:150].replace("\n", " ")
                        console.print(f"    -> {key}: {preview}...")

            # Update state tracking
            initial_state.update(node_output)

    final_state = initial_state
    elapsed = time.time() - start_time

    if verbose:
        console.print(f"\n[bold green]Session complete.[/bold green]")
        console.print(f"  Status: {final_state.get('status', 'unknown')}")
        console.print(f"  Iterations: {final_state.get('iteration', 0)}")
        console.print(f"  Time: {elapsed:.1f}s")

    # Save outputs
    result = {
        "session_id": session_id,
        "brief": brief,
        "status": final_state.get("status", "unknown"),
        "iterations": final_state.get("iteration", 0),
        "elapsed_seconds": round(elapsed, 1),
        "research": final_state.get("research", ""),
        "concepts": final_state.get("concepts", ""),
        "layout": final_state.get("layout", ""),
        "critique": final_state.get("critique", ""),
        "history": final_state.get("history", []),
        "flow_metrics": final_state.get("flow_metrics", {}),
    }

    result_path = os.path.join(output_dir, "session.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    if verbose:
        console.print(f"  Saved: {result_path}")

    return result


def main():
    parser = argparse.ArgumentParser(description="Run a creative production session")
    parser.add_argument("brief", help="Creative brief (text or @file)")
    parser.add_argument("--max-iterations", "-i", type=int, default=3, help="Max critique iterations")
    parser.add_argument("--output", "-o", default=None, help="Output directory")
    parser.add_argument("--model", "-m", default=None, help="Model name (default: claude-opus-4-6 via local proxy)")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress progress output")
    args = parser.parse_args()

    # Allow reading brief from file
    brief = args.brief
    if brief.startswith("@") and os.path.isfile(brief[1:]):
        with open(brief[1:], "r") as f:
            brief = f.read().strip()

    result = run_session(
        brief=brief,
        max_iterations=args.max_iterations,
        output_dir=args.output,
        model=args.model,
        verbose=not args.quiet,
    )

    # Print final layout to stdout
    if result.get("layout"):
        print(result["layout"])


if __name__ == "__main__":
    main()
