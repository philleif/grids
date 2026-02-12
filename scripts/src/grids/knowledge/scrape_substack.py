#!/usr/bin/env python3
"""Scrape free posts from a Substack archive page and save as markdown corpus files."""

import argparse
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

DEFAULT_COUNT = 10
DEFAULT_OUTPUT = "./REFERENCE/substacks"
REQUEST_DELAY = 1.0
USER_AGENT = "grids-corpus-scraper/1.0"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Scrape free Substack posts into markdown corpus files for domain agent training."
    )
    parser.add_argument("url", help="Substack archive URL (e.g. https://example.substack.com/archive)")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT, help=f"Number of free posts to fetch (default: {DEFAULT_COUNT})")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT, help=f"Output directory (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--collection", help="ChromaDB collection name to index into (e.g. 'dating-advice')")
    parser.add_argument("--dry-run", action="store_true", help="List posts without fetching/saving")
    return parser.parse_args()


def get_session():
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def derive_substack_name(url):
    """Extract a short name from the Substack URL for filenames."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if "substack.com" in host:
        return host.split(".")[0]
    return host.replace("www.", "").split(".")[0]


def derive_base_url(url):
    """Get the base URL (scheme + host) from the archive URL."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.hostname}"


def fetch_archive_posts(session, archive_url, count):
    """Fetch archive page and extract post links, filtering out paywalled posts."""
    print(f"Fetching archive: {archive_url}")
    resp = session.get(archive_url, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    base_url = derive_base_url(archive_url)
    posts = []

    # Substack archive pages use links with /p/ in the path for posts
    seen_urls = set()
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "/p/" not in href:
            continue

        # Build full URL
        if href.startswith("/"):
            full_url = base_url + href
        elif href.startswith("http"):
            full_url = href
        else:
            continue

        # Normalize: strip query params and fragments
        full_url = full_url.split("?")[0].split("#")[0]

        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)

        # Check for paywall indicators near this link
        parent = link.find_parent(class_=re.compile(r"post|pencraft|feed", re.I))
        is_paywalled = False
        if parent:
            parent_text = parent.get_text(strip=True).lower()
            if any(kw in parent_text for kw in ["subscribers only", "paid subscribers", "🔒"]):
                is_paywalled = True
            lock_icon = parent.find("svg", class_=re.compile(r"lock", re.I))
            if lock_icon:
                is_paywalled = True

        if is_paywalled:
            continue

        # Extract title from the link text or nearby heading
        title = ""
        heading = link.find(["h1", "h2", "h3"])
        if heading:
            title = heading.get_text(strip=True)
        elif link.get_text(strip=True) and len(link.get_text(strip=True)) > 5:
            title = link.get_text(strip=True)

        if not title or len(title) < 3:
            continue

        # Skip nav/footer links that happen to contain /p/
        if title.lower() in ("home", "archive", "about", "podcast", "subscribe", "sign in"):
            continue

        slug = full_url.rstrip("/").split("/")[-1]
        posts.append({"url": full_url, "title": title, "slug": slug})

        if len(posts) >= count:
            break

    return posts


