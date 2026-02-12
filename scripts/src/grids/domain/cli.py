"""CLI entry points for domain operations."""

import argparse
import json
import os
import sys

from rich.console import Console
from rich.panel import Panel

from grids.domain.config import load_domain
from grids.domain.master import DomainMaster
from grids.domain.research import run_web_research
from grids.domain.work_orders import WorkOrderQueue
from grids.domain.validation import validate_artifact
from grids.knowledge.store import index_chunks

console = Console(stderr=True)


def domain_init():
    """Initialize a domain: ingest PDFs, run web research, build knowledge store."""
    parser = argparse.ArgumentParser(description="Initialize a domain from config")
    parser.add_argument("config", help="Path to domain YAML config file")
    parser.add_argument("--skip-pdf", action="store_true", help="Skip PDF ingestion")
    parser.add_argument("--skip-web", action="store_true", help="Skip web research")
    parser.add_argument("--output", "-o", default=None, help="Output directory")
    args = parser.parse_args()

    config = load_domain(args.config)
    domain_name = config.domain.name
    output_dir = args.output or os.path.join("tmp", domain_name)

    console.print(Panel(
        f"[bold]{config.domain.name}[/bold]\n{config.domain.description}",
        title="Domain Initialization",
        border_style="cyan",
    ))

    # Step 1: Ingest PDFs
    if not args.skip_pdf:
        for pdf_source in config.sources.pdfs:
            pdf_path = pdf_source.path
            if not os.path.isfile(pdf_path):
                console.print(f"[yellow]PDF not found: {pdf_path} -- skipping[/yellow]")
                continue

            console.print(f"\n[cyan]Ingesting: {pdf_source.description}[/cyan]")
            console.print(f"  Path: {pdf_path}")
            console.print(f"  Collection: {pdf_source.collection}")

            # Run the ingest pipeline
            safe_name = pdf_source.collection.replace(" ", "_")
            ingest_dir = os.path.join(output_dir, safe_name)
            os.makedirs(ingest_dir, exist_ok=True)

            # Use the existing ingest pipeline via subprocess
            import subprocess
            scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            )))
            ingest_bin = os.path.join(scripts_dir, "bin", "grids-ingest")

            if os.path.isfile(ingest_bin):
                result = subprocess.run(
                    [ingest_bin, pdf_path, ingest_dir],
                    capture_output=True, text=True,
                )
                if result.returncode != 0:
                    console.print(f"[red]Ingest failed: {result.stderr[:200]}[/red]")
                    continue
                console.print(result.stdout[-200:] if result.stdout else "  Done.")
            else:
                console.print(f"[yellow]grids-ingest not found at {ingest_bin}[/yellow]")

            # Index chunks
            chunks_path = os.path.join(ingest_dir, "chunks.jsonl")
            if os.path.isfile(chunks_path):
                # Tag concepts with LLM before indexing
                from grids.knowledge.concepts import llm_tag_chunks_file
                console.print(f"  Tagging concepts...")
                llm_tag_chunks_file(chunks_path, config.domain.name, config.domain.description)

                console.print(f"  Indexing into '{pdf_source.collection}'...")
                index_chunks(chunks_path, pdf_source.collection)

    # Step 2: Web research
    if not args.skip_web and config.sources.web_research.enabled:
        console.print(f"\n[cyan]Running web research...[/cyan]")
        indexed = run_web_research(config, output_dir=os.path.join(output_dir, "web-research"))
        console.print(f"[green]Web research complete: {indexed} chunks indexed[/green]")

    # Summary
    console.print(f"\n[bold green]Domain '{domain_name}' initialized.[/bold green]")
    console.print(f"  Collections: {', '.join(config.all_collections)}")
    console.print(f"  Sub-agents: {', '.join(sa.name for sa in config.sub_agents)}")
    console.print(f"  Output: {output_dir}/")


def domain_validate():
    """Validate an artifact against a domain's agents."""
    parser = argparse.ArgumentParser(description="Validate work product against domain expertise")
    parser.add_argument("config", help="Path to domain YAML config file")
    parser.add_argument("artifact", help="Path to artifact JSON file")
    parser.add_argument("--brief", "-b", default=None, help="Brief / description of what was requested")
    parser.add_argument("--queue-dir", default="tmp", help="Base directory for work order queues")
    args = parser.parse_args()

    config = load_domain(args.config)
    master = DomainMaster(config)

    # Load artifact
    with open(args.artifact, "r", encoding="utf-8") as f:
        artifact = json.load(f)

    brief = args.brief or artifact.get("description", artifact.get("title", ""))

    result = master.validate(artifact, brief)

    # Print results
    from grids.domain.validation import _print_validation
    _print_validation(result)

    # Output JSON summary
    print(json.dumps(result.to_dict(), indent=2))

    sys.exit(0 if result.approved else 1)


def domain_specify():
    """Generate a work specification from a request."""
    parser = argparse.ArgumentParser(description="Generate work specification from domain expertise")
    parser.add_argument("config", help="Path to domain YAML config file")
    parser.add_argument("request", help="What to build (text or @file)")
    parser.add_argument("--emit", action="store_true", help="Emit as work order to queue")
    parser.add_argument("--queue-dir", default="tmp", help="Base directory for work order queues")
    args = parser.parse_args()

    config = load_domain(args.config)
    master = DomainMaster(config)

    request = args.request
    if request.startswith("@") and os.path.isfile(request[1:]):
        with open(request[1:], "r") as f:
            request = f.read().strip()

    console.print(Panel(request, title="Request", border_style="cyan"))
    console.print("[cyan]Generating work specification...[/cyan]")

    spec = master.specify(request)
    console.print(Panel(json.dumps(spec.to_dict(), indent=2), title="Work Specification"))

    if args.emit:
        queue = WorkOrderQueue(args.queue_dir, config.domain.name)
        from grids.orchestration.flow import Priority
        order = queue.emit_new(
            domain=config.domain.name,
            kind="code",
            spec=spec.to_dict(),
            acceptance_criteria=spec.acceptance_criteria,
            priority=Priority.HIGH,
            cost_of_delay=3.0,
            job_size=spec.estimated_size,
        )
        console.print(f"[green]Emitted work order: {order.id}[/green]")
    else:
        print(json.dumps(spec.to_dict(), indent=2))
