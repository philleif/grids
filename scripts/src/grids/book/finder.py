"""Book finder -- multi-strategy search and download of reference book PDFs.

Cascading search strategy:
  1. Internet Archive (public/free PDFs via advanced search + metadata API)
  2. Open Library (for IA identifiers of borrowable books)
  3. DuckDuckGo filetype:pdf web search (finds PDFs on university servers, mirrors, etc.)
  4. Anna's Archive (shadow library aggregator, optional)
  5. IA borrow + page-image rip fallback (requires archive.org account)

Downloads to REFERENCE/<domain>/ and optionally updates the domain YAML config.
"""

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

from rich.console import Console
from rich.table import Table

console = Console(stderr=True)

UA = "GRIDS/0.2 (book finder; +https://github.com/grids)"


# ---------------------------------------------------------------------------
# 1. Internet Archive
# ---------------------------------------------------------------------------

def search_internet_archive(query: str, limit: int = 5) -> list[dict]:
    """Search Internet Archive for downloadable PDFs."""
    params = urllib.parse.urlencode({
        "q": f"{query} mediatype:texts format:pdf",
        "output": "json",
        "rows": limit,
        "fl[]": "identifier,title,creator,date,format,downloads",
    })
    url = f"https://archive.org/advancedsearch.php?{params}"
    results = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        for doc in data.get("response", {}).get("docs", []):
            identifier = doc.get("identifier", "")
            results.append({
                "source": "internet_archive",
                "title": doc.get("title", ""),
                "author": doc.get("creator", ""),
                "year": doc.get("date", ""),
                "identifier": identifier,
                "downloads": doc.get("downloads", 0),
                "pdf_url": f"https://archive.org/download/{identifier}/{identifier}.pdf",
                "page_url": f"https://archive.org/details/{identifier}",
            })
    except Exception as e:
        console.print(f"[yellow]Internet Archive search failed: {e}[/yellow]")
    return results


def get_ia_file_list(identifier: str) -> list[dict]:
    """Fetch the full file list for an IA item."""
    url = f"https://archive.org/metadata/{identifier}/files"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return data.get("result", [])
    except Exception:
        return []


def get_ia_pdf_url(identifier: str) -> str | None:
    """Get the actual PDF download URL for an IA item, trying multiple patterns."""
    files = get_ia_file_list(identifier)
    pdfs = [f for f in files if f.get("name", "").lower().endswith(".pdf")]
    if not pdfs:
        return None
    # Prefer: exact match, then largest PDF, then first
    exact = [f for f in pdfs if f["name"] == f"{identifier}.pdf"]
    if exact:
        chosen = exact[0]
    else:
        pdfs.sort(key=lambda f: int(f.get("size", 0)), reverse=True)
        chosen = pdfs[0]
    return f"https://archive.org/download/{identifier}/{urllib.parse.quote(chosen['name'])}"


def get_ia_item_access(identifier: str) -> str:
    """Check whether an IA item is freely downloadable or borrow-only."""
    url = f"https://archive.org/metadata/{identifier}/metadata"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        meta = data.get("result", {})
        access = meta.get("access-restricted-item", "false")
        collection = meta.get("collection", "")
        if isinstance(collection, list):
            collection = " ".join(collection)
        if access == "true" or "inlibrary" in collection:
            return "borrow_only"
        return "public"
    except Exception:
        return "unknown"


def get_ia_page_count(identifier: str) -> int | None:
    """Get page count from IA metadata for page-ripping."""
    files = get_ia_file_list(identifier)
    for f in files:
        name = f.get("name", "")
        if name.endswith("_jp2.zip"):
            # Count from scandata if available
            pass
    # Fallback: check scandata.xml
    url = f"https://archive.org/download/{identifier}/{identifier}_scandata.xml"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read().decode("utf-8", errors="replace")
        pages = content.count("<page ")
        return pages if pages > 0 else None
    except Exception:
        return None


