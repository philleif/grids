"""Seed REFERENCE directory with PDFs declared in domain YAML configs.

Reads every domains/*.yaml, extracts sources.pdfs entries, checks which
files already exist on disk, and attempts to download missing ones via
the grids.book.finder cascade (IA -> Open Library -> DuckDuckGo -> Anna's Archive).

Usage:
    grids-seed                          # seed all domains
    grids-seed --domain editorial       # seed one domain
    grids-seed --report                 # gap analysis only
    grids-seed --dry-run                # show what would be downloaded
    grids-seed --rip                    # enable IA page-rip fallback
    grids-seed --skip-annas             # skip shadow libraries
"""

import argparse
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from rich.console import Console
from rich.table import Table

from grids.book.finder import find_book, try_download, download_pdf

console = Console(stderr=True)

ROOT = Path(__file__).resolve().parents[4]  # -> GRIDS/
DOMAINS_DIR = ROOT / "domains"
REFERENCE_DIR = ROOT / "REFERENCE"
SEED_IGNORE = REFERENCE_DIR / ".seed-ignore"
SEED_LOG = REFERENCE_DIR / "SEED-LOG.md"


# ---------------------------------------------------------------------------
# Manifest building
# ---------------------------------------------------------------------------

def _load_ignore_list() -> set[str]:
    """Load titles from .seed-ignore (one per line, # comments allowed)."""
    if not SEED_IGNORE.exists():
        return set()
    ignores = set()
    for line in SEED_IGNORE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            ignores.add(line.lower())
    return ignores


def _extract_search_query(description: str) -> str:
    """Turn a YAML description like 'Anne Lamott -- Bird by Bird' into a search query."""
    # Strip leading author with " -- " separator -> use full string as query
    # The full "Author -- Title" format actually works well for search
    return description.strip()


def build_manifest(domain_filter: str | None = None) -> list[dict]:
    """Parse all domain YAMLs and build a list of expected PDFs.

    Returns list of dicts with keys:
        domain, path, description, collection, exists, search_query
    """
    manifest = []
    ignore_list = _load_ignore_list()

    for yaml_path in sorted(DOMAINS_DIR.glob("*.yaml")):
        with open(yaml_path) as f:
            config = yaml.safe_load(f)

        domain_name = config.get("domain", {}).get("name", yaml_path.stem)
        if domain_filter and domain_name != domain_filter:
            continue

        pdfs = config.get("sources", {}).get("pdfs", [])
        for entry in pdfs:
            path = entry.get("path", "")
            description = entry.get("description", "")
            collection = entry.get("collection", "")

            full_path = ROOT / path
            exists = full_path.exists()
            ignored = description.lower() in ignore_list

            manifest.append({
                "domain": domain_name,
                "path": path,
                "full_path": str(full_path),
                "description": description,
                "collection": collection,
                "exists": exists,
                "ignored": ignored,
                "search_query": _extract_search_query(description),
            })

    return manifest


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(manifest: list[dict]):
    """Print a gap analysis table."""
    table = Table(title="REFERENCE Seed Gap Analysis")
    table.add_column("Domain", width=20)
    table.add_column("Description", max_width=55)
    table.add_column("Status", width=12)

    # Domain summary
    domains: dict[str, dict] = {}
    for entry in manifest:
        d = entry["domain"]
        if d not in domains:
            domains[d] = {"total": 0, "exists": 0, "missing": 0, "ignored": 0}
        domains[d]["total"] += 1
        if entry["exists"]:
            domains[d]["exists"] += 1
        elif entry["ignored"]:
            domains[d]["ignored"] += 1
        else:
            domains[d]["missing"] += 1

    for entry in manifest:
        if entry["exists"]:
            status = "[green]OK[/green]"
        elif entry["ignored"]:
            status = "[dim]ignored[/dim]"
        else:
            status = "[red]MISSING[/red]"

        table.add_row(entry["domain"], entry["description"][:55], status)

    console.print(table)
    console.print()

    # Summary
    summary = Table(title="Summary by Domain")
    summary.add_column("Domain", width=20)
    summary.add_column("Total", width=8, justify="right")
    summary.add_column("OK", width=8, justify="right")
    summary.add_column("Missing", width=8, justify="right")
    summary.add_column("Ignored", width=8, justify="right")

    for d, counts in sorted(domains.items()):
        summary.add_row(
            d,
            str(counts["total"]),
            f"[green]{counts['exists']}[/green]",
            f"[red]{counts['missing']}[/red]" if counts["missing"] else "0",
            f"[dim]{counts['ignored']}[/dim]" if counts["ignored"] else "0",
        )

    total_all = sum(c["total"] for c in domains.values())
    total_ok = sum(c["exists"] for c in domains.values())
    total_miss = sum(c["missing"] for c in domains.values())
    total_ign = sum(c["ignored"] for c in domains.values())
    summary.add_row(
        "[bold]TOTAL[/bold]",
        f"[bold]{total_all}[/bold]",
        f"[bold green]{total_ok}[/bold green]",
        f"[bold red]{total_miss}[/bold red]" if total_miss else "[bold]0[/bold]",
        f"[bold dim]{total_ign}[/bold dim]" if total_ign else "[bold]0[/bold]",
    )
    console.print(summary)


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

