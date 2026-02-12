"""grids-run -- the CA execution CLI.

Usage:
    grids-run "Build a calling cards design studio for creative professionals"
    grids-run --seed seeds/calling-cards.yaml
    grids-run --domains design,production-tech,editorial "Build a zine layout tool"
    grids-run --brief briefs/calling-cards.md --max-ticks 30 --verbose
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from grids.orchestration.grid import AgentGrid, Neighborhood
from grids.orchestration.seed import seed_from_domains, seed_from_yaml, seed_phase1b, seed_phase2, inject_brief
from grids.orchestration.tick import run, RunResult
from grids.orchestration.invoke import make_invoke_fn, consolidate_analysis, consolidate_product_spec
from grids.orchestration.validate import validate_build, ValidationResult as BuildValidationResult

console = Console(stderr=True)


class StreamLogger:
    """Captures every LLM call + tick event to a JSONL file for analysis."""

    def __init__(self, path: str):
        self._path = path
        self._f = open(path, "w", encoding="utf-8")
        self._seq = 0
        self._phase_entries: list[str] = []

    def log(self, event_type: str, **data):
        self._seq += 1
        entry = {"seq": self._seq, "ts": time.time(), "type": event_type, **data}
        line = json.dumps(entry, default=str) + "\n"
        self._f.write(line)
        self._f.flush()
        self._phase_entries.append(line)

    def start_phase(self):
        """Mark the start of a new phase. Resets the per-phase buffer."""
        self._phase_entries = []

    def save_phase_stream(self, phase_dir: str, run_start_data: dict | None = None):
        """Save the accumulated phase entries as a standalone stream.jsonl in the phase dir."""
        path = os.path.join(phase_dir, "stream.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            if run_start_data:
                f.write(json.dumps(run_start_data, default=str) + "\n")
            for line in self._phase_entries:
                f.write(line)

    def close(self):
        self._f.close()


def main():
    parser = argparse.ArgumentParser(
        description="Run the ANKOS cellular automaton grid",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  grids-run "Build a calling cards design studio"\n'
            '  grids-run --seed seeds/calling-cards.yaml\n'
            '  grids-run --domains design,production-tech "Build a zine tool"\n'
        ),
    )
    parser.add_argument("brief", nargs="?", help="Brief text (or @file to read from file)")
    parser.add_argument("--seed", "-s", help="Seed YAML file (overrides --domains and brief)")
    parser.add_argument("--domains", "-d", help="Comma-separated domain names")
    parser.add_argument("--max-ticks", "-t", type=int, default=30, help="Max ticks (default: 30)")
    parser.add_argument("--quiescence", "-q", type=int, default=3, help="Idle ticks before stopping (default: 3)")
    parser.add_argument("--output", "-o", help="Output directory (default: tmp/run-<timestamp>)")
    parser.add_argument("--grid-width", type=int, help="Override grid width")
    parser.add_argument("--grid-height", type=int, help="Override grid height")
    parser.add_argument("--neighborhood", choices=["von_neumann", "moore"], default="moore")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose tick output")
    parser.add_argument("--ascii", action="store_true", help="Show ASCII grid each tick")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument("--tui", action="store_true", help="Rich Live TUI with streaming LLM output")
    parser.add_argument("--record", "-r", help="Record run to MP4 video (path, or auto-named if --tui)")
    parser.add_argument("--log-stream", action="store_true", help="Log full agent emit/LLM stream to JSONL")
    parser.add_argument("--search-rules", metavar="ROLE",
                        help="Run rule space search for a role (master/sub/critique/research/execution)")
    parser.add_argument("--evolve-rules", metavar="ROLE",
                        help="Run evolutionary rule search for a role")
    parser.add_argument("--rule-generations", type=int, default=3, help="Generations for --evolve-rules")
    parser.add_argument("--rule-population", type=int, default=10, help="Population per generation")
    parser.add_argument("--use-best-rules", action="store_true",
                        help="Use best-known rules from registry instead of defaults")
    parser.add_argument("--phases", choices=["1a", "1b", "2", "all"], default="all",
                        help="Which phases to run (default: all three)")
    parser.add_argument("--phase1a-ticks", type=int, default=15,
                        help="Max ticks for Phase 1a domain analysis (default: 15)")
    parser.add_argument("--phase1b-ticks", type=int, default=8,
                        help="Max ticks for Phase 1b product spec (default: 8)")
    parser.add_argument("--phase2-ticks", type=int, default=15,
                        help="Max ticks for Phase 2 execution (default: 15)")
    parser.add_argument("--spec-input", help="Skip Phase 1, use this JSON spec file for Phase 2")
    parser.add_argument("--report", action="store_true",
                        help="Auto-generate analysis report after run (requires --log-stream)")
    parser.add_argument("--stuck-threshold", type=int, default=2,
                        help="Consecutive ticks with unprocessed inbox before stuck warning (default: 2)")
    parser.add_argument("--skip-validation", action="store_true",
                        help="Skip Phase 2b build validation after code generation")
    parser.add_argument("--phase2b-max-rework", type=int, default=2,
                        help="Max rework iterations for Phase 2b validation (default: 2)")
    parser.add_argument("--phase2b-rework-ticks", type=int, default=8,
                        help="Max ticks per Phase 2b rework iteration (default: 8)")
    parser.add_argument("--no-screenshots", action="store_true",
                        help="Skip screenshot capture during Phase 2b validation")
    args = parser.parse_args()

    # Rule space search mode (no grid run, just rule evaluation)
    if args.search_rules or args.evolve_rules:
        _run_rule_search(args)
        return

    # Resolve brief
    brief_text = None
    if args.brief:
        if args.brief.startswith("@") and os.path.isfile(args.brief[1:]):
            with open(args.brief[1:], "r", encoding="utf-8") as f:
                brief_text = f.read().strip()
        else:
            brief_text = args.brief

    # Build grid
    seed_config = {}
    if args.seed:
        grid, seed_config = seed_from_yaml(args.seed)
        if not brief_text:
            # Brief might be in seed config
            for w in seed_config.get("initial_work", []):
                if w.get("kind") == "brief_chunk":
                    brief_text = w.get("content", "")
                    break
    else:
        domains = args.domains.split(",") if args.domains else None
        grid = seed_from_domains(
            domains=domains,
            grid_width=args.grid_width,
            grid_height=args.grid_height,
            neighborhood=Neighborhood(args.neighborhood),
        )

    # Optionally replace default rules with best-known from registry
    if args.use_best_rules:
        _apply_best_rules(grid)

    # Apply stuck-cell detection threshold to all cells
    if args.stuck_threshold != 2:
        for cell in grid.all_cells():
            cell.stuck_threshold = args.stuck_threshold

    if not args.seed and brief_text:
        inject_brief(grid, brief_text)

    if not brief_text and not args.seed:
        console.print("[red]No brief provided. Use positional argument or --seed.[/red]")
        sys.exit(1)

    # Output directory
    output_dir = args.output or f"tmp/run-{int(time.time())}"
    os.makedirs(output_dir, exist_ok=True)

    # Print header
    if not args.json:
        console.print(Panel(
            brief_text or "(from seed file)",
            title="ANKOS Grid Run",
            border_style="cyan",
        ))
        _print_grid_info(grid)
        console.print()

    # Stream logger
    stream_logger = None
    if args.log_stream:
        stream_log_path = os.path.join(output_dir, "stream.jsonl")
        stream_logger = StreamLogger(stream_log_path)
        stream_logger.log("run_start", brief=brief_text, seed=args.seed,
                          grid_size=f"{grid.width}x{grid.height}",
                          cell_count=len(grid.cells))

    # Three-phase mode (default: all phases)
    if args.tui or args.phases != "all":
        _run_phased(args, grid, seed_config if args.seed else {}, brief_text, output_dir, stream_logger)
        return

    # Record-only mode (no TUI)
    if args.record:
        from grids.orchestration.recorder import VideoRecorder

        video_path = args.record if args.record.endswith(".mp4") else os.path.join(output_dir, "run.mp4")
        recorder = VideoRecorder(grid, video_path)
        recorder.start()
        recorder.add_stream_event("ANKOS Grid Run")
        recorder.add_stream_event(f"Grid: {grid.width}x{grid.height}")
        recorder.write_hold(seconds=2.0)

        invoke_fn = _wrap_with_logger(make_invoke_fn(verbose=args.verbose), stream_logger)

        def on_tick_recorded(result):
            recorder.on_tick(result)
            recorder.write_hold(seconds=0.5)
            if stream_logger:
                stream_logger.log("tick", tick=result.tick,
                                  actions=result.actions_taken,
                                  llm_calls=result.llm_calls,
                                  emitted=result.items_emitted,
                                  elapsed=result.elapsed_seconds,
                                  routing_scheduled=result.propagations + result.rejected,
                                  routing_delivered=result.propagations,
                                  routing_rejected=result.rejected,
                                  critique_scores=result.critique_scores,
                                  critique_verdicts=result.critique_verdicts,
                                  rework_count=result.rework_count)
            if args.ascii:
                console.print(f"\n[dim]--- Tick {result.tick} ---[/dim]")
                console.print(grid.ascii_view())

        result = run(
            grid,
            invoke_fn,
            max_ticks=args.max_ticks,
            quiescence_ticks=args.quiescence,
            verbose=not args.json,
            on_tick=on_tick_recorded,
        )

        recorder.add_stream_event("--- RUN COMPLETE ---")
        recorder.write_hold(seconds=3.0)
        recorder.stop()
        console.print(f"\n[green]Video saved: {video_path}[/green]")

        _save_results(output_dir, grid, result, brief_text)
        _close_logger(stream_logger, result, output_dir)
        if not args.json:
            _print_summary(result, output_dir)
        return

    # Standard mode (no TUI, no recording)
    invoke_fn = _wrap_with_logger(make_invoke_fn(verbose=args.verbose), stream_logger)

    def on_tick(result):
        if stream_logger:
            stream_logger.log("tick", tick=result.tick,
                              actions=result.actions_taken,
                              llm_calls=result.llm_calls,
                              emitted=result.items_emitted,
                              elapsed=result.elapsed_seconds,
                              routing_scheduled=result.propagations + result.rejected,
                              routing_delivered=result.propagations,
                              routing_rejected=result.rejected,
                              critique_scores=result.critique_scores,
                              critique_verdicts=result.critique_verdicts,
                              rework_count=result.rework_count)
        if args.ascii:
            console.print(f"\n[dim]--- Tick {result.tick} ---[/dim]")
            console.print(grid.ascii_view())

    result = run(
        grid,
        invoke_fn,
        max_ticks=args.max_ticks,
        quiescence_ticks=args.quiescence,
        verbose=not args.json,
        on_tick=on_tick,
    )

    # Save results
    _save_results(output_dir, grid, result, brief_text)
    _close_logger(stream_logger, result, output_dir)

    if args.json:
        print(json.dumps({
            "ticks": result.total_ticks,
            "llm_calls": result.total_llm_calls,
            "artifacts": len(result.artifacts),
            "quiescent": result.quiescent,
            "elapsed": result.elapsed_seconds,
            "output_dir": output_dir,
        }, indent=2))
    else:
        console.print()
        _print_summary(result, output_dir)

    # Auto-generate report if requested
    if args.report and args.log_stream:
        _run_report(output_dir)


def _run_phased(args, grid, seed_config, brief_text, output_dir, stream_logger):
    """Run the three-phase pipeline: 1a (domain analysis) -> 1b (product spec) -> 2 (build).

    Phase 1a: Domain expert analysis via the full CA grid.
              Output: domain analyses from all cells.
    Phase 1b: Product specification via a mini-grid of product-focused cells.
              Input: consolidated domain analysis. Output: concrete product spec.
    Phase 2:  Code execution via an execution-focused grid.
              Input: consolidated product spec. Output: working software files.

    Each phase runs its own grid to quiescence (or tick limit), then a
    consolidation LLM call bridges to the next phase.
    """
    from grids.orchestration.tui import run_with_tui
    from grids.orchestration.seed import seed_phase1b, seed_phase2

    phases_to_run = args.phases
    project_config = seed_config.get("project", {})
    domains = seed_config.get("domains", [])
    complexity_budget = seed_config.get("complexity_budget", {})
    total_llm = 0
    total_ticks = 0
    all_artifacts = []

    video_path = None
    if args.record:
        video_path = args.record if args.record.endswith(".mp4") else os.path.join(output_dir, "run.mp4")

    # Build a run_start dict for per-phase streams
    _phase_run_start = {
        "seq": 0, "ts": time.time(), "type": "run_start",
        "brief": brief_text,
        "seed": getattr(args, "seed", None) or "",
    }

    # --- Phase 1a: Domain Analysis ---
    if phases_to_run in ("1a", "all"):
        console.print(Panel(
            f"Domain experts analyze the brief through {len(grid.cells)} cells.",
            title="Phase 1a: Domain Analysis",
            border_style="cyan",
        ))
        if stream_logger:
            stream_logger.start_phase()
            stream_logger.log("phase_start", phase="1a",
                              grid_size=f"{grid.width}x{grid.height}",
                              cell_count=len(grid.cells))

        if args.tui:
            result_1a = run_with_tui(
                grid,
                max_ticks=args.phase1a_ticks,
                quiescence_ticks=args.quiescence,
                use_true_streaming=True,
                video_path=video_path,
                stream_logger=stream_logger,
            )
        else:
            invoke_fn = _wrap_with_logger(make_invoke_fn(verbose=args.verbose), stream_logger)
            result_1a = run(grid, invoke_fn, max_ticks=args.phase1a_ticks,
                            quiescence_ticks=args.quiescence, verbose=not args.json,
                            on_tick=_make_tick_logger(stream_logger))

        total_llm += result_1a.total_llm_calls
        total_ticks += result_1a.total_ticks

        # Save Phase 1a results
        phase1a_dir = os.path.join(output_dir, "phase-1a")
        os.makedirs(phase1a_dir, exist_ok=True)
        _save_results(phase1a_dir, grid, result_1a, brief_text)
        if stream_logger:
            rs = {**_phase_run_start, "grid_size": f"{grid.width}x{grid.height}", "cell_count": len(grid.cells)}
            stream_logger.save_phase_stream(phase1a_dir, run_start_data=rs)

        if not args.json:
            console.print(Panel(
                f"Ticks: {result_1a.total_ticks} | LLM calls: {result_1a.total_llm_calls} | "
                f"Items: {result_1a.total_items_emitted}",
                title="Phase 1a Complete",
                border_style="green",
            ))

        if phases_to_run == "1a":
            _print_phase_summary(output_dir, total_ticks, total_llm, all_artifacts)
            if stream_logger:
                stream_logger.close()
            return

        # Bridge: consolidate domain analysis
        console.print("\n[bold magenta]Consolidating domain analysis...[/bold magenta]")
        if stream_logger:
            stream_logger.log("consolidation_start", phase="1a->1b")

        domain_analysis = consolidate_analysis(grid, brief_text, project_config)
        total_llm += 1

        # Save the analysis
        analysis_path = os.path.join(output_dir, "domain-analysis.json")
        with open(analysis_path, "w", encoding="utf-8") as f:
            json.dump(domain_analysis, f, indent=2, default=str)
        console.print(f"  [green]Domain analysis saved: {analysis_path}[/green]")

        if stream_logger:
            stream_logger.log("consolidation_end", phase="1a->1b",
                              output_path=analysis_path,
                              output_preview=str(domain_analysis)[:500])
    else:
        domain_analysis = None

    # --- Phase 1b: Product Specification ---
    if phases_to_run in ("1b", "all"):
        # If running 1b standalone, load analysis from file
        if domain_analysis is None:
            analysis_path = os.path.join(output_dir, "domain-analysis.json")
            if os.path.exists(analysis_path):
                with open(analysis_path, "r", encoding="utf-8") as f:
                    domain_analysis = json.load(f)
            else:
                console.print("[red]No domain analysis found. Run Phase 1a first or provide --spec-input.[/red]")
                return

        console.print(Panel(
            "Product-designer, systems-architect, UX-specifier, integration-planner, "
            "and product-critique cells iterate to produce a concrete product spec.",
            title="Phase 1b: Product Specification",
            border_style="magenta",
        ))

        grid_1b = seed_phase1b(
            domain_analysis=domain_analysis,
            brief=brief_text,
            project_config=project_config,
            complexity_budget=complexity_budget,
        )

        if stream_logger:
            stream_logger.start_phase()
            stream_logger.log("phase_start", phase="1b",
                              grid_size=f"{grid_1b.width}x{grid_1b.height}",
                              cell_count=len(grid_1b.cells))

        if not args.json:
            _print_grid_info(grid_1b)

        if args.tui:
            result_1b = run_with_tui(
                grid_1b,
                max_ticks=args.phase1b_ticks,
                quiescence_ticks=args.quiescence,
                use_true_streaming=True,
                stream_logger=stream_logger,
            )
        else:
            invoke_fn = _wrap_with_logger(make_invoke_fn(verbose=args.verbose), stream_logger)
            result_1b = run(grid_1b, invoke_fn, max_ticks=args.phase1b_ticks,
                            quiescence_ticks=args.quiescence, verbose=not args.json,
                            on_tick=_make_tick_logger(stream_logger))

        total_llm += result_1b.total_llm_calls
        total_ticks += result_1b.total_ticks

        # Save Phase 1b results
        phase1b_dir = os.path.join(output_dir, "phase-1b")
        os.makedirs(phase1b_dir, exist_ok=True)
        _save_results(phase1b_dir, grid_1b, result_1b, brief_text)
        if stream_logger:
            rs = {**_phase_run_start, "grid_size": f"{grid_1b.width}x{grid_1b.height}", "cell_count": len(grid_1b.cells)}
            stream_logger.save_phase_stream(phase1b_dir, run_start_data=rs)

        if not args.json:
            console.print(Panel(
                f"Ticks: {result_1b.total_ticks} | LLM calls: {result_1b.total_llm_calls} | "
                f"Items: {result_1b.total_items_emitted}",
                title="Phase 1b Complete",
                border_style="green",
            ))

        if phases_to_run == "1b":
            _print_phase_summary(output_dir, total_ticks, total_llm, all_artifacts)
            if stream_logger:
                stream_logger.close()
            return

        # Bridge: consolidate product spec from Phase 1b grid
        console.print("\n[bold magenta]Consolidating product specification...[/bold magenta]")
        if stream_logger:
            stream_logger.log("consolidation_start", phase="1b->2")

        product_spec = consolidate_product_spec(grid_1b, brief_text, project_config, complexity_budget)
        total_llm += 2  # merge pass + convergence pass

        spec_path = os.path.join(output_dir, "product-spec.json")
        with open(spec_path, "w", encoding="utf-8") as f:
            json.dump(product_spec, f, indent=2, default=str)
        console.print(f"  [green]Product spec saved: {spec_path}[/green]")

        if stream_logger:
            stream_logger.log("consolidation_end", phase="1b->2",
                              output_path=spec_path,
                              output_preview=str(product_spec)[:500])
    else:
        product_spec = None

    # --- Phase 2: Execution (Build) ---
    if phases_to_run in ("2", "all"):
        # If running Phase 2 standalone, load spec from file
        if product_spec is None:
            if args.spec_input:
                spec_path = args.spec_input
            else:
                spec_path = os.path.join(output_dir, "product-spec.json")
            if os.path.exists(spec_path):
                with open(spec_path, "r", encoding="utf-8") as f:
                    product_spec = json.load(f)
            else:
                console.print("[red]No product spec found. Run Phase 1b first or provide --spec-input.[/red]")
                return

        console.print(Panel(
            "Coder, tester, and runner cells build working software from the product spec.",
            title="Phase 2: Build",
            border_style="bright_white",
        ))

        # Load domain analysis for consultant cells if available
        if domain_analysis is None:
            da_path = os.path.join(output_dir, "domain-analysis.json")
            if os.path.exists(da_path):
                with open(da_path, "r", encoding="utf-8") as f:
                    domain_analysis = json.load(f)

        activate_consultants = seed_config.get("execution", {}).get("activate_consultants", True)

        grid_2 = seed_phase2(
            consolidated_spec=product_spec,
            project_config=project_config,
            domains=domains,
            activate_consultants=activate_consultants,
            domain_analysis=domain_analysis,
        )

        if stream_logger:
            stream_logger.start_phase()
            stream_logger.log("phase_start", phase="2",
                              grid_size=f"{grid_2.width}x{grid_2.height}",
                              cell_count=len(grid_2.cells))

        if not args.json:
            _print_grid_info(grid_2)

        if args.tui:
            result_2 = run_with_tui(
                grid_2,
                max_ticks=args.phase2_ticks,
                quiescence_ticks=args.quiescence,
                use_true_streaming=True,
                stream_logger=stream_logger,
            )
        else:
            invoke_fn = _wrap_with_logger(make_invoke_fn(verbose=args.verbose), stream_logger)
            result_2 = run(grid_2, invoke_fn, max_ticks=args.phase2_ticks,
                           quiescence_ticks=args.quiescence, verbose=not args.json,
                           on_tick=_make_tick_logger(stream_logger))

        total_llm += result_2.total_llm_calls
        total_ticks += result_2.total_ticks
        all_artifacts.extend(result_2.artifacts)

        # Save Phase 2 results
        phase2_dir = os.path.join(output_dir, "phase-2")
        os.makedirs(phase2_dir, exist_ok=True)
        _save_results(phase2_dir, grid_2, result_2, brief_text)
        if stream_logger:
            rs = {**_phase_run_start, "grid_size": f"{grid_2.width}x{grid_2.height}", "cell_count": len(grid_2.cells)}
            stream_logger.save_phase_stream(phase2_dir, run_start_data=rs)

        # Write code files to output directory
        _write_code_artifacts(output_dir, result_2, project_config)

        # --- Phase 2b: Build Validation (GRD-7) ---
        if not args.skip_validation:
            app_dir = project_config.get("output_dir", os.path.join(output_dir, "app"))
            validation_result, rework_llm, rework_ticks = _run_phase2b(
                args=args,
                app_dir=app_dir,
                output_dir=output_dir,
                project_config=project_config,
                product_spec=product_spec,
                domains=domains,
                domain_analysis=domain_analysis,
                seed_config=seed_config if args.seed else {},
                stream_logger=stream_logger,
                brief_text=brief_text,
            )
            total_llm += rework_llm
            total_ticks += rework_ticks

            # Save validation results
            val_path = os.path.join(output_dir, "validation-result.json")
            with open(val_path, "w", encoding="utf-8") as f:
                json.dump(validation_result.to_dict(), f, indent=2)
            console.print(f"  [green]Validation results saved: {val_path}[/green]")

    # Final summary
    _print_phase_summary(output_dir, total_ticks, total_llm, all_artifacts)

    if stream_logger:
        stream_logger.log("run_end", total_ticks=total_ticks,
                          total_llm=total_llm,
                          artifacts=len(all_artifacts))
        stream_logger.close()
        console.print(f"[green]Stream log: {os.path.join(output_dir, 'stream.jsonl')}[/green]")

    # Auto-generate report if requested
    if args.report and args.log_stream:
        _run_report(output_dir)


def _run_phase2b(
    args,
    app_dir: str,
    output_dir: str,
    project_config: dict,
    product_spec: dict | str | None,
    domains: list[str] | None,
    domain_analysis: dict | str | None,
    seed_config: dict,
    stream_logger: StreamLogger | None,
    brief_text: str = "",
) -> tuple[BuildValidationResult, int, int]:
    """Phase 2b: Build validation with rework loop (GRD-7).

    Runs build checks (dependencies, TypeScript, route conflicts, assets)
    and optionally captures screenshots. If errors are found, re-seeds a
    mini execution grid with rework items and re-runs Phase 2, then
    re-writes code and re-validates. Repeats up to max_rework iterations.

    Returns (final_validation_result, total_llm_calls, total_ticks).
    """
    from grids.orchestration.seed import seed_phase2

    max_rework = args.phase2b_max_rework
    rework_ticks = args.phase2b_rework_ticks
    run_screenshots = not args.no_screenshots
    total_llm = 0
    total_ticks = 0
    validation_result = None

    for rework_iter in range(max_rework + 1):  # +1 for initial validation
        phase_label = "initial" if rework_iter == 0 else f"rework {rework_iter}"

        console.print(Panel(
            f"Validating {app_dir} ({phase_label})",
            title="Phase 2b: Build Validation (GRD-7)",
            border_style="bright_white",
        ))

        if stream_logger:
            stream_logger.log("phase_start", phase=f"2b-{phase_label}",
                              app_dir=app_dir,
                              rework_iteration=rework_iter)

        validation_result = validate_build(
            app_dir=app_dir,
            project_config=project_config,
            output_dir=output_dir,
            run_screenshots=run_screenshots,
            verbose=True,
        )

        if stream_logger:
            stream_logger.log("validation_complete",
                              phase=f"2b-{phase_label}",
                              passed=validation_result.passed,
                              errors=validation_result.error_count,
                              warnings=validation_result.warning_count,
                              screenshots=len(validation_result.screenshots),
                              elapsed=validation_result.elapsed_seconds)

        if validation_result.passed:
            console.print(Panel(
                f"[bold green]Build validation passed ({phase_label})[/bold green]\n"
                f"Screenshots: {len(validation_result.screenshots)}\n"
                f"Warnings: {validation_result.warning_count}",
                title="Phase 2b Complete",
                border_style="green",
            ))
            return validation_result, total_llm, total_ticks

        # Validation failed -- check if we can rework
        if rework_iter >= max_rework:
            console.print(Panel(
                f"[bold yellow]Max rework iterations ({max_rework}) reached. "
                f"{validation_result.error_count} errors remain.[/bold yellow]",
                title="Phase 2b: Rework Exhausted",
                border_style="yellow",
            ))
            return validation_result, total_llm, total_ticks

        # Build rework items from validation errors
        rework_items = validation_result.to_rework_items()
        if not rework_items:
            console.print("  [dim]No actionable rework items from validation[/dim]")
            return validation_result, total_llm, total_ticks

        console.print(
            f"\n[bold magenta]Rework: {len(rework_items)} issues to fix "
            f"(iteration {rework_iter + 1}/{max_rework})[/bold magenta]"
        )

        # Re-seed Phase 2 grid with validation errors as rework context
        activate_consultants = seed_config.get("execution", {}).get("activate_consultants", True)

        # Build an enriched spec that includes the original spec + validation errors
        rework_spec = product_spec if isinstance(product_spec, dict) else {"spec": product_spec}
        rework_spec = dict(rework_spec)  # don't mutate original
        rework_spec["validation_errors"] = rework_items
        rework_spec["rework_iteration"] = rework_iter + 1
        rework_spec["rework_instructions"] = (
            "CRITICAL: The previous code output had build validation errors. "
            "You MUST fix ALL of the validation_errors listed below. "
            "Each error includes the category, message, affected file, and a suggestion. "
            "Focus on fixing these specific issues. Do not rewrite from scratch -- "
            "patch the existing code to resolve each error."
        )

        grid_rework = seed_phase2(
            consolidated_spec=rework_spec,
            project_config=project_config,
            domains=domains,
            activate_consultants=activate_consultants,
            domain_analysis=domain_analysis,
        )

        if stream_logger:
            stream_logger.start_phase()
            stream_logger.log("phase_start", phase=f"2-rework-{rework_iter + 1}",
                              grid_size=f"{grid_rework.width}x{grid_rework.height}",
                              cell_count=len(grid_rework.cells),
                              rework_items=len(rework_items))

        if args.tui:
            from grids.orchestration.tui import run_with_tui
            result_rework = run_with_tui(
                grid_rework,
                max_ticks=rework_ticks,
                quiescence_ticks=args.quiescence,
                use_true_streaming=True,
                stream_logger=stream_logger,
            )
        else:
            invoke_fn = _wrap_with_logger(make_invoke_fn(verbose=args.verbose), stream_logger)
            result_rework = run(grid_rework, invoke_fn, max_ticks=rework_ticks,
                                quiescence_ticks=args.quiescence, verbose=True,
                                on_tick=_make_tick_logger(stream_logger))

        total_llm += result_rework.total_llm_calls
        total_ticks += result_rework.total_ticks

        # Save rework results
        rework_dir = os.path.join(output_dir, f"phase-2b-rework-{rework_iter + 1}")
        os.makedirs(rework_dir, exist_ok=True)
        _save_results(rework_dir, grid_rework, result_rework, None)
        if stream_logger:
            rs = {"seq": 0, "ts": time.time(), "type": "run_start", "brief": brief_text,
                  "grid_size": f"{grid_rework.width}x{grid_rework.height}", "cell_count": len(grid_rework.cells)}
            stream_logger.save_phase_stream(rework_dir, run_start_data=rs)

        # Re-write code artifacts from rework output
        _write_code_artifacts(output_dir, result_rework, project_config)

        console.print(Panel(
            f"Ticks: {result_rework.total_ticks} | LLM calls: {result_rework.total_llm_calls} | "
            f"Items: {result_rework.total_items_emitted}",
            title=f"Phase 2 Rework {rework_iter + 1} Complete",
            border_style="magenta",
        ))

    # Should not reach here, but return last result
    return validation_result, total_llm, total_ticks


def _run_report(output_dir: str):
    """Run the analysis report generator on a completed run directory."""
    try:
        from grids.analysis.report import generate_report
        console.print("\n[bold magenta]Generating session report...[/bold magenta]")
        generate_report(output_dir, use_llm=True, verbose=True)
    except Exception as e:
        console.print(f"[yellow]Report generation failed: {e}[/yellow]")


def _print_phase_summary(output_dir: str, total_ticks: int, total_llm: int, artifacts: list):
    """Print final cross-phase summary."""
    console.print(Panel(
        f"Total ticks: {total_ticks}\n"
        f"Total LLM calls: {total_llm}\n"
        f"Artifacts: {len(artifacts)}\n"
        f"Output: {output_dir}/",
        title="Pipeline Complete",
        border_style="bold green",
    ))

    # List saved files
    for name in ["domain-analysis.json", "product-spec.json"]:
        path = os.path.join(output_dir, name)
        if os.path.exists(path):
            size = os.path.getsize(path)
            console.print(f"  [green]{name}[/green] ({size:,} bytes)")


def _write_code_artifacts(output_dir: str, result: RunResult, project_config: dict):
    """Extract code files from Phase 2 artifacts and write to disk."""
    app_dir = project_config.get("output_dir", os.path.join(output_dir, "app"))
    files_written = 0

    for art in result.artifacts:
        content = art.get("content")
        file_list = _extract_file_list(content)

        if not file_list and content:
            preview = str(content)[:120].replace("\n", " ")
            console.print(f"  [yellow]Warning: could not extract files from artifact "
                          f"({art.get('source', '?')}): {preview}...[/yellow]")

        for file_spec in file_list:
            if not isinstance(file_spec, dict):
                continue
            file_path = file_spec.get("path", "")
            file_content = file_spec.get("content", "")
            if not file_path or not file_content:
                continue

            # Normalize path: LLMs may emit absolute sandbox paths, relative
            # project paths, or bare filenames.  Strip any known prefix so
            # we end up with a path relative to app_dir.
            file_path = _normalize_artifact_path(file_path, app_dir)

            full_path = os.path.join(app_dir, file_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(file_content)
            files_written += 1

    if files_written:
        console.print(f"\n[bold green]Wrote {files_written} code files to {app_dir}/[/bold green]")


def _normalize_artifact_path(file_path: str, app_dir: str) -> str:
    """Normalize an LLM-emitted file path to be relative to app_dir.

    Handles patterns like:
      /home/user/apps/love-line/src/App.tsx  -> src/App.tsx
      apps/love-line/src/App.tsx             -> src/App.tsx
      src/App.tsx                            -> src/App.tsx  (unchanged)
    """
    # Strip shell quoting artifacts from heredoc paths
    # e.g. app/'(tabs)'/index.tsx -> app/(tabs)/index.tsx
    file_path = file_path.replace("'", "").replace('"', "")

    # Strip absolute sandbox paths (the LLM thinks it's writing to /home/user/...)
    abs_prefixes = ["/home/user/", "/tmp/", "/workspace/"]
    for prefix in abs_prefixes:
        if file_path.startswith(prefix):
            file_path = file_path[len(prefix):]
            break

    # Strip the app_dir-relative prefix (e.g. "apps/love-line/")
    app_base = os.path.basename(app_dir)
    # Try full app_dir match first
    if file_path.startswith(app_dir + "/"):
        file_path = file_path[len(app_dir) + 1:]
    else:
        # Try matching any path that ends with .../app_base/rest
        parts = file_path.split("/")
        for i, part in enumerate(parts):
            if part == app_base and i < len(parts) - 1:
                file_path = "/".join(parts[i + 1:])
                break

    return file_path


def _extract_heredoc_files(text: str) -> list[dict]:
    """Extract files from shell heredoc patterns like:
    cat > path/to/file << 'EOF'
    ...content...
    EOF
    """
    pattern = re.compile(
        r"cat\s+>\s*(\S+)\s*<<\s*['\"]?(\w+)['\"]?\s*\n"
        r"(.*?)\n\2",
        re.DOTALL,
    )
    files = []
    for m in pattern.finditer(text):
        path = m.group(1).strip()
        content = m.group(3)
        if path and content:
            files.append({"path": path, "content": content})
    return files


def _extract_xml_file_tags(text: str) -> list[dict]:
    """Extract files from XML-style tags like:
    <file path="path/to/file">
    ...content...
    </file>
    """
    pattern = re.compile(
        r'<file\s+path="([^"]+)"[^>]*>\s*\n?(.*?)\s*</file>',
        re.DOTALL,
    )
    files = []
    for m in pattern.finditer(text):
        path = m.group(1).strip()
        content = m.group(2)
        if path and content:
            files.append({"path": path, "content": content})
    return files


def _extract_markdown_file_blocks(text: str) -> list[dict]:
    """Extract files from markdown code blocks with filename annotations.

    Handles patterns like:
        ```tsx app/index.tsx
        ...code...
        ```

        // app/index.tsx
        ```tsx
        ...code...
        ```

        **app/index.tsx**
        ```tsx
        ...code...
        ```
    """
    files = []
    # Pattern 1: ```lang path/to/file.ext  (filename on the fence line)
    pattern1 = re.compile(
        r"```\w*\s+([\w./()\[\]\-]+\.(?:tsx?|jsx?|json|m?js|css|md|ya?ml|toml|config\.\w+))\s*\n"
        r"(.*?)\n```",
        re.DOTALL,
    )
    for m in pattern1.finditer(text):
        path = m.group(1).strip()
        content = m.group(2)
        if path and content and "/" in path:
            files.append({"path": path, "content": content})

    if files:
        return files

    # Pattern 2: filename on the line immediately before the fence
    # Matches: // path/to/file.ext\n```lang  or  **path/to/file.ext**\n```lang
    pattern2 = re.compile(
        r"(?:^|\n)\s*(?://|#|\*\*)\s*([\w./()\[\]\-]+\.(?:tsx?|jsx?|json|m?js|css|md|ya?ml|toml|config\.\w+))\s*\*{0,2}\s*\n"
        r"```\w*\s*\n(.*?)\n```",
        re.DOTALL,
    )
    for m in pattern2.finditer(text):
        path = m.group(1).strip()
        content = m.group(2)
        if path and content and "/" in path:
            files.append({"path": path, "content": content})

    return files


def _extract_file_list(content) -> list[dict]:
    """Extract a list of {path, content} file specs from artifact content.
    Handles: dict with 'files' key, dict with 'path'+'content' keys,
    JSON in code blocks, XML <file> tags, shell heredoc patterns, or
    markdown code blocks with filename annotations.
    Resilient to truncated JSON -- recovers all complete file entries."""
    if isinstance(content, dict):
        files = content.get("files", [])
        if files:
            return files
        if "path" in content and "content" in content:
            return [content]
        return []

    if isinstance(content, str):
        # Try to parse the whole string as JSON first
        try:
            parsed = json.loads(content)
            return _extract_file_list(parsed)
        except (json.JSONDecodeError, TypeError):
            pass

        # Extract JSON from ```json code blocks
        if "```" in content:
            for block in content.split("```"):
                block = block.strip()
                if block.startswith("json"):
                    block = block[4:].strip()
                # Try direct parse
                try:
                    parsed = json.loads(block)
                    files = _extract_file_list(parsed)
                    if files:
                        return files
                except (json.JSONDecodeError, TypeError):
                    pass
                # Truncated JSON: try to recover complete file entries
                recovered = _recover_truncated_files(block)
                if recovered:
                    return recovered

        # Try XML <file path="...">...</file> tags
        xml_files = _extract_xml_file_tags(content)
        if xml_files:
            return xml_files

        # Try shell heredoc patterns: cat > path << 'EOF'
        heredoc_files = _extract_heredoc_files(content)
        if heredoc_files:
            return heredoc_files

        # Try markdown code blocks with filename annotations
        md_files = _extract_markdown_file_blocks(content)
        if md_files:
            return md_files

    if isinstance(content, list):
        # Could be a raw list of file specs
        if content and isinstance(content[0], dict) and "path" in content[0]:
            return content

    return []


def _recover_truncated_files(text: str) -> list[dict]:
    """Recover complete file entries from truncated JSON.
    LLM output often gets cut off mid-file. We extract all files
    that have both a complete 'path' and 'content' field."""
    files = []
    # Find all "path": "..." patterns followed by "content": "..."
    # Use a regex to find complete file objects
    # Strategy: find each {"path": ..., "content": ...} object boundary
    # by looking for complete JSON string values
    try:
        # Try closing the JSON at various truncation points
        for suffix in ['"}]}', '"}\n]}', '"}]\n}', '"}\n]\n}']:
            try:
                parsed = json.loads(text + suffix)
                result = []
                if isinstance(parsed, dict):
                    result = parsed.get("files", [])
                elif isinstance(parsed, list):
                    result = parsed
                # Filter to only complete entries
                complete = [f for f in result if isinstance(f, dict)
                            and f.get("path") and f.get("content")]
                if complete:
                    return complete
            except (json.JSONDecodeError, TypeError):
                continue

        # More aggressive recovery: binary search for the last valid truncation point
        # Find where "files" array starts
        files_start = text.find('"files"')
        if files_start == -1:
            return []
        array_start = text.find('[', files_start)
        if array_start == -1:
            return []

        # Try progressively shorter substrings
        for end in range(len(text), array_start, -100):
            for suffix in ['"}]}', '"}\n]}']:
                try:
                    parsed = json.loads(text[:end] + suffix)
                    result = parsed.get("files", []) if isinstance(parsed, dict) else parsed
                    complete = [f for f in result if isinstance(f, dict)
                                and f.get("path") and f.get("content")]
                    if complete:
                        console.print(f"  [yellow]Recovered {len(complete)} files from truncated JSON "
                                      f"({len(text) - end} chars truncated)[/yellow]")
                        return complete
                except (json.JSONDecodeError, TypeError):
                    continue
    except Exception:
        pass
    return []


def _make_tick_logger(logger: StreamLogger | None):
    """Create an on_tick callback that logs tick events to the stream logger."""
    if logger is None:
        return None

    def on_tick(result):
        logger.log("tick",
                    tick=result.tick,
                    actions=result.actions_taken,
                    llm_calls=result.llm_calls,
                    emitted=result.items_emitted,
                    elapsed=round(result.elapsed_seconds, 3),
                    routing_scheduled=result.propagations + result.rejected,
                    routing_delivered=result.propagations,
                    routing_rejected=result.rejected,
                    critique_scores=result.critique_scores,
                    critique_verdicts=result.critique_verdicts,
                    rework_count=result.rework_count)

    return on_tick


def _wrap_with_logger(invoke_fn, logger: StreamLogger | None):
    """Wrap an invoke_fn to log every call to the stream logger."""
    if logger is None:
        return invoke_fn

    def logged_invoke(cell, action, work, neighbors):
        logger.log("llm_start",
                    domain=cell.domain, agent=cell.agent_type,
                    role=cell.role, action=action.value,
                    pos=f"{cell.position[0]},{cell.position[1]}",
                    work_preview=str(work.content)[:300] if work else None)
        result = invoke_fn(cell, action, work, neighbors)
        response_str = str(result) if result else ""
        logger.log("llm_end",
                    domain=cell.domain, agent=cell.agent_type,
                    action=action.value,
                    token_count=len(response_str.split()),
                    response=response_str[:2000])
        return result

    return logged_invoke


def _close_logger(logger: StreamLogger | None, result: RunResult, output_dir: str):
    if logger:
        logger.log("run_end", ticks=result.total_ticks,
                    llm_calls=result.total_llm_calls,
                    artifacts=len(result.artifacts),
                    elapsed=result.elapsed_seconds)
        logger.close()
        console.print(f"[green]Stream log: {os.path.join(output_dir, 'stream.jsonl')}[/green]")


def _print_grid_info(grid: AgentGrid):
    """Print grid topology info."""
    table = Table(title=f"Grid: {grid.width}x{grid.height} ({grid.neighborhood.value})")
    table.add_column("Domain", style="cyan")
    table.add_column("Cells", width=6)
    table.add_column("Types")

    # Group by domain
    domains: dict[str, list] = {}
    for cell in grid.all_cells():
        domains.setdefault(cell.domain, []).append(cell)

    for domain, cells in sorted(domains.items()):
        types = sorted(set(c.agent_type for c in cells))
        table.add_row(domain, str(len(cells)), ", ".join(types))

    console.print(table)
    console.print(f"  Total cells: {len(grid.cells)}")
    console.print(f"  Cells with work: {sum(1 for c in grid.all_cells() if c.has_work)}")


def _print_summary(result: RunResult, output_dir: str):
    """Print run summary."""
    r = result.routing
    q = result.quality
    avg_q = q.avg_critique_score
    avg_q_str = f"{avg_q:.1f}" if avg_q is not None else "n/a"
    verdicts = q.verdict_counts
    verdict_str = ", ".join(f"{k}={v}" for k, v in sorted(verdicts.items())) if verdicts else "none"

    console.print(Panel(
        f"Ticks: {result.total_ticks}\n"
        f"LLM calls: {result.total_llm_calls}\n"
        f"Items emitted: {result.total_items_emitted}\n"
        f"Artifacts: {len(result.artifacts)}\n"
        f"Quiescent: {result.quiescent}\n"
        f"Time: {result.elapsed_seconds}s\n"
        f"Output: {output_dir}/\n"
        f"\n"
        f"[bold]P(good) = P(routing) x P(quality)[/bold]\n"
        f"  Routing: {r.items_delivered}/{r.items_scheduled} delivered "
        f"({r.routing_efficiency:.0%} efficiency)\n"
        f"  Quality: avg score={avg_q_str}, verdicts=[{verdict_str}], "
        f"rework={q.rework_count}",
        title="Run Complete",
        border_style="green" if result.quiescent else "yellow",
    ))

    if result.artifacts:
        console.print("\n[bold]Artifacts:[/bold]")
        for i, art in enumerate(result.artifacts):
            source = art.get("source", "?")
            kind = art.get("kind", "?")
            tick = art.get("tick", "?")
            preview = str(art.get("content", ""))[:100]
            console.print(f"  {i + 1}. [{source}] {kind} (tick {tick}): {preview}...")


def _save_results(output_dir: str, grid: AgentGrid, result: RunResult, brief: str | None):
    """Save full run results to output directory."""
    # Grid snapshot
    snapshot_path = os.path.join(output_dir, "grid-snapshot.json")
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(grid.snapshot(), f, indent=2)

    # Run result
    result_path = os.path.join(output_dir, "run-result.json")
    result_data = {
        "brief": brief,
        "total_ticks": result.total_ticks,
        "total_llm_calls": result.total_llm_calls,
        "total_items_emitted": result.total_items_emitted,
        "quiescent": result.quiescent,
        "elapsed_seconds": result.elapsed_seconds,
        "artifacts_count": len(result.artifacts),
        # Two-level performance metrics (GRD-6)
        "routing": result.routing.to_dict(result.all_routing_records),
        "quality": result.quality.to_dict(),
    }
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result_data, f, indent=2)

    # Artifacts (one file each)
    artifacts_dir = os.path.join(output_dir, "artifacts")
    os.makedirs(artifacts_dir, exist_ok=True)
    for i, art in enumerate(result.artifacts):
        art_path = os.path.join(artifacts_dir, f"artifact-{i:03d}.json")
        with open(art_path, "w", encoding="utf-8") as f:
            json.dump(art, f, indent=2, default=str)

    # Tick history
    history_path = os.path.join(output_dir, "tick-history.json")
    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(
            [{"tick": t.tick, "actions": t.actions_taken, "llm": t.llm_calls,
              "emitted": t.items_emitted, "elapsed": t.elapsed_seconds,
              "routing": {"scheduled": t.propagations + t.rejected,
                          "delivered": t.propagations, "rejected": t.rejected},
              "quality": {"critique_scores": t.critique_scores,
                          "critique_verdicts": t.critique_verdicts,
                          "rework_count": t.rework_count}}
             for t in result.tick_history],
            f, indent=2,
        )


def _apply_best_rules(grid: AgentGrid):
    """Replace default rule tables with best-known variants from the registry."""
    from grids.orchestration.rule_search import RuleSearchHarness
    harness = RuleSearchHarness()
    replaced = 0
    for cell in grid.all_cells():
        best = harness.get_best(cell.role)
        if best is not None:
            cell.rule_table = best
            replaced += 1
    if replaced:
        console.print(f"  [magenta]Replaced {replaced} cells with best-known rule tables[/magenta]")


def _run_rule_search(args):
    """Run rule space search or evolutionary search."""
    from grids.orchestration.rule_search import RuleSearchHarness

    role = args.search_rules or args.evolve_rules
    brief = args.brief or "Build a design studio application"
    harness = RuleSearchHarness()

    if args.evolve_rules:
        console.print(Panel(
            f"Role: {role}\n"
            f"Generations: {args.rule_generations}\n"
            f"Population: {args.rule_population}\n"
            f"Brief: {brief[:80]}",
            title="Evolutionary Rule Search (NKS Ch. 2-6)",
            border_style="magenta",
        ))
        result = harness.evolve(
            role, brief=brief,
            generations=args.rule_generations,
            population=args.rule_population,
        )
    else:
        console.print(Panel(
            f"Role: {role}\nBrief: {brief[:80]}",
            title="Rule Space Search (NKS Ch. 2-6)",
            border_style="cyan",
        ))
        result = harness.search(role, brief=brief, n_candidates=args.rule_population)

    # Display results
    table = Table(title=f"Rule Search Results: {role}")
    table.add_column("Rank", width=4)
    table.add_column("Score", width=8)
    table.add_column("Rules", width=5)
    table.add_column("Gen", width=4)
    table.add_column("Fingerprint", width=18)
    table.add_column("vs Baseline")

    for i, c in enumerate(result.all_results[:15]):
        delta = c.score - result.baseline_score
        delta_str = f"+{delta:.1f}" if delta >= 0 else f"{delta:.1f}"
        style = "green" if delta > 0 else ("red" if delta < 0 else "dim")
        table.add_row(
            str(i + 1),
            f"{c.score:.1f}",
            str(len(c.rule_table.rules)),
            str(c.generation),
            c.fingerprint,
            f"[{style}]{delta_str}[/{style}]",
        )
    console.print(table)

    console.print(f"\n  Baseline score: {result.baseline_score:.1f}")
    if result.best:
        console.print(f"  Best score:     {result.best.score:.1f}")
        console.print(f"  Improvement:    {result.best.score - result.baseline_score:+.1f}")
    console.print(f"  Candidates tested: {result.candidates_tested}")
    console.print(f"  Time: {result.elapsed_seconds:.1f}s")

    # Show registry summary
    report = harness.report()
    if report:
        console.print("\n[bold]Registry Summary:[/bold]")
        for role_name, stats in sorted(report.items()):
            console.print(
                f"  {role_name}: {stats['tested']} tested, "
                f"best={stats['best_score']:.1f}, avg={stats['avg_score']:.1f}"
            )


if __name__ == "__main__":
    main()