def rip_ia_pages_to_pdf(identifier: str, output_path: str, scale: int = 4) -> bool:
    """Download individual page images from IA BookReader and stitch into a PDF.

    This works for borrow-only items when the BookReader is accessible.
    Uses the IIIF-like image endpoint. Requires img2pdf.
    """
    try:
        import img2pdf
        from PIL import Image
        from io import BytesIO
    except ImportError:
        console.print("[red]img2pdf + Pillow required for page ripping. "
                       "pip install img2pdf Pillow[/red]")
        return False

    page_count = get_ia_page_count(identifier)
    if not page_count:
        console.print(f"  [yellow]Cannot determine page count for {identifier}[/yellow]")
        return False

    console.print(f"  [cyan]Ripping {page_count} pages from BookReader...[/cyan]")
    images = []
    for i in range(page_count):
        img_url = (
            f"https://archive.org/BookReader/BookReaderImages.php"
            f"?zip={identifier}_jp2.zip"
            f"&file={identifier}_jp2/{identifier}_{i:04d}.jp2"
            f"&id={identifier}&scale={scale}&rotate=0"
        )
        try:
            req = urllib.request.Request(img_url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                img_data = resp.read()
            if len(img_data) < 500:
                continue
            images.append(img_data)
            if (i + 1) % 25 == 0:
                console.print(f"    page {i + 1}/{page_count}")
            time.sleep(0.3)
        except Exception:
            continue

    if not images:
        return False

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    try:
        with open(output_path, "wb") as f:
            f.write(img2pdf.convert(images))
        return True
    except Exception as e:
        console.print(f"  [red]PDF assembly failed: {e}[/red]")
        return False


# ---------------------------------------------------------------------------
# 2. Open Library
# ---------------------------------------------------------------------------

def search_open_library(title: str, limit: int = 5) -> list[dict]:
    """Search Open Library for books matching the title."""
    params = urllib.parse.urlencode({"title": title, "limit": limit})
    url = f"https://openlibrary.org/search.json?{params}"
    results = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        for doc in data.get("docs", []):
            entry = {
                "source": "openlibrary",
                "title": doc.get("title", ""),
                "author": ", ".join(doc.get("author_name", [])),
                "year": doc.get("first_publish_year", ""),
                "key": doc.get("key", ""),
                "isbn": (doc.get("isbn", []) or [""])[0],
                "has_fulltext": doc.get("has_fulltext", False),
                "ia_ids": doc.get("ia", []),
                "ebook_access": doc.get("ebook_access", "no_ebook"),
            }
            results.append(entry)
    except Exception as e:
        console.print(f"[yellow]Open Library search failed: {e}[/yellow]")
    return results


# ---------------------------------------------------------------------------
# 3. DuckDuckGo filetype:pdf search
# ---------------------------------------------------------------------------

def _get_ddgs_class():
    """Import DDGS from whichever package is available (ddgs or duckduckgo_search)."""
    try:
        from ddgs import DDGS
        return DDGS
    except ImportError:
        pass
    try:
        from duckduckgo_search import DDGS
        return DDGS
    except ImportError:
        return None


def search_web_pdf(title: str, limit: int = 8) -> list[dict]:
    """Search the web for direct PDF links using DuckDuckGo.

    Tries multiple query variations:
      - "<title>" filetype:pdf
      - "<title>" pdf download
    """
    results = []
    DDGS = _get_ddgs_class()
    if DDGS is None:
        console.print("[yellow]ddgs / duckduckgo-search not installed, "
                       "skipping web PDF search[/yellow]")
        return results

    queries = [
        f'"{title}" filetype:pdf',
        f'"{title}" pdf download',
    ]
    seen_urls = set()
    for q in queries:
        try:
            with DDGS() as ddgs:
                for r in ddgs.text(q, max_results=limit):
                    href = r.get("href", "") or r.get("link", "")
                    if not href or href in seen_urls:
                        continue
                    seen_urls.add(href)
                    is_direct_pdf = href.lower().endswith(".pdf")
                    results.append({
                        "source": "web_search",
                        "title": _strip_html(r.get("title", "")),
                        "author": "",
                        "year": "",
                        "pdf_url": href if is_direct_pdf else None,
                        "page_url": href,
                        "is_direct_pdf": is_direct_pdf,
                        "snippet": _strip_html(r.get("body", "") or r.get("snippet", "")),
                    })
            time.sleep(1)
        except Exception as e:
            console.print(f"[yellow]Web search failed for '{q}': {e}[/yellow]")
    return results


def _strip_html(s: str) -> str:
    """Remove HTML tags and decode entities."""
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).strip()


# ---------------------------------------------------------------------------
# 4. Anna's Archive (optional)
# ---------------------------------------------------------------------------

ANNAS_MIRRORS = [
    "https://annas-archive.org",
    "https://annas-archive.se",
    "https://annas-archive.li",
]


