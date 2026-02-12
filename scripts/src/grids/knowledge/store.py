"""ChromaDB knowledge store -- ingest chunks, query, manage collections."""

import argparse
import json
import os
import sys

import chromadb
from rich.console import Console
from rich.table import Table

from grids.knowledge.embeddings import LocalEmbeddingFunction

console = Console(stderr=True)

DEFAULT_DB_PATH = "tmp/chromadb"

COLLECTIONS = {
    "nks": "Wolfram -- A New Kind of Science",
    "devflow": "Reinertsen -- Principles of Product Development Flow",
}


def register_collection(name: str, description: str):
    """Register a new collection (e.g., from a domain config)."""
    COLLECTIONS[name] = description


def list_collections(db_path: str = DEFAULT_DB_PATH) -> list[str]:
    """List all collections that actually exist in ChromaDB."""
    client = get_client(db_path)
    return [c.name for c in client.list_collections()]


def get_client(db_path: str = DEFAULT_DB_PATH) -> chromadb.ClientAPI:
    os.makedirs(db_path, exist_ok=True)
    return chromadb.PersistentClient(path=db_path)


def get_collection(client: chromadb.ClientAPI, name: str) -> chromadb.Collection:
    ef = LocalEmbeddingFunction()
    return client.get_or_create_collection(
        name=name,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )


def index_chunks(
    chunks_path: str,
    collection_name: str,
    db_path: str = DEFAULT_DB_PATH,
    batch_size: int = 64,
) -> int:
    client = get_client(db_path)
    collection = get_collection(client, collection_name)

    with open(chunks_path, "r", encoding="utf-8") as f:
        chunks = [json.loads(line) for line in f if line.strip()]

    total = len(chunks)
    indexed = 0

    for i in range(0, total, batch_size):
        batch = chunks[i : i + batch_size]
        ids = [c["id"] for c in batch]
        documents = [c["text"] for c in batch]
        metadatas = [c.get("metadata", {}) for c in batch]

        # ChromaDB metadata values must be str, int, float, or bool
        clean_metas = []
        for m in metadatas:
            clean = {}
            for k, v in m.items():
                if v is None:
                    continue
                if isinstance(v, (str, int, float, bool)):
                    clean[k] = v
                else:
                    clean[k] = str(v)
            clean_metas.append(clean)

        collection.upsert(ids=ids, documents=documents, metadatas=clean_metas)
        indexed += len(batch)
        console.print(f"  Indexed {indexed}/{total} chunks", end="\r")

    console.print(f"\n[green]Indexed {indexed} chunks into collection '{collection_name}'[/green]")
    return indexed


def query_store(
    question: str,
    collection_name: str,
    db_path: str = DEFAULT_DB_PATH,
    n_results: int = 5,
) -> list[dict]:
    client = get_client(db_path)
    collection = get_collection(client, collection_name)

    results = collection.query(query_texts=[question], n_results=n_results)

    hits = []
    for i in range(len(results["ids"][0])):
        hits.append({
            "id": results["ids"][0][i],
            "distance": results["distances"][0][i] if results.get("distances") else None,
            "text": results["documents"][0][i],
            "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
        })
    return hits


def query_all(
    question: str,
    db_path: str = DEFAULT_DB_PATH,
    n_results: int = 5,
) -> dict[str, list[dict]]:
    """Query all collections and return combined results."""
    client = get_client(db_path)
    all_results = {}
    for name in COLLECTIONS:
        try:
            hits = query_store(question, name, db_path, n_results)
            if hits:
                all_results[name] = hits
        except Exception:
            continue
    return all_results


def main():
    parser = argparse.ArgumentParser(description="Index chunks into ChromaDB")
    parser.add_argument("chunks", help="Path to chunks.jsonl file")
    parser.add_argument("--collection", "-c", required=True, help="Collection name (e.g., nks, devflow)")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="ChromaDB storage path")
    args = parser.parse_args()

    if not os.path.isfile(args.chunks):
        console.print(f"[red]File not found: {args.chunks}[/red]")
        sys.exit(1)

    index_chunks(args.chunks, args.collection, args.db_path)


def query_main():
    parser = argparse.ArgumentParser(description="Query the knowledge store")
    parser.add_argument("question", help="Natural language question")
    parser.add_argument("--collection", "-c", default=None, help="Collection to query (omit for all)")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="ChromaDB storage path")
    parser.add_argument("-n", type=int, default=5, help="Number of results")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    if args.collection:
        hits = query_store(args.question, args.collection, args.db_path, args.n)
        results = {args.collection: hits}
    else:
        results = query_all(args.question, args.db_path, args.n)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for coll_name, hits in results.items():
            desc = COLLECTIONS.get(coll_name, coll_name)
            console.print(f"\n[bold cyan]{desc}[/bold cyan]")
            table = Table(show_lines=True)
            table.add_column("ID", style="dim")
            table.add_column("Score", width=8)
            table.add_column("Section")
            table.add_column("Text", max_width=80)
            for hit in hits:
                score = f"{1 - hit['distance']:.3f}" if hit.get("distance") is not None else "-"
                section = hit.get("metadata", {}).get("section", "-")
                text = hit["text"][:200].replace("\n", " ")
                table.add_row(hit["id"], score, str(section), text)
            console.print(table)


if __name__ == "__main__":
    main()