def fetch_post_content(session, post_url):
    """Fetch a single post and extract the article body as markdown-ish text."""
    resp = session.get(post_url, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Extract metadata
    meta = {}
    title_tag = soup.find("meta", property="og:title")
    meta["title"] = title_tag["content"] if title_tag else ""

    author_tag = soup.find("meta", attrs={"name": "author"})
    meta["author"] = author_tag["content"] if author_tag else ""

    date_tag = soup.find("meta", property="article:published_time")
    if date_tag and date_tag.get("content"):
        meta["date"] = date_tag["content"][:10]
    else:
        # Try og:article:published_time or other date meta tags
        for prop in ["article:published_time", "og:article:published_time"]:
            dt = soup.find("meta", property=prop)
            if dt and dt.get("content"):
                meta["date"] = dt["content"][:10]
                break
        else:
            # Fall back to time element in the page
            time_tag = soup.find("time", datetime=True)
            if time_tag:
                meta["date"] = time_tag["datetime"][:10]
            else:
                # Try parsing from visible date text near the byline
                meta["date"] = ""

    # Find the article body -- Substack uses a few different container classes
    body = None
    for selector in [
        "div.body.markup",
        "div.available-content",
        "div.post-content",
        "article",
    ]:
        body = soup.select_one(selector)
        if body:
            break

    if not body:
        return meta, ""

    # Remove subscription CTAs, comment sections, share buttons, etc.
    for remove_sel in [
        "div.subscription-widget-wrap",
        "div.subscribe-widget",
        "div.post-footer",
        "div.comments",
        "div.share-dialog",
        "div.captioned-button-wrap",
        "div.pencraft.pc-display-flex",  # Substack footer widgets
    ]:
        for el in body.select(remove_sel):
            el.decompose()

    # Remove ad/sponsor blocks (images with specific sponsor patterns)
    for img in body.find_all("img"):
        src = img.get("src", "")
        # Keep content images, skip tiny tracking pixels
        if "pixel" in src or "beacon" in src:
            img.decompose()

    # Convert to clean text with basic markdown preservation
    lines = []
    for element in body.children:
        text = element_to_markdown(element)
        if text.strip():
            lines.append(text)

    content = "\n\n".join(lines)

    # Clean up excessive whitespace
    content = re.sub(r"\n{3,}", "\n\n", content)
    content = content.strip()

    return meta, content


def element_to_markdown(el):
    """Convert a BeautifulSoup element to simple markdown."""
    if isinstance(el, str):
        return el.strip()

    if el.name is None:
        return el.get_text(strip=True)

    tag = el.name

    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        level = int(tag[1])
        return "#" * level + " " + inline_to_markdown(el)

    if tag == "blockquote":
        text = el.get_text(strip=True)
        return "> " + text.replace("\n", "\n> ")

    if tag == "hr":
        return "---"

    if tag == "ul":
        items = []
        for li in el.find_all("li", recursive=False):
            items.append("- " + li.get_text(strip=True))
        return "\n".join(items)

    if tag == "ol":
        items = []
        for i, li in enumerate(el.find_all("li", recursive=False), 1):
            items.append(f"{i}. " + li.get_text(strip=True))
        return "\n".join(items)

    if tag == "p":
        return inline_to_markdown(el)

    if tag in ("div", "section", "article"):
        parts = []
        for child in el.children:
            text = element_to_markdown(child)
            if text.strip():
                parts.append(text)
        return "\n\n".join(parts)

    # For images, keep as markdown image
    if tag == "img":
        src = el.get("src", "")
        alt = el.get("alt", "")
        if src and "pixel" not in src and "beacon" not in src:
            return f"![{alt}]({src})"
        return ""

    if tag == "figure":
        img = el.find("img")
        caption = el.find("figcaption")
        parts = []
        if img:
            src = img.get("src", "")
            alt = img.get("alt", "")
            if src:
                parts.append(f"![{alt}]({src})")
        if caption:
            parts.append(f"*{caption.get_text(strip=True)}*")
        return "\n".join(parts)

    # Fallback: just get text
    return el.get_text(strip=True)


def inline_to_markdown(el):
    """Convert inline elements (within a <p>) to markdown with links and emphasis."""
    parts = []
    for child in el.children:
        if isinstance(child, str):
            parts.append(child)
        elif child.name == "a":
            href = child.get("href", "")
            text = child.get_text()
            if href and text.strip():
                parts.append(f" [{text.strip()}]({href}) ")
            else:
                parts.append(text)
        elif child.name in ("strong", "b"):
            parts.append(f" **{child.get_text().strip()}** ")
        elif child.name in ("em", "i"):
            parts.append(f" *{child.get_text().strip()}* ")
        elif child.name == "br":
            parts.append("\n")
        else:
            parts.append(child.get_text())
    # Clean up double spaces from the padding
    result = "".join(parts)
    result = re.sub(r"  +", " ", result)
    result = re.sub(r" ([.,;:!?])", r"\1", result)
    return result.strip()


def save_post(output_dir, substack_name, slug, meta, content):
    """Save a post as a markdown file with YAML frontmatter."""
    filename = f"{substack_name}-{slug}.md"
    filepath = Path(output_dir) / filename

    title = meta.get("title", slug).replace('"', '\\"')
    author = meta.get("author", "")
    date = meta.get("date", "")
    source = meta.get("source", "")

    frontmatter = f"""---
title: "{title}"
author: {author}
date: {date}
source: {source}
substack: {substack_name}
---"""

    filepath.write_text(f"{frontmatter}\n\n{content}\n", encoding="utf-8")
    return filepath


def main():
    args = parse_args()
    session = get_session()

    substack_name = derive_substack_name(args.url)
    print(f"Substack: {substack_name}")
    print(f"Fetching up to {args.count} free posts...\n")

    posts = fetch_archive_posts(session, args.url, args.count)

    if not posts:
        print("No free posts found on this archive page.")
        sys.exit(1)

    print(f"Found {len(posts)} free posts:\n")
    for i, post in enumerate(posts, 1):
        print(f"  {i}. {post['title']}")
        print(f"     {post['url']}")

    if args.dry_run:
        print(f"\n(dry run -- no files saved)")
        return

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nFetching and saving to {output_dir}/\n")

    for i, post in enumerate(posts, 1):
        print(f"  [{i}/{len(posts)}] {post['slug']}...", end=" ", flush=True)
        try:
            meta, content = fetch_post_content(session, post["url"])
            meta["source"] = post["url"]
            if not meta.get("title"):
                meta["title"] = post["title"]
            filepath = save_post(output_dir, substack_name, post["slug"], meta, content)
            print(f"saved ({len(content)} chars)")
        except Exception as e:
            print(f"ERROR: {e}")

        if i < len(posts):
            time.sleep(REQUEST_DELAY)

    print(f"\nDone! {len(posts)} posts saved to {output_dir}/")

    if args.collection:
        _index_to_chromadb(output_dir, substack_name, args.collection)


def _index_to_chromadb(output_dir: str, substack_name: str, collection: str):
    """Index scraped markdown files into a ChromaDB collection.
    Chunks each post into ~500-char paragraphs and indexes via the store API."""
    try:
        from grids.knowledge.store import get_client, get_collection
    except ImportError:
        print(f"\n[!] ChromaDB indexing not available (missing grids.knowledge.store).")
        print(f"    Run manually: grids-index --collection {collection} {output_dir}/{substack_name}-*.md")
        return

    print(f"\nIndexing {substack_name} posts into ChromaDB collection '{collection}'...")
    md_files = sorted(Path(output_dir).glob(f"{substack_name}-*.md"))
    if not md_files:
        print("No markdown files found to index.")
        return

    client = get_client()
    coll = get_collection(client, collection)
    total_chunks = 0

    for md_file in md_files:
        text = md_file.read_text(encoding="utf-8")
        # Strip frontmatter
        if text.startswith("---"):
            end = text.find("---", 3)
            if end > 0:
                frontmatter = text[3:end].strip()
                text = text[end + 3:].strip()
            else:
                frontmatter = ""
        else:
            frontmatter = ""

        # Extract metadata from frontmatter
        meta = {}
        for line in frontmatter.split("\n"):
            if ":" in line:
                key, _, val = line.partition(":")
                meta[key.strip()] = val.strip().strip('"')

        # Chunk by paragraphs, merging short ones
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        chunks = []
        current = ""
        for p in paragraphs:
            if len(current) + len(p) < 500:
                current = (current + "\n\n" + p).strip()
            else:
                if current:
                    chunks.append(current)
                current = p
        if current:
            chunks.append(current)

        # Index chunks
        slug = md_file.stem
        ids = [f"{slug}-chunk-{i}" for i in range(len(chunks))]
        metadatas = [
            {"source": meta.get("source", ""), "title": meta.get("title", ""),
             "author": meta.get("author", ""), "substack": substack_name,
             "section": f"chunk-{i}", "slug": slug}
            for i in range(len(chunks))
        ]
        if chunks:
            coll.upsert(ids=ids, documents=chunks, metadatas=metadatas)
            total_chunks += len(chunks)

    print(f"Indexed {total_chunks} chunks from {len(md_files)} posts into '{collection}'")


if __name__ == "__main__":
    main()