def search_annas_archive(title: str, limit: int = 5) -> list[dict]:
    """Search Anna's Archive for books. Tries multiple mirror domains."""
    results = []
    encoded = urllib.parse.quote(title)
    browser_ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    for base in ANNAS_MIRRORS:
        url = f"{base}/search?q={encoded}&ext=pdf&sort=&lang=en"
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": browser_ua,
                "Accept": "text/html,application/xhtml+xml",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8", errors="replace")

            # Parse result entries with /md5/ links
            pattern = (r'href="(/md5/[a-f0-9]+)"[^>]*>.*?<h3[^>]*>(.*?)</h3>'
                       r'.*?<div[^>]*class="[^"]*text-sm[^"]*"[^>]*>(.*?)</div>')
            matches = re.findall(pattern, body, re.DOTALL)[:limit]
            for path, raw_title, raw_meta in matches:
                clean_title = _strip_html(raw_title)
                clean_meta = _strip_html(raw_meta)
                author = ""
                year = ""
                year_match = re.search(r"\b(1[89]\d{2}|20[0-2]\d)\b", clean_meta)
                if year_match:
                    year = year_match.group(1)
                parts = clean_meta.split(",")
                if parts:
                    author = parts[0].strip()

                results.append({
                    "source": "annas_archive",
                    "title": clean_title,
                    "author": author,
                    "year": year,
                    "page_url": f"{base}{path}",
                    "md5": path.split("/")[-1],
                })
            if results:
                break
        except Exception:
            continue

    if not results:
        console.print(f"[yellow]Anna's Archive search failed (all mirrors)[/yellow]")
    return results