def seed_manifest(manifest: list[dict], dry_run: bool = False,
                  try_rip: bool = False, skip_annas: bool = False,
                  delay: float = 3.0) -> list[dict]:
    """Attempt to download all missing PDFs in the manifest.

    Returns list of result dicts with keys: domain, description, path, status, detail.
    """
    results = []
    missing = [e for e in manifest if not e["exists"] and not e["ignored"]]

    if not missing:
        console.print("[green]All PDFs already present or ignored. Nothing to do.[/green]")
        return results

    console.print(f"\n[bold]Seeding {len(missing)} missing PDFs...[/bold]\n")

    for i, entry in enumerate(missing, 1):
        console.print(f"\n{'='*60}")
        console.print(
            f"[bold cyan][{i}/{len(missing)}][/bold cyan] "
            f"[bold]{entry['description']}[/bold]"
        )
        console.print(f"  domain: {entry['domain']}  ->  {entry['path']}")

        if dry_run:
            console.print(f"  [yellow]DRY RUN -- would search for: {entry['search_query']}[/yellow]")
            results.append({
                "domain": entry["domain"],
                "description": entry["description"],
                "path": entry["path"],
                "status": "dry_run",
                "detail": f"Would search: {entry['search_query']}",
            })
            continue

        # Ensure target directory exists
        target_path = Path(entry["full_path"])
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Search using the finder cascade
        search_results = find_book(
            entry["search_query"],
            limit=5,
            skip_web=False,
            skip_annas=skip_annas,
        )

        if not search_results:
            console.print(f"  [red]No results found[/red]")
            results.append({
                "domain": entry["domain"],
                "description": entry["description"],
                "path": entry["path"],
                "status": "not_found",
                "detail": "No search results from any source",
            })
            if i < len(missing):
                time.sleep(delay)
            continue

        # Try downloading -- use a temp name first, then rename to target
        target_dir = str(target_path.parent)
        downloaded = try_download(search_results, target_dir, try_rip=try_rip)

        if downloaded:
            # Rename to the expected filename if it doesn't match
            downloaded_path = Path(downloaded)
            if downloaded_path != target_path:
                if target_path.exists():
                    target_path.unlink()
                downloaded_path.rename(target_path)
                console.print(f"  [green]Renamed -> {target_path.name}[/green]")

            size_mb = target_path.stat().st_size / 1024 / 1024
            results.append({
                "domain": entry["domain"],
                "description": entry["description"],
                "path": entry["path"],
                "status": "downloaded",
                "detail": f"{size_mb:.1f} MB",
            })
        else:
            results.append({
                "domain": entry["domain"],
                "description": entry["description"],
                "path": entry["path"],
                "status": "failed",
                "detail": "All download attempts failed",
            })

        # Rate limit between searches
        if i < len(missing):
            console.print(f"  [dim]waiting {delay}s...[/dim]")
            time.sleep(delay)

    return results


