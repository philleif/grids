"""grids-eval -- Automated run scoring CLI.

Usage:
    grids-eval tmp/run-1770560001/
    grids-eval tmp/run-1770567015/phase-1a/
    grids-eval tmp/run-1770560001/ --json
"""

from __future__ import annotations

import argparse
import json
import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from grids.eval.scorer import score_run, score_phase, PhaseScore, RunScore

console = Console(stderr=True)

# Thresholds for color-coding
GOOD = 0.75
WARN = 0.50


def _score_color(val: float) -> str:
    if val >= GOOD:
        return "green"
    if val >= WARN:
        return "yellow"
    return "red"


def _verdict_style(verdict: str) -> str:
    if verdict == "HEALTHY":
        return "bold green"
    if verdict == "DEGRADED":
        return "bold yellow"
    return "bold red"


def _pct(val: float) -> str:
    return f"{val * 100:.0f}%"


def _print_phase(ps: PhaseScore):
    """Print a rich scorecard for a single phase."""
    table = Table(
        title=f"Phase: {ps.phase_name}",
        title_style="bold cyan",
        show_lines=True,
    )
    table.add_column("Metric", style="bold", width=28)
    table.add_column("Score", width=8, justify="right")
    table.add_column("Detail", width=50)

    # Cell utilization
    color = _score_color(ps.cell_utilization)
    table.add_row(
        "Cell Utilization",
        f"[{color}]{_pct(ps.cell_utilization)}[/{color}]",
        f"{ps.cells_that_processed}/{ps.total_cells} cells processed items",
    )

    # Tick efficiency
    color = _score_color(ps.tick_efficiency)
    table.add_row(
        "Tick Efficiency",
        f"[{color}]{_pct(ps.tick_efficiency)}[/{color}]",
        f"{ps.active_ticks}/{ps.total_ticks} ticks had LLM calls",
    )

    # Critique coverage
    color = _score_color(ps.critique_coverage)
    detail = (f"{ps.critique_cells_that_reviewed}/{ps.critique_cells_total} critique cells reviewed"
              if ps.critique_cells_total > 0 else "no critique cells in grid")
    table.add_row(
        "Critique Coverage",
        f"[{color}]{_pct(ps.critique_coverage)}[/{color}]",
        detail,
    )

    # Quiescence legitimacy
    color = _score_color(ps.quiescence_legitimacy)
    q_detail = "quiescent" if ps.quiescent else "hit max ticks"
    if ps.cells_with_nonempty_inbox > 0:
        q_detail += f", {ps.cells_with_nonempty_inbox} cells still had inbox items"
    table.add_row(
        "Quiescence Legitimacy",
        f"[{color}]{_pct(ps.quiescence_legitimacy)}[/{color}]",
        q_detail,
    )

    # Propagation efficiency
    color = _score_color(ps.propagation_efficiency)
    table.add_row(
        "Propagation Efficiency",
        f"[{color}]{_pct(ps.propagation_efficiency)}[/{color}]",
        f"{ps.total_items_emitted} items emitted / {ps.total_llm_calls} LLM calls",
    )

    # Two-level metrics (GRD-6)
    color = _score_color(ps.routing_efficiency)
    table.add_row(
        "Routing Efficiency",
        f"[{color}]{_pct(ps.routing_efficiency)}[/{color}]",
        f"{ps.routing_delivered}/{ps.routing_scheduled} propagations accepted",
    )

    avg_q = ps.avg_quality_score
    if avg_q is not None:
        q_norm = min(1.0, max(0.0, avg_q / 100.0)) if avg_q > 1.0 else avg_q
        color = _score_color(q_norm)
        verdicts_str = ", ".join(f"{k}={v}" for k, v in sorted(ps.quality_critique_verdicts.items()))
        table.add_row(
            "Quality Score",
            f"[{color}]{avg_q:.1f}[/{color}]",
            f"verdicts: [{verdicts_str}], rework: {ps.quality_rework_count}",
        )
    else:
        table.add_row(
            "Quality Score",
            "[dim]n/a[/dim]",
            "no critique scores available",
        )

    console.print(table)

    # P(good output) formula
    p_good = ps.p_good_output
    if p_good is not None:
        p_color = _score_color(p_good)
        console.print(
            f"  [bold]P(good) = P(routing) x P(quality) = "
            f"{ps.routing_efficiency:.0%} x {avg_q:.0f}/100 = "
            f"[{p_color}]{p_good:.0%}[/{p_color}][/bold]"
        )
    else:
        console.print(
            f"  [bold]P(good) = P(routing) x P(quality) = "
            f"{ps.routing_efficiency:.0%} x [dim]n/a[/dim][/bold]"
        )

    # Per-role routing breakdown
    if ps.routing_per_role:
        console.print("  [dim]Routing by role:[/dim]")
        for role, stats in sorted(ps.routing_per_role.items()):
            eff = stats.get("efficiency", 0)
            color = _score_color(eff)
            console.print(
                f"    {role}: {stats.get('delivered', 0)}/{stats.get('scheduled', 0)} "
                f"([{color}]{eff:.0%}[/{color}])"
            )

    console.print()

    # Overall verdict
    v = ps.verdict
    console.print(
        f"  Overall: [{_verdict_style(v)}]{v}[/{_verdict_style(v)}] "
        f"({_pct(ps.overall_health)})"
    )

    # Show idle cells if utilization is low
    if ps.cell_utilization < GOOD and ps.idle_cells:
        console.print(f"\n  [dim]Idle cells ({len(ps.idle_cells)}):[/dim]")
        for c in ps.idle_cells[:10]:
            console.print(f"    [{c.position}] {c.domain}/{c.agent_type} ({c.role})")
        if len(ps.idle_cells) > 10:
            console.print(f"    ... +{len(ps.idle_cells) - 10} more")

    console.print()


