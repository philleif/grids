"""Deep research tool -- finds quality visual references for a given creative brief."""

import argparse
import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timezone

from rich.console import Console

console = Console(stderr=True)

SEARCH_SOURCES = [
    {
        "id": "are-na",
        "name": "Are.na",
        "base_url": "https://api.are.na/v2/search",
        "type": "api",
    },
    {
        "id": "loc",
        "name": "Library of Congress",
        "base_url": "https://www.loc.gov/search/",
        "type": "api",
    },
    {
        "id": "met",
        "name": "The Metropolitan Museum (Open Access)",
        "base_url": "https://collectionapi.metmuseum.org/public/collection/v1/search",
        "type": "api",
    },
    {
        "id": "smithsonian",
        "name": "Smithsonian Open Access",
        "base_url": "https://api.si.edu/openaccess/api/v1.0/search",
        "type": "api",
    },
    {
        "id": "europeana",
        "name": "Europeana",
        "base_url": "https://api.europeana.eu/record/v2/search.json",
        "type": "api",
    },
]


def search_met(query: str, limit: int = 20) -> list[dict]:
    """Search the Met's open access API."""
    params = urllib.parse.urlencode({"q": query, "isHighlight": "true", "hasImages": "true"})
    url = f"https://collectionapi.metmuseum.org/public/collection/v1/search?{params}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        if not data.get("objectIDs"):
            return []
        results = []
        for oid in data["objectIDs"][:limit]:
            obj_url = f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{oid}"
            try:
                with urllib.request.urlopen(obj_url, timeout=10) as resp2:
                    obj = json.loads(resp2.read())
                if obj.get("primaryImageSmall"):
                    results.append({
                        "source": "met",
                        "id": str(oid),
                        "title": obj.get("title", ""),
                        "artist": obj.get("artistDisplayName", ""),
                        "date": obj.get("objectDate", ""),
                        "medium": obj.get("medium", ""),
                        "image_url": obj.get("primaryImageSmall", ""),
                        "image_url_full": obj.get("primaryImage", ""),
                        "page_url": obj.get("objectURL", ""),
                        "tags": [t["term"] for t in obj.get("tags", []) or [] if "term" in t],
                    })
            except Exception:
                continue
        return results
    except Exception as e:
        console.print(f"[yellow]Met search failed: {e}[/yellow]")
        return []


def search_loc(query: str, limit: int = 20) -> list[dict]:
    """Search Library of Congress digital collections."""
    params = urllib.parse.urlencode({"q": query, "fo": "json", "c": limit, "fa": "online-format:image"})
    url = f"https://www.loc.gov/search/?{params}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        results = []
        for item in data.get("results", []):
            image_url = ""
            if item.get("image_url"):
                urls = item["image_url"]
                image_url = urls[0] if isinstance(urls, list) else urls
            if image_url:
                results.append({
                    "source": "loc",
                    "id": item.get("id", ""),
                    "title": item.get("title", ""),
                    "artist": item.get("contributor", [""])[0] if item.get("contributor") else "",
                    "date": item.get("date", ""),
                    "medium": "",
                    "image_url": image_url,
                    "image_url_full": image_url,
                    "page_url": item.get("url", ""),
                    "tags": item.get("subject", []) or [],
                })
        return results
    except Exception as e:
        console.print(f"[yellow]LoC search failed: {e}[/yellow]")
        return []


def deep_research(query: str, limit_per_source: int = 10) -> list[dict]:
    """Search across all sources and return combined results."""
    all_results = []
    console.print(f"[cyan]Researching:[/cyan] {query}")

    console.print("  Searching Metropolitan Museum...")
    all_results.extend(search_met(query, limit_per_source))

    console.print("  Searching Library of Congress...")
    all_results.extend(search_loc(query, limit_per_source))

    console.print(f"[green]Found {len(all_results)} references[/green]")
    return all_results


def download_references(refs: list[dict], output_dir: str) -> list[dict]:
    """Download reference images to local dir."""
    os.makedirs(output_dir, exist_ok=True)
    downloaded = []
    for ref in refs:
        url = ref.get("image_url")
        if not url:
            continue
        ext = ".jpg"
        if ".png" in url.lower():
            ext = ".png"
        filename = f"{ref['source']}_{ref['id']}{ext}".replace("/", "_")
        filepath = os.path.join(output_dir, filename)
        if os.path.exists(filepath):
            ref["local_path"] = filepath
            downloaded.append(ref)
            continue
        try:
            urllib.request.urlretrieve(url, filepath)
            ref["local_path"] = filepath
            downloaded.append(ref)
        except Exception as e:
            console.print(f"[yellow]Failed to download {ref['title']}: {e}[/yellow]")
    return downloaded


def main():
    parser = argparse.ArgumentParser(description="Deep research -- find quality visual references")
    parser.add_argument("query", help="Search query (e.g. 'vintage calling cards letterpress')")
    parser.add_argument("--output", "-o", default=None, help="Output directory for images")
    parser.add_argument("--limit", "-n", type=int, default=10, help="Max results per source")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of summary")
    parser.add_argument("--download", "-d", action="store_true", help="Download reference images")
    args = parser.parse_args()

    results = deep_research(args.query, limit_per_source=args.limit)

    output_dir = args.output or os.path.join("tmp", "moodboard", args.query.replace(" ", "-")[:40])

    if args.download and results:
        results = download_references(results, os.path.join(output_dir, "refs"))

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            console.print(f"  [{r['source']}] [bold]{r['title']}[/bold]")
            if r.get("artist"):
                console.print(f"    by {r['artist']}, {r.get('date', '')}")
            if r.get("page_url"):
                console.print(f"    {r['page_url']}")

    # Save manifest
    os.makedirs(output_dir, exist_ok=True)
    manifest_path = os.path.join(output_dir, "research.json")
    manifest = {
        "query": args.query,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sources_searched": ["met", "loc"],
        "total_results": len(results),
        "results": results,
    }
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    console.print(f"\n[green]Saved to {manifest_path}[/green]")


if __name__ == "__main__":
    main()