def get_annas_download_url(md5: str) -> str | None:
    """Try to extract a direct download URL from an Anna's Archive detail page."""
    browser_ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    for base in ANNAS_MIRRORS:
        url = f"{base}/md5/{md5}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": browser_ua})
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            for pat in [
                r'href="(https?://libgen\.[^"]+)"',
                r'href="(https?://download\.[^"]+)"',
                r'href="(/slow_download/[^"]+)"',
            ]:
                m = re.search(pat, body)
                if m:
                    link = m.group(1)
                    if link.startswith("/"):
                        link = f"{base}{link}"
                    return link
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def download_pdf(url: str, output_path: str, timeout: int = 120) -> bool:
    """Download a PDF from a URL with progress feedback."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/pdf,*/*",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            # Reject HTML error pages masquerading as PDFs
            if "text/html" in content_type:
                return False
            total = 0
            with open(output_path, "wb") as f:
                while True:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
                    total += len(chunk)
        if total < 1000:
            os.unlink(output_path)
            return False
        # Verify it's actually a PDF
        with open(output_path, "rb") as f:
            magic = f.read(5)
        if magic != b"%PDF-":
            os.unlink(output_path)
            return False
        return True
    except Exception as e:
        console.print(f"[red]Download failed: {e}[/red]")
        if os.path.exists(output_path):
            os.unlink(output_path)
        return False


def _safe_filename(title: str, max_len: int = 80) -> str:
    """Create a filesystem-safe filename from a title."""
    safe = "".join(c if c.isalnum() or c in " -_" else "" for c in title)
    return safe.strip().replace(" ", "-")[:max_len] or "book"


# ---------------------------------------------------------------------------
# Scoring & orchestration
# ---------------------------------------------------------------------------

def score_result(r: dict) -> float:
    """Score a result by likelihood of yielding a downloadable PDF."""
    s = 0.0
    if r.get("is_direct_pdf"):
        s += 20
    if r.get("pdf_url"):
        s += 10
    if r.get("has_fulltext"):
        s += 10
    if r.get("ebook_access") == "public":
        s += 15
    if r.get("ia_ids"):
        s += 5
    if r.get("md5"):
        s += 8
    s += min(r.get("downloads", 0) / 100, 5)
    # Penalise borrow-only
    if r.get("access") == "borrow_only":
        s -= 5
    return s


def find_book(title: str, limit: int = 5, skip_web: bool = False,
              skip_annas: bool = False) -> list[dict]:
    """Search all sources for a book and return combined, scored results."""
    console.print(f"\n[bold cyan]Searching for:[/bold cyan] {title}\n")

    all_results = []

    # 1. Internet Archive
    console.print("  [dim]1/4[/dim] Internet Archive...")
    ia_results = search_internet_archive(title, limit)
    all_results.extend(ia_results)

    # 2. Open Library
    console.print("  [dim]2/4[/dim] Open Library...")
    ol_results = search_open_library(title, limit)
    all_results.extend(ol_results)

    # 3. Web search (filetype:pdf)
    if not skip_web:
        console.print("  [dim]3/4[/dim] Web PDF search...")
        web_results = search_web_pdf(title, limit)
        all_results.extend(web_results)
    else:
        console.print("  [dim]3/4[/dim] Web PDF search... [dim]skipped[/dim]")

    # 4. Anna's Archive
    if not skip_annas:
        console.print("  [dim]4/4[/dim] Anna's Archive...")
        aa_results = search_annas_archive(title, limit)
        all_results.extend(aa_results)
    else:
        console.print("  [dim]4/4[/dim] Anna's Archive... [dim]skipped[/dim]")

    all_results.sort(key=score_result, reverse=True)
    console.print(f"\n[green]Found {len(all_results)} results across all sources[/green]")
    return all_results


def try_download(results: list[dict], download_dir: str,
                 try_rip: bool = False) -> str | None:
    """Walk results and attempt download. Returns path on success, None on failure.

    Strategy order per result:
      1. Direct pdf_url
      2. IA metadata PDF lookup (tries all PDF filenames)
      3. Anna's Archive download link extraction
      4. IA page-rip fallback (if --rip flag set)
    """
    for r in results:
        safe = _safe_filename(r.get("title", "book"))
        output_path = os.path.join(download_dir, f"{safe}.pdf")

        # --- Direct PDF URL ---
        pdf_url = r.get("pdf_url")
        if pdf_url:
            console.print(f"\n[cyan]Trying:[/cyan] {r.get('title', '')}")
            console.print(f"  [dim]source: {r.get('source')} | url: {pdf_url}[/dim]")
            if download_pdf(pdf_url, output_path):
                _report_success(output_path)
                return output_path

        # --- IA metadata lookup ---
        for ia_id in (r.get("ia_ids") or []) + ([r["identifier"]] if r.get("identifier") else []):
            resolved = get_ia_pdf_url(ia_id)
            if resolved:
                console.print(f"\n[cyan]Trying IA file:[/cyan] {ia_id}")
                console.print(f"  [dim]{resolved}[/dim]")
                if download_pdf(resolved, output_path):
                    _report_success(output_path)
                    return output_path
                # Check if borrow-only
                access = get_ia_item_access(ia_id)
                if access == "borrow_only" and try_rip:
                    console.print(f"  [yellow]Borrow-only -- attempting page rip...[/yellow]")
                    if rip_ia_pages_to_pdf(ia_id, output_path):
                        _report_success(output_path)
                        return output_path

        # --- Anna's Archive download ---
        if r.get("md5"):
            console.print(f"\n[cyan]Trying Anna's Archive:[/cyan] {r.get('title', '')}")
            dl_url = get_annas_download_url(r["md5"])
            if dl_url:
                console.print(f"  [dim]{dl_url}[/dim]")
                if download_pdf(dl_url, output_path):
                    _report_success(output_path)
                    return output_path

    console.print("\n[yellow]No downloadable PDF found from any source.[/yellow]")
    return None


def _report_success(path: str):
    size_mb = os.path.getsize(path) / 1024 / 1024
    console.print(f"  [green]Downloaded: {path} ({size_mb:.1f} MB)[/green]")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Find and download reference book PDFs (multi-source)")
    parser.add_argument("title", help="Book title to search for")
    parser.add_argument("--download", "-d", default=None,
                        help="Download to this directory")
    parser.add_argument("--domain", default=None,
                        help="Domain name (saves to REFERENCE/<domain>/)")
    parser.add_argument("--limit", "-n", type=int, default=5,
                        help="Max results per source")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--skip-web", action="store_true",
                        help="Skip DuckDuckGo web search")
    parser.add_argument("--skip-annas", action="store_true",
                        help="Skip Anna's Archive search")
    parser.add_argument("--rip", action="store_true",
                        help="Enable IA page-rip fallback for borrow-only books")
    args = parser.parse_args()

    results = find_book(
        args.title,
        limit=args.limit,
        skip_web=args.skip_web,
        skip_annas=args.skip_annas,
    )

    if args.json:
        print(json.dumps(results, indent=2))
        return

    # Display results table
    table = Table(title=f"Results for: {args.title}")
    table.add_column("#", width=3)
    table.add_column("Source", width=16)
    table.add_column("Title", max_width=50)
    table.add_column("Author", max_width=25)
    table.add_column("Year", width=6)
    table.add_column("PDF?", width=6)

    for i, r in enumerate(results, 1):
        has_pdf = "direct" if r.get("is_direct_pdf") else (
            "yes" if r.get("pdf_url") or r.get("has_fulltext") or r.get("md5") else "no"
        )
        style = "green" if has_pdf in ("direct", "yes") else "dim"
        table.add_row(
            str(i),
            r.get("source", ""),
            r.get("title", "")[:50],
            str(r.get("author", ""))[:25],
            str(r.get("year", "")),
            f"[{style}]{has_pdf}[/{style}]",
        )
    console.print(table)

    # Download
    if args.download or args.domain:
        download_dir = args.download or os.path.join(
            "REFERENCE", args.domain or "general")
        try_download(results, download_dir, try_rip=args.rip)


if __name__ == "__main__":
    main()