# ---------------------------------------------------------------------------
# Seed log
# ---------------------------------------------------------------------------

def write_seed_log(results: list[dict]):
    """Append results to REFERENCE/SEED-LOG.md."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"\n## Seed Run: {now}\n"]

    downloaded = [r for r in results if r["status"] == "downloaded"]
    failed = [r for r in results if r["status"] == "failed"]
    not_found = [r for r in results if r["status"] == "not_found"]
    dry_run = [r for r in results if r["status"] == "dry_run"]

    lines.append(f"| Status | Count |")
    lines.append(f"|--------|-------|")
    if downloaded:
        lines.append(f"| Downloaded | {len(downloaded)} |")
    if failed:
        lines.append(f"| Failed | {len(failed)} |")
    if not_found:
        lines.append(f"| Not found | {len(not_found)} |")
    if dry_run:
        lines.append(f"| Dry run | {len(dry_run)} |")
    lines.append("")

    if downloaded:
        lines.append("### Downloaded")
        for r in downloaded:
            lines.append(f"- **{r['description']}** -> `{r['path']}` ({r['detail']})")
        lines.append("")

    if failed or not_found:
        lines.append("### Failed / Not Found")
        for r in failed + not_found:
            lines.append(f"- **{r['description']}** -- {r['detail']}")
        lines.append("")

    # Append or create
    existing = SEED_LOG.read_text() if SEED_LOG.exists() else "# REFERENCE Seed Log\n"
    SEED_LOG.write_text(existing + "\n".join(lines) + "\n")
    console.print(f"\n[dim]Log written to {SEED_LOG}[/dim]")


# ---------------------------------------------------------------------------
# Print final summary
# ---------------------------------------------------------------------------

def print_results_summary(results: list[dict]):
    """Print a summary table of seed results."""
    if not results:
        return

    table = Table(title="Seed Results")
    table.add_column("Status", width=12)
    table.add_column("Domain", width=20)
    table.add_column("Description", max_width=45)
    table.add_column("Detail", max_width=30)

    for r in results:
        status_map = {
            "downloaded": "[green]OK[/green]",
            "failed": "[red]FAILED[/red]",
            "not_found": "[yellow]NOT FOUND[/yellow]",
            "dry_run": "[cyan]DRY RUN[/cyan]",
        }
        table.add_row(
            status_map.get(r["status"], r["status"]),
            r["domain"],
            r["description"][:45],
            r["detail"][:30],
        )

    console.print(table)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Seed REFERENCE/ with PDFs declared in domain YAML configs")
    parser.add_argument("--domain", "-d", default=None,
                        help="Seed only this domain (e.g. editorial, culture-crit)")
    parser.add_argument("--report", action="store_true",
                        help="Print gap analysis only, don't download")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be downloaded without doing it")
    parser.add_argument("--rip", action="store_true",
                        help="Enable IA page-rip fallback for borrow-only books")
    parser.add_argument("--skip-annas", action="store_true",
                        help="Skip Anna's Archive searches")
    parser.add_argument("--delay", type=float, default=3.0,
                        help="Delay in seconds between searches (default: 3)")

    args = parser.parse_args()

    manifest = build_manifest(domain_filter=args.domain)

    if not manifest:
        console.print("[yellow]No PDF entries found in domain configs.[/yellow]")
        if args.domain:
            console.print(f"[dim]Check that domains/{args.domain}.yaml exists "
                          f"and has sources.pdfs entries.[/dim]")
        return

    print_report(manifest)

    if args.report:
        return

    results = seed_manifest(
        manifest,
        dry_run=args.dry_run,
        try_rip=args.rip,
        skip_annas=args.skip_annas,
        delay=args.delay,
    )

    if results:
        print_results_summary(results)
        if not args.dry_run:
            write_seed_log(results)


if __name__ == "__main__":
    main()
