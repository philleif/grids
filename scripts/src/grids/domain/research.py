"""LLM-driven web research -- expands seed queries, searches, chunks, and indexes results."""

import json
import os
import hashlib
import time

from rich.console import Console

from grids.domain.config import DomainConfig
from grids.knowledge.store import get_client, get_collection, index_chunks
from grids.orchestration.agents import get_llm

console = Console(stderr=True)


def _search_ddg(query: str, max_results: int = 10) -> list[dict]:
    """Search DuckDuckGo and return results with snippets."""
    from duckduckgo_search import DDGS

    results = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                })
    except Exception as e:
        console.print(f"[yellow]DDG search failed for '{query}': {e}[/yellow]")
    return results


def expand_queries(seed_queries: list[str], domain_description: str) -> list[str]:
    """Use the LLM to expand seed queries into more targeted searches."""
    llm = get_llm(temperature=0.3)
    prompt = (
        f"You are a research librarian for the domain: {domain_description}\n\n"
        f"Given these seed search queries:\n"
        + "\n".join(f"- {q}" for q in seed_queries)
        + "\n\nExpand them into 10-15 more specific, targeted search queries that would find "
        "high-quality academic papers, technical references, and expert knowledge in this domain. "
        "Return ONLY a JSON array of query strings, no other text."
    )

    from langchain_core.messages import HumanMessage
    response = llm.invoke([HumanMessage(content=prompt)])
    text = response.content.strip()

    # Parse JSON array from response
    try:
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        queries = json.loads(text)
        if isinstance(queries, list):
            return seed_queries + [q for q in queries if isinstance(q, str)]
    except (json.JSONDecodeError, IndexError):
        pass

    return seed_queries


def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """Simple character-based chunking with overlap."""
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


def _text_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:12]


def run_web_research(
    config: DomainConfig,
    output_dir: str | None = None,
    verbose: bool = True,
) -> int:
    """Run LLM-driven web research for a domain, index results into ChromaDB."""
    web_config = config.sources.web_research
    if not web_config.enabled:
        if verbose:
            console.print("[dim]Web research disabled for this domain.[/dim]")
        return 0

    domain_name = config.domain.name
    collection_name = f"{domain_name}-web"
    output_dir = output_dir or os.path.join("tmp", domain_name, "web-research")
    os.makedirs(output_dir, exist_ok=True)

    # Expand seed queries
    if verbose:
        console.print(f"[cyan]Expanding {len(web_config.seed_queries)} seed queries...[/cyan]")
    all_queries = expand_queries(web_config.seed_queries, config.domain.description)
    if verbose:
        console.print(f"  Expanded to {len(all_queries)} queries")

    # Search
    all_results = []
    seen_urls = set()
    for i, query in enumerate(all_queries):
        if verbose:
            console.print(f"  [{i+1}/{len(all_queries)}] Searching: {query}")
        results = _search_ddg(query, max_results=web_config.max_results_per_query)
        for r in results:
            if r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                r["query"] = query
                all_results.append(r)

    if verbose:
        console.print(f"[green]Found {len(all_results)} unique results[/green]")

    # Chunk results into indexable units
    chunks = []
    for r in all_results:
        text = f"{r['title']}\n\n{r['snippet']}"
        for i, chunk_text in enumerate(_chunk_text(text)):
            chunk_id = f"web-{_text_hash(r['url'])}-{i}"
            chunks.append({
                "id": chunk_id,
                "text": chunk_text,
                "metadata": {
                    "source": "web",
                    "url": r["url"],
                    "title": r["title"],
                    "query": r["query"],
                    "domain": domain_name,
                },
            })

    # Save chunks file
    chunks_path = os.path.join(output_dir, "chunks.jsonl")
    with open(chunks_path, "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk) + "\n")

    if verbose:
        console.print(f"  Wrote {len(chunks)} chunks to {chunks_path}")

    # Index into ChromaDB
    if chunks:
        indexed = index_chunks(chunks_path, collection_name)
        if verbose:
            console.print(f"[green]Indexed {indexed} chunks into '{collection_name}'[/green]")
        return indexed

    return 0
