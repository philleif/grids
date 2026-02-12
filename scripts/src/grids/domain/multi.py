"""Multi-domain validation -- run an artifact through multiple domain masters.

Each domain validates from its own perspective (design checks composition,
editorial checks copy, production-tech checks fabrication readiness, etc.).
Feedback from all domains is merged into a single iteration directive.
"""

import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from grids.domain.config import DomainConfig, load_domain
from grids.domain.master import DomainMaster, ValidationResult

console = Console(stderr=True)

DOMAINS_DIR = Path(__file__).resolve().parents[4] / "domains"

# Which domains are relevant for which kinds of artifact
DOMAIN_RELEVANCE = {
    "svg": ["design", "production-tech"],
    "latex": ["design", "editorial", "production-tech"],
    "html": ["design", "production-tech"],
    "raw": ["design", "editorial"],
}

# Aspects each domain evaluates when reviewing another domain's output.
# Domains not listed here derive their focus from their YAML description.
_CROSS_DOMAIN_FOCUS = {
    "editorial": "copy quality, text hierarchy, readability, tone of voice",
    "production-tech": "fabrication readiness, print specs, CMYK correctness, bleed/trim compliance",
    "agency-mix": "brand coherence, positioning clarity, audience fit",
    "creative-production": "workflow quality, craft standards, professional finish",
    "culture-crit": "cultural authenticity, reference awareness, aesthetic DNA",
    "data-visualization": "data-ink ratio, visual integrity, label clarity",
}


def _cross_domain_focus(name: str, config: DomainConfig | None = None) -> str:
    """Get the cross-domain review focus for a domain.
    Falls back to the domain's YAML description if not explicitly registered."""
    if name in _CROSS_DOMAIN_FOCUS:
        return _CROSS_DOMAIN_FOCUS[name]
    if config and config.domain.description:
        return config.domain.description[:200]
    return "general review"


class MultiDomainResult:
    """Aggregated result from multiple domain validations."""

    def __init__(
        self,
        primary_result: ValidationResult,
        secondary_results: dict[str, ValidationResult],
        merged_feedback: str,
        all_approved: bool,
    ):
        self.primary_result = primary_result
        self.secondary_results = secondary_results
        self.merged_feedback = merged_feedback
        self.all_approved = all_approved

    def to_dict(self) -> dict:
        return {
            "all_approved": self.all_approved,
            "primary": self.primary_result.to_dict(),
            "secondary": {
                name: r.to_dict() for name, r in self.secondary_results.items()
            },
            "merged_feedback": self.merged_feedback,
        }


def load_all_domains(names: list[str] | None = None) -> dict[str, DomainConfig]:
    """Load domain configs by name. If names is None, load all available."""
    configs = {}
    for yaml_path in sorted(DOMAINS_DIR.glob("*.yaml")):
        config = load_domain(yaml_path)
        domain_name = config.domain.name
        if names is None or domain_name in names:
            configs[domain_name] = config
    return configs


def select_secondary_domains(
    primary_domain: str,
    artifact_format: str,
    explicit_domains: list[str] | None = None,
) -> list[str]:
    """Pick which secondary domains should review this artifact."""
    if explicit_domains is not None:
        return [d for d in explicit_domains if d != primary_domain]

    relevant = DOMAIN_RELEVANCE.get(artifact_format, ["design"])
    # Always include the format-relevant ones, plus agency-mix for brand coherence
    candidates = set(relevant) | {"agency-mix", "editorial"}
    candidates.discard(primary_domain)

    # Only include domains that have YAML configs
    available = {p.stem for p in DOMAINS_DIR.glob("*.yaml")}
    # Also check by domain.name inside configs
    available_names = set()
    for yaml_path in DOMAINS_DIR.glob("*.yaml"):
        try:
            cfg = load_domain(yaml_path)
            available_names.add(cfg.domain.name)
        except Exception:
            available_names.add(yaml_path.stem)

    return sorted(candidates & available_names)


