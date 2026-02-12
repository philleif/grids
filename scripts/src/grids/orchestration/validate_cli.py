"""CLI for Phase 2b build validation (GRD-7).

Standalone tool to validate a generated app directory:
    grids-validate apps/love-line
    grids-validate apps/love-line --no-screenshots --json
"""

from __future__ import annotations

import argparse
import json
import sys

from rich.console import Console

console = Console(stderr=True)


def main():
    parser = argparse.ArgumentParser(
        description="Validate generated code by running build checks (GRD-7)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  grids-validate apps/love-line\n"
            "  grids-validate apps/love-line --project-type expo --framework 'expo react-native'\n"
            "  grids-validate apps/calling-cards --no-screenshots --json\n"
        ),
    )
    parser.add_argument("app_dir", help="Path to the app directory to validate")
    parser.add_argument("--project-type", default="web-app", help="Project type (default: web-app)")
    parser.add_argument("--framework", default="", help="Framework string (e.g., 'expo react-native + tamagui')")
    parser.add_argument("--output-dir", help="Output directory for screenshots/results")
    parser.add_argument("--no-screenshots", action="store_true", help="Skip screenshot capture")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args()

    from grids.orchestration.validate import validate_build

    project_config = {
        "type": args.project_type,
        "framework": args.framework,
    }

    result = validate_build(
        app_dir=args.app_dir,
        project_config=project_config,
        output_dir=args.output_dir,
        run_screenshots=not args.no_screenshots,
        verbose=not args.json,
    )

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        # Save result
        if args.output_dir:
            import os
            result_path = os.path.join(args.output_dir, "validation-result.json")
            os.makedirs(os.path.dirname(result_path), exist_ok=True)
            with open(result_path, "w") as f:
                json.dump(result.to_dict(), f, indent=2)
            console.print(f"\n[green]Results saved: {result_path}[/green]")

    sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    main()