def _print_run(rs: RunScore):
    """Print full run scorecard."""
    console.print(Panel(
        f"[bold]{rs.run_dir}[/bold]",
        title="grids-eval",
        border_style="cyan",
    ))

    for ps in rs.phases:
        _print_phase(ps)

    if len(rs.phases) > 1:
        v = rs.verdict
        console.print(Panel(
            f"Verdict: [{_verdict_style(v)}]{v}[/{_verdict_style(v)}] ({_pct(rs.overall_health)})\n"
            f"Phases scored: {len(rs.phases)}",
            title="Pipeline Summary",
            border_style="green" if v == "HEALTHY" else ("yellow" if v == "DEGRADED" else "red"),
        ))


def main():
    parser = argparse.ArgumentParser(
        description="Score a GRIDS run directory for health metrics",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  grids-eval tmp/run-1770560001/\n"
            "  grids-eval tmp/run-1770567015/phase-1a/\n"
            "  grids-eval tmp/run-1770560001/ --json\n"
        ),
    )
    parser.add_argument("run_dir", help="Path to a run directory or phase subdirectory")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON scorecard to stdout")
    parser.add_argument("--phase", "-p", help="Score only a specific phase (e.g., phase-1a)")

    args = parser.parse_args()

    # If user pointed at a phase subdirectory directly (contains grid-snapshot.json)
    from pathlib import Path
    target = Path(args.run_dir)

    if args.phase:
        phase_path = target / args.phase
        if not phase_path.exists():
            console.print(f"[red]Phase directory not found: {phase_path}[/red]")
            sys.exit(1)
        ps = score_phase(str(phase_path), phase_name=args.phase)
        if ps is None:
            console.print(f"[red]No grid-snapshot.json in {phase_path}[/red]")
            sys.exit(1)
        if args.json:
            print(json.dumps(ps.to_dict(), indent=2))
        else:
            _print_phase(ps)
        # Exit code based on health
        sys.exit(0 if ps.verdict != "UNHEALTHY" else 1)

    # Check if this IS a phase directory (has grid-snapshot.json but no phase-* subdirs)
    has_snapshot = (target / "grid-snapshot.json").exists()
    has_phase_dirs = any(target.glob("phase-*"))

    if has_snapshot and not has_phase_dirs:
        # Scoring a single phase directory directly
        ps = score_phase(str(target), phase_name=target.name)
        if ps is None:
            console.print(f"[red]Failed to parse {target}[/red]")
            sys.exit(1)
        if args.json:
            print(json.dumps(ps.to_dict(), indent=2))
        else:
            _print_phase(ps)
        sys.exit(0 if ps.verdict != "UNHEALTHY" else 1)

    # Full run scoring
    rs = score_run(str(target))
    if not rs.phases:
        console.print(f"[red]No scorable phases found in {target}[/red]")
        console.print("[dim]Expected grid-snapshot.json, tick-history.json, run-result.json[/dim]")
        sys.exit(1)

    if args.json:
        print(json.dumps(rs.to_dict(), indent=2))
    else:
        _print_run(rs)

    sys.exit(0 if rs.verdict != "UNHEALTHY" else 1)


if __name__ == "__main__":
    main()
