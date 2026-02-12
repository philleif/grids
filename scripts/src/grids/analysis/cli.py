"""grids-report CLI entry point.

Usage:
    grids-report tmp/run-1770560001/
    grids-report tmp/run-1770560001/ --viz-only
    grids-report tmp/run-1770560001/ --retro-only
    grids-report tmp/run-1770560001/ --json
    grids-report tmp/run-1770560001/ --no-llm
"""

from __future__ import annotations

import argparse
import json
import sys

from rich.console import Console

console = Console(stderr=True)


def main():
    parser = argparse.ArgumentParser(
        description="Generate an analysis report from a GRIDS run directory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  grids-report tmp/run-1770560001/\n"
            "  grids-report tmp/run-1770560001/ --viz-only\n"
            "  grids-report tmp/run-1770560001/ --retro-only --no-llm\n"
            "  grids-report tmp/run-1770560001/ --json\n"
        ),
    )
    parser.add_argument("run_dir", help="Path to the run directory (contains stream.jsonl)")
    parser.add_argument("--viz-only", action="store_true",
                        help="Generate only the D3 visualization (no LLM calls for narrative/retro)")
    parser.add_argument("--retro-only", action="store_true",
                        help="Generate only the retrospective (skip narrative)")
    parser.add_argument("--no-llm", action="store_true",
                        help="Use heuristic analysis only, no LLM calls")
    parser.add_argument("--json", action="store_true",
                        help="Output report.json to stdout")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="Suppress progress output")

    args = parser.parse_args()

    from grids.analysis.report import generate_report

    result = generate_report(
        run_dir=args.run_dir,
        use_llm=not args.no_llm,
        viz_only=args.viz_only,
        retro_only=args.retro_only,
        verbose=not args.quiet and not args.json,
    )

    if args.json:
        report_path = f"{args.run_dir}/report.json"
        try:
            with open(report_path, "r", encoding="utf-8") as f:
                print(f.read())
        except FileNotFoundError:
            # If report.json wasn't generated (viz-only mode), output basic stats
            print(json.dumps({
                "run_dir": args.run_dir,
                "files_written": result.files_written,
                "llm_calls_used": result.llm_calls_used,
                "elapsed_seconds": result.elapsed_seconds,
            }, indent=2))

    if not result.files_written:
        sys.exit(1)


if __name__ == "__main__":
    main()
