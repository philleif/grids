"""Recall -- agents query session memory before starting new work.

"What did we already decide about X?" -- searches session memory and
decision history to provide relevant prior context without carrying
the full conversation history.
"""

from grids.knowledge.store import get_client, get_collection, DEFAULT_DB_PATH
from grids.memory.session_memory import MEMORY_COLLECTION


def recall(
    query: str,
    n_results: int = 5,
    session_id: str | None = None,
    item_type: str | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> list[dict]:
    """Search session memory for relevant prior context.

    Optionally filter by session_id or item_type.
    """
    client = get_client(db_path)
    collection = get_collection(client, MEMORY_COLLECTION)

    if collection.count() == 0:
        return []

    where = None
    if session_id and item_type:
        where = {"$and": [{"session_id": session_id}, {"item_type": item_type}]}
    elif session_id:
        where = {"session_id": session_id}
    elif item_type:
        where = {"item_type": item_type}

    results = collection.query(
        query_texts=[query],
        n_results=n_results,
        where=where,
    )

    hits = []
    for i in range(len(results["ids"][0])):
        hits.append({
            "id": results["ids"][0][i],
            "text": results["documents"][0][i],
            "distance": results["distances"][0][i] if results.get("distances") else None,
            "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
        })
    return hits


def recall_for_agent(
    agent_name: str,
    brief: str,
    n_results: int = 3,
    db_path: str = DEFAULT_DB_PATH,
) -> str:
    """Convenience: recall prior context formatted for injection into an agent prompt."""
    hits = recall(f"{agent_name} {brief}", n_results=n_results, db_path=db_path)
    if not hits:
        return ""

    parts = ["Prior session memory (relevant to current task):"]
    for hit in hits:
        meta = hit.get("metadata", {})
        source = meta.get("item_type", "memory")
        parts.append(f"[{source}] {hit['text']}")
    return "\n---\n".join(parts)