def multi_domain_validate(
    artifact: dict,
    brief: str,
    primary_config: DomainConfig,
    secondary_domains: list[str] | None = None,
    iteration: int = 0,
    verbose: bool = True,
) -> MultiDomainResult:
    """Run artifact through primary domain + secondary domain validators.

    The primary domain runs full validation (all sub-agents + master veto).
    Secondary domains run focused validation on their cross-domain aspect only.
    """
    artifact_format = artifact.get("format", "raw")
    primary_name = primary_config.domain.name

    # Primary domain: full validation
    if verbose:
        console.print(f"\n[bold cyan]Primary domain: {primary_name}[/bold cyan]")

    primary_master = DomainMaster(primary_config)
    primary_result = primary_master.validate(artifact, brief, iteration=iteration)

    if verbose:
        _print_domain_result(primary_name, primary_result, is_primary=True)

    # Secondary domains: focused validation
    domain_names = select_secondary_domains(
        primary_name, artifact_format, secondary_domains
    )

    if verbose and domain_names:
        console.print(f"\n[cyan]Secondary domains: {', '.join(domain_names)}[/cyan]")

    secondary_results: dict[str, ValidationResult] = {}
    configs = load_all_domains(domain_names)

    for name in domain_names:
        config = configs.get(name)
        if config is None:
            continue

        if verbose:
            console.print(f"\n  [dim]{name}[/dim] -- {_cross_domain_focus(name, config)}")

        try:
            master = DomainMaster(config)
            # Augment brief with cross-domain focus
            focus = _cross_domain_focus(name, config)
            focused_brief = f"{brief}\n\n[Cross-domain review focus: {focus}]"
            result = master.validate(artifact, focused_brief, iteration=iteration)
            secondary_results[name] = result

            if verbose:
                _print_domain_result(name, result, is_primary=False)
        except Exception as e:
            if verbose:
                console.print(f"  [yellow]{name} validation skipped: {e}[/yellow]")

    # Merge feedback
    all_feedback_parts = []
    if not primary_result.approved and primary_result.feedback:
        all_feedback_parts.append(f"[{primary_name} (primary)]\n{primary_result.feedback}")

    for name, result in secondary_results.items():
        if not result.approved and result.feedback:
            all_feedback_parts.append(f"[{name}]\n{result.feedback}")

    merged_feedback = "\n\n---\n\n".join(all_feedback_parts)

    # All approved only if primary + all secondaries approve
    all_approved = primary_result.approved and all(
        r.approved for r in secondary_results.values()
    )

    if verbose:
        _print_summary(primary_name, primary_result, secondary_results, all_approved)

    return MultiDomainResult(
        primary_result=primary_result,
        secondary_results=secondary_results,
        merged_feedback=merged_feedback,
        all_approved=all_approved,
    )


def _print_domain_result(name: str, result: ValidationResult, is_primary: bool):
    """Print a compact domain validation result."""
    prefix = "[bold]" if is_primary else "[dim]"
    suffix = "[/bold]" if is_primary else "[/dim]"
    status = "[green]APPROVED[/green]" if result.approved else "[red]ITERATE[/red]"
    console.print(
        f"  {prefix}{name}{suffix}: "
        f"weighted={result.weighted_score:.2f} master={result.master_score:.2f} {status}"
    )
    for s in result.sub_scores:
        if s.verdict != "pass":
            console.print(f"    [yellow]{s.agent_name}: {s.score:.2f} -- {s.feedback[:80]}[/yellow]")


def _print_summary(
    primary_name: str,
    primary_result: ValidationResult,
    secondary_results: dict[str, ValidationResult],
    all_approved: bool,
):
    """Print final multi-domain summary."""
    table = Table(title="Multi-Domain Validation Summary")
    table.add_column("Domain", width=22)
    table.add_column("Role", width=10)
    table.add_column("Score", width=8)
    table.add_column("Verdict", width=10)

    color = "green" if primary_result.approved else "red"
    table.add_row(
        primary_name,
        "primary",
        f"{primary_result.weighted_score:.2f}",
        f"[{color}]{'PASS' if primary_result.approved else 'FAIL'}[/{color}]",
    )

    for name, result in secondary_results.items():
        color = "green" if result.approved else "red"
        table.add_row(
            name,
            "secondary",
            f"{result.weighted_score:.2f}",
            f"[{color}]{'PASS' if result.approved else 'FAIL'}[/{color}]",
        )

    console.print(table)

    overall = "[bold green]ALL APPROVED[/bold green]" if all_approved else "[bold red]NEEDS ITERATION[/bold red]"
    console.print(f"\n  Overall: {overall}")
