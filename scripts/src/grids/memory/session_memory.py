"""Session memory -- auto-summarizes completed work and stores for retrieval.

Prevents context window overflow by offloading completed work items into
ChromaDB as structured summaries. Agents can recall prior decisions and
outputs without carrying the full history in-context.
"""

import hashlib
import json
import os
import time

from langchain_core.messages import HumanMessage
from rich.console import Console

from grids.knowledge.store import get_client, get_collection, DEFAULT_DB_PATH
from grids.orchestration.agents import get_llm

console = Console(stderr=True)

MEMORY_COLLECTION = "session-memory"

SUMMARIZE_PROMPT = """Summarize this completed work item for future reference.
Include: what was requested, key decisions made, final outcome, any unresolved issues.
Be concise but preserve all actionable details. Output plain text, 3-5 sentences."""


def _memory_id(session_id: str, item_type: str, index: int) -> str:
    raw = f"{session_id}-{item_type}-{index}"
    return f"mem-{hashlib.md5(raw.encode()).hexdigest()[:12]}"


def store_memory(
    session_id: str,
    item_type: str,
    content: str,
    metadata: dict | None = None,
    summarize: bool = True,
    db_path: str = DEFAULT_DB_PATH,
) -> str:
    """Store a memory entry. Optionally summarizes via LLM first."""
    if summarize and len(content) > 500:
        llm = get_llm(temperature=0.1)
        response = llm.invoke([HumanMessage(content=f"{SUMMARIZE_PROMPT}\n\n{content[:3000]}")])
        summary = response.content.strip()
    else:
        summary = content

    client = get_client(db_path)
    collection = get_collection(client, MEMORY_COLLECTION)

    existing = collection.count()
    mem_id = _memory_id(session_id, item_type, existing)

    meta = {
        "session_id": session_id,
        "item_type": item_type,
        "timestamp": time.time(),
        "original_length": len(content),
    }
    if metadata:
        for k, v in metadata.items():
            if isinstance(v, (str, int, float, bool)):
                meta[k] = v

    collection.upsert(ids=[mem_id], documents=[summary], metadatas=[meta])
    return mem_id


def store_session_result(session_result: dict, db_path: str = DEFAULT_DB_PATH) -> list[str]:
    """Store all phases of a session result into memory."""
    session_id = session_result.get("session_id", "unknown")
    ids = []

    for key in ("research", "concepts", "layout", "critique"):
        content = session_result.get(key, "")
        if content:
            mem_id = store_memory(
                session_id=session_id,
                item_type=key,
                content=content,
                metadata={"brief": session_result.get("brief", "")[:200]},
                db_path=db_path,
            )
            ids.append(mem_id)

    for entry in session_result.get("history", []):
        preview = entry.get("output_preview", "")
        if preview:
            store_memory(
                session_id=session_id,
                item_type=f"history-{entry.get('agent', 'unknown')}",
                content=preview,
                metadata={
                    "agent": entry.get("agent", ""),
                    "iteration": entry.get("iteration", 0),
                },
                summarize=False,
                db_path=db_path,
            )

    return ids


def store_work_order_result(
    order_id: str,
    domain: str,
    artifact: dict,
    validation_result: dict | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> str:
    """Store a completed work order + validation result."""
    content_parts = [
        f"Work order: {order_id}",
        f"Domain: {domain}",
        f"Format: {artifact.get('format', 'unknown')}",
    ]

    notes = artifact.get("design_notes", [])
    if notes:
        content_parts.append("Design decisions:")
        for n in notes[:5]:
            content_parts.append(f"  - {n.get('decision', '')}: {n.get('rationale', '')}")

    if validation_result:
        content_parts.append(f"Validation: {'approved' if validation_result.get('approved') else 'iterated'}")
        content_parts.append(f"Score: {validation_result.get('weighted_score', 0):.3f}")
        if validation_result.get("feedback"):
            content_parts.append(f"Feedback: {validation_result['feedback'][:300]}")

    return store_memory(
        session_id=order_id,
        item_type="work-order-result",
        content="\n".join(content_parts),
        metadata={"domain": domain, "order_id": order_id},
        db_path=db_path,
    )
