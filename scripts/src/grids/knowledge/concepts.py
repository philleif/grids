"""Concept extraction -- tags chunks with key concepts for better retrieval."""

import json
import re

# Core concepts from each book, used for heuristic tagging.
# These get enriched by LLM extraction later.

NKS_CONCEPTS = {
    "cellular automata": ["cellular automaton", "cellular automata", "ca rule", "rule \\d+"],
    "simple programs": ["simple program", "simple rule", "enumeration of"],
    "computational irreducibility": ["computational irreducibility", "irreducible", "irreducibility"],
    "computational equivalence": ["computational equivalence", "principle of computational equivalence"],
    "universality": ["universal", "universality", "turing complete", "rule 110"],
    "complexity from simplicity": ["complexity", "from simple", "emergence", "emergent"],
    "randomness": ["randomness", "random", "pseudorandom", "intrinsic randomness"],
    "nesting": ["nesting", "nested", "self-similar", "fractal"],
    "substitution systems": ["substitution system", "string rewriting"],
    "turing machines": ["turing machine", "head.*tape"],
    "register machines": ["register machine"],
    "tag systems": ["tag system"],
    "network systems": ["network system", "network evolution"],
    "multiway systems": ["multiway system", "multiway"],
    "perception and analysis": ["perception", "analysis", "human perception"],
    "fundamental physics": ["fundamental physics", "space.*time", "causal network"],
    "biological forms": ["biological", "growth", "morphogenesis", "pigmentation"],
    "fluid dynamics": ["fluid", "turbulence", "flow pattern"],
    "mathematical structures": ["mathematical", "number theory", "prime"],
}

DEVFLOW_CONCEPTS = {
    "cost of delay": ["cost of delay", "cod", "delay cost", "economic impact of delay"],
    "wip constraints": ["wip", "work.in.process", "work.in.progress", "wip limit", "wip constraint"],
    "batch size": ["batch size", "small batch", "large batch", "batch reduction"],
    "queue management": ["queue", "queuing", "queue size", "queue capacity"],
    "variability": ["variability", "variation", "variance", "stochastic"],
    "flow efficiency": ["flow", "cycle time", "throughput", "lead time"],
    "economic framework": ["economic", "economics", "profit", "revenue", "value"],
    "cadence": ["cadence", "synchronization", "rhythm", "periodic"],
    "fast feedback": ["feedback", "feedback loop", "fast feedback", "information"],
    "decentralized control": ["decentralized", "local control", "autonomous", "authority"],
    "design reviews": ["design review", "review", "gate", "phase gate"],
    "sequencing": ["sequencing", "priority", "wsjf", "weighted shortest job first"],
    "risk management": ["risk", "uncertainty", "probability", "payoff"],
    "lean product development": ["lean", "toyota", "kanban", "pull system"],
    "product development": ["product development", "development process", "r&d"],
    "resource utilization": ["utilization", "capacity", "idle", "busy"],
    "congestion": ["congestion", "bottleneck", "blocking", "starvation"],
    "margin": ["margin", "operating margin", "slack", "buffer"],
}


def tag_chunk(text: str, source_collection: str) -> list[str]:
    """Tag a chunk with matching concepts based on regex patterns."""
    concepts = NKS_CONCEPTS if source_collection == "nks" else DEVFLOW_CONCEPTS
    text_lower = text.lower()
    matched = []

    for concept, patterns in concepts.items():
        for pattern in patterns:
            if re.search(pattern, text_lower):
                matched.append(concept)
                break

    return matched


def llm_tag_chunk(text: str, domain_name: str, domain_description: str) -> list[str]:
    """Use LLM to extract domain-relevant concepts from arbitrary text."""
    from grids.orchestration.agents import get_llm
    from langchain_core.messages import HumanMessage

    llm = get_llm(temperature=0.1)
    response = llm.invoke([HumanMessage(content=(
        f"You are a concept tagger for the domain: {domain_name} ({domain_description}).\n\n"
        f"Extract 2-5 key domain concepts from this text. Return ONLY a JSON array of short concept strings.\n\n"
        f"Text:\n{text[:800]}"
    ))])

    result_text = response.content.strip()
    try:
        if "```" in result_text:
            result_text = result_text.split("```")[1]
            if result_text.startswith("json"):
                result_text = result_text[4:]
            result_text = result_text.strip()
        concepts = json.loads(result_text)
        if isinstance(concepts, list):
            return [str(c) for c in concepts]
    except (json.JSONDecodeError, IndexError):
        pass
    return []


def llm_tag_chunks_file(
    chunks_path: str,
    domain_name: str,
    domain_description: str,
    output_path: str | None = None,
    batch_size: int = 10,
) -> tuple[int, int]:
    """Tag chunks in a JSONL file using LLM-based concept extraction.

    Processes in batches to reduce LLM calls -- sends multiple chunks per request.
    """
    from grids.orchestration.agents import get_llm
    from langchain_core.messages import HumanMessage

    with open(chunks_path, "r", encoding="utf-8") as f:
        chunks = [json.loads(line) for line in f if line.strip()]

    llm = get_llm(temperature=0.1)
    tagged_count = 0

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        texts = "\n\n---CHUNK---\n\n".join(
            f"[{j}] {c['text'][:300]}" for j, c in enumerate(batch)
        )

        response = llm.invoke([HumanMessage(content=(
            f"You are a concept tagger for: {domain_name} ({domain_description}).\n\n"
            f"For each numbered chunk below, extract 2-5 key domain concepts.\n"
            f"Return a JSON object mapping chunk index to concept array.\n"
            f"Example: {{\"0\": [\"concept-a\", \"concept-b\"], \"1\": [\"concept-c\"]}}\n\n"
            f"{texts}"
        ))])

        result_text = response.content.strip()
        try:
            if "```" in result_text:
                result_text = result_text.split("```")[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]
                result_text = result_text.strip()
            mapping = json.loads(result_text)
            for idx_str, concepts in mapping.items():
                idx = int(idx_str)
                if 0 <= idx < len(batch):
                    batch[idx].setdefault("metadata", {})["concepts"] = concepts
                    tagged_count += 1
        except (json.JSONDecodeError, IndexError, ValueError):
            pass

    out = output_path or chunks_path
    with open(out, "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk) + "\n")

    return len(chunks), tagged_count


def enrich_chunks_file(chunks_path: str, collection_name: str, output_path: str | None = None):
    """Read chunks.jsonl, add concept tags, write back."""
    enriched = []
    with open(chunks_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            chunk = json.loads(line)
            concepts = tag_chunk(chunk["text"], collection_name)
            chunk["metadata"]["concepts"] = concepts
            enriched.append(chunk)

    out = output_path or chunks_path
    with open(out, "w", encoding="utf-8") as f:
        for chunk in enriched:
            f.write(json.dumps(chunk) + "\n")

    tagged_count = sum(1 for c in enriched if c["metadata"].get("concepts"))
    return len(enriched), tagged_count
