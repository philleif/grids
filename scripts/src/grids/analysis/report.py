"""Main report orchestrator.

Coordinates all analysis modules to produce the full session report:
1. Parse stream.jsonl
2. Generate chat summaries
3. Analyze workflow
4. Identify key moments
5. Generate narrative + highlights
6. Generate retrospective
7. Build D3 visualization HTML
8. Write all output files
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from grids.analysis.stream_parser import ParsedStream, parse_stream, generate_chat_summaries
from grids.analysis.workflow import WorkflowBreakdown, analyze_workflow, identify_key_moments
from grids.analysis.narrative import Narrative, generate_narrative
from grids.analysis.retrospective import Retrospective, generate_retrospective
from grids.analysis.viz_builder import generate_html, save_data_files

console = Console(stderr=True)


@dataclass
class ReportResult:
    parsed: ParsedStream | None = None
    workflow: WorkflowBreakdown | None = None
    narrative: Narrative | None = None
    retrospective: Retrospective | None = None
    output_dir: str = ""
    files_written: list[str] = field(default_factory=list)
    llm_calls_used: int = 0
    elapsed_seconds: float = 0.0


def generate_report(
    run_dir: str | Path,
    use_llm: bool = True,
    viz_only: bool = False,
    retro_only: bool = False,
    verbose: bool = True,
) -> ReportResult:
    """Generate the full session analysis report from a run directory."""
    t0 = time.time()
    run_dir = Path(run_dir)
    result = ReportResult(output_dir=str(run_dir))

    # Validate inputs exist -- check top-level first, then sub-phase dirs
    stream_path = run_dir / "stream.jsonl"
    snapshot_path = run_dir / "grid-snapshot.json"

    if not snapshot_path.exists():
        # Pipeline run: find the best sub-phase directory with both files
        phase_dir = _find_best_phase_dir(run_dir)
        if phase_dir:
            if verbose:
                console.print(f"[dim]Pipeline run detected, using {phase_dir.name}/[/dim]")
            stream_path = phase_dir / "stream.jsonl"
            snapshot_path = phase_dir / "grid-snapshot.json"
        else:
            if not stream_path.exists():
                console.print(f"[red]No stream.jsonl found in {run_dir}[/red]")
                console.print("[dim]Run with --log-stream to generate stream data.[/dim]")
                return result
            console.print(f"[red]No grid-snapshot.json found in {run_dir} or sub-phases[/red]")
            return result

    if not stream_path.exists():
        console.print(f"[red]No stream.jsonl found in {stream_path.parent}[/red]")
        console.print("[dim]Run with --log-stream to generate stream data.[/dim]")
        return result

    # Load grid snapshot
    with open(snapshot_path, "r", encoding="utf-8") as f:
        grid_snapshot = json.load(f)

    # Step 1: Parse stream
    if verbose:
        console.print("[bold cyan]Parsing stream data...[/bold cyan]")
    parsed = parse_stream(stream_path)
    result.parsed = parsed

    if verbose:
        console.print(
            f"  {len(parsed.llm_calls)} LLM calls, {len(parsed.ticks)} ticks, "
            f"{parsed.total_tokens:,} tokens, {len(parsed.domains)} domains"
        )

    # Step 2: Generate chat summaries
    if verbose:
        console.print("[bold cyan]Generating chat summaries...[/bold cyan]")
    generate_chat_summaries(parsed, use_llm=use_llm)
    result.llm_calls_used += len(parsed.llm_calls) // 5 + 1 if use_llm else 0

    # Step 3: Save data files (always -- these support the viz)
    save_data_files(parsed, grid_snapshot, run_dir)
    result.files_written.extend(["_chat_data.json", "_viz_data.json"])

    if viz_only:
        # Generate viz HTML without narrative/retro
        if verbose:
            console.print("[bold cyan]Generating visualization...[/bold cyan]")
        run_name = _infer_run_name(parsed, run_dir)
        html = generate_html(parsed, grid_snapshot, run_name=run_name)
        viz_path = run_dir / "visualization-d3.html"
        with open(viz_path, "w", encoding="utf-8") as f:
            f.write(html)
        result.files_written.append("visualization-d3.html")
        result.elapsed_seconds = time.time() - t0
        if verbose:
            console.print(f"  [green]Saved: {viz_path}[/green]")
        return result

    # Step 4: Analyze workflow
    if verbose:
        console.print("[bold cyan]Analyzing workflow...[/bold cyan]")
    workflow = analyze_workflow(parsed)
    result.workflow = workflow

    # Step 5: Identify key moments
    if verbose:
        console.print("[bold cyan]Identifying key moments...[/bold cyan]")
    identify_key_moments(parsed, workflow, use_llm=use_llm)
    if use_llm:
        result.llm_calls_used += len(workflow.phases)

    narrative = None
    retrospective = None

    if not retro_only:
        # Step 6: Generate narrative
        if verbose:
            console.print("[bold cyan]Generating session narrative...[/bold cyan]")
        narrative = generate_narrative(parsed, workflow, use_llm=use_llm)
        result.narrative = narrative
        if use_llm:
            result.llm_calls_used += 2

    # Step 7: Generate retrospective
    if verbose:
        console.print("[bold cyan]Generating ANKOS retrospective...[/bold cyan]")
    ankos_path = _find_ankos_md(run_dir)
    retrospective = generate_retrospective(
        parsed, workflow, narrative or Narrative(),
        ankos_path=ankos_path,
        use_llm=use_llm,
    )
    result.retrospective = retrospective
    if use_llm:
        result.llm_calls_used += 1

    # Step 8: Save report.json
    # Compute two-level metrics retroactively from stream (GRD-6)
    routing_summary = parsed.compute_routing_summary()
    quality_summary = parsed.compute_quality_summary()

    report_data = {
        "generated_at": time.time(),
        "run_dir": str(run_dir),
        "brief": parsed.brief[:500],
        "grid_size": parsed.grid_size,
        "cell_count": parsed.cell_count,
        "total_ticks": len(parsed.ticks),
        "total_llm_calls": len(parsed.llm_calls),
        "total_tokens": parsed.total_tokens,
        "domains": parsed.domains,
        "two_level_metrics": {
            "routing": routing_summary.to_dict(),
            "quality": quality_summary.to_dict(),
        },
        "workflow": workflow.to_dict(),
        "narrative": narrative.to_dict() if narrative else None,
        "retrospective": retrospective.to_dict(),
        "report_llm_calls": result.llm_calls_used,
    }

    report_json_path = run_dir / "report.json"
    with open(report_json_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2, default=str)
    result.files_written.append("report.json")

    # Step 9: Generate D3 visualization with report data
    if verbose:
        console.print("[bold cyan]Generating interactive visualization...[/bold cyan]")
    run_name = _infer_run_name(parsed, run_dir)
    html = generate_html(
        parsed, grid_snapshot,
        workflow=workflow,
        narrative=narrative,
        retrospective=retrospective,
        run_name=run_name,
    )
    viz_path = run_dir / "visualization-d3.html"
    with open(viz_path, "w", encoding="utf-8") as f:
        f.write(html)
    result.files_written.append("visualization-d3.html")

    # Also save a separate report.html (same content, distinct filename)
    report_html_path = run_dir / "report.html"
    with open(report_html_path, "w", encoding="utf-8") as f:
        f.write(html)
    result.files_written.append("report.html")

    result.elapsed_seconds = time.time() - t0

    if verbose:
        _print_summary(result)

    # Save reflection to journal if meta/reflections is available
    _save_reflection(result, run_dir)

    return result


def _find_best_phase_dir(run_dir: Path) -> Path | None:
    """Find the sub-phase directory with the most LLM events in its stream.jsonl.

    Prefers directories that have both stream.jsonl and grid-snapshot.json.
    Falls back to the latest phase directory if multiple candidates exist.
    """
    candidates = []
    for child in sorted(run_dir.iterdir()):
        if not child.is_dir() or not child.name.startswith("phase-"):
            continue
        stream = child / "stream.jsonl"
        snapshot = child / "grid-snapshot.json"
        if snapshot.exists() and stream.exists():
            # Count llm_start events as a proxy for richness
            try:
                llm_count = sum(1 for line in open(stream) if '"llm_start"' in line)
            except Exception:
                llm_count = 0
            candidates.append((llm_count, child))
        elif snapshot.exists():
            # Has snapshot but no stream -- still a candidate (stream may be empty)
            candidates.append((0, child))

    if not candidates:
        return None

    # Return the one with the most LLM events (richest stream data)
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _infer_run_name(parsed: ParsedStream, run_dir: Path) -> str:
    """Infer a human-readable name for the run."""
    # Try seed filename
    if parsed.seed:
        name = Path(parsed.seed).stem
        return name.replace("-", " ").replace("_", " ").title()
    # Fall back to directory name
    return run_dir.name


def _find_ankos_md(run_dir: Path) -> Path | None:
    """Find the ANKOS.md file by walking up from the run directory."""
    current = run_dir
    for _ in range(5):
        ankos = current / "ANKOS.md"
        if ankos.exists():
            return ankos
        current = current.parent
    return None


def _save_reflection(result: ReportResult, run_dir: Path) -> None:
    """Save the retrospective as a Reflection entry if the journal system is available."""
    if not result.retrospective:
        return
    try:
        from grids.meta.reflections import Reflection, ReflectionJournal

        journal_dir = run_dir.parent / "reflections"
        journal = ReflectionJournal(str(journal_dir))

        retro = result.retrospective
        reflection = Reflection(
            agent="grids-report",
            task_id=str(run_dir.name),
            went_well=retro.what_worked[:5],
            went_poorly=retro.what_didnt[:5],
            bottlenecks=[],
            time_spent=result.elapsed_seconds,
            iterations_needed=0,
            confidence=retro.health_score,
            suggestions=retro.what_to_try[:5],
        )
        journal.add(reflection)
    except Exception:
        pass


def _print_summary(result: ReportResult) -> None:
    """Print the report generation summary."""
    retro = result.retrospective

    health = ""
    if retro:
        health = f"\nHealth score: {retro.health_score:.0%}"
        if retro.what_worked:
            health += f"\nWorked: {retro.what_worked[0][:100]}"
        if retro.what_didnt:
            health += f"\nFailed: {retro.what_didnt[0][:100]}"

    console.print(Panel(
        f"Files written: {', '.join(result.files_written)}\n"
        f"LLM calls used: {result.llm_calls_used}\n"
        f"Time: {result.elapsed_seconds:.1f}s"
        f"{health}",
        title="Report Generated",
        border_style="bold green",
    ))
