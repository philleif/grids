"""LangGraph agent definitions -- each agent is a node with a small rule set and knowledge access.

Agents follow the Wolfram principle: simple local rules, complex emergent behavior.
They consult the ChromaDB knowledge store for relevant insights from NKS and Dev Flow.
"""

import os

import httpx
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from grids.knowledge.store import query_all, query_store
from grids.orchestration.rules import AgentState, Signal, Action, RuleTable, ALL_RULE_TABLES

# CLIProxyAPI local proxy -- exposes OpenAI-compatible /v1/chat/completions
# Auth is handled by Claude OAuth; the proxy routes based on User-Agent header.
DEFAULT_BASE_URL = os.environ.get("GRIDS_LLM_BASE_URL", "http://localhost:8317/v1")
DEFAULT_MODEL = os.environ.get("GRIDS_LLM_MODEL", "claude-opus-4-6")

_http_client = httpx.Client(headers={"User-Agent": "claude-code/1.0"})


def get_llm(model: str | None = None, temperature: float = 0.7) -> ChatOpenAI:
    return ChatOpenAI(
        model=model or DEFAULT_MODEL,
        temperature=temperature,
        max_tokens=4096,
        base_url=DEFAULT_BASE_URL,
        api_key="not-needed",
        http_client=_http_client,
    )


def _retrieve_context(query: str, collection: str | None = None, n: int = 3) -> str:
    """Pull relevant knowledge from the book corpus."""
    if collection:
        hits = query_store(query, collection, n_results=n)
        results = {collection: hits}
    else:
        results = query_all(query, n_results=n)

    if not results:
        return ""

    context_parts = []
    for coll_name, hits in results.items():
        for hit in hits:
            section = hit.get("metadata", {}).get("section", "")
            source = hit.get("metadata", {}).get("source", coll_name)
            concepts = hit.get("metadata", {}).get("concepts", "")
            excerpt = hit["text"][:600]
            context_parts.append(
                f"[{source} / {section}] (concepts: {concepts})\n{excerpt}"
            )
    return "\n---\n".join(context_parts)


# --- Agent system prompts (kept small per Wolfram principle) ---

RESEARCH_PROMPT = """You are the Research agent in an emergent orchestration system for building
specialized software tools for creative professionals.

Your job: given a tool specification, find relevant architectural patterns, principles,
and domain knowledge from Wolfram's NKS and Reinertsen's Product Development Flow.

Rules:
- Pull 3-5 relevant knowledge fragments per specification element
- NKS informs architecture: simple rules, computational equivalence, emergent complexity
- Dev Flow informs process: queue management, WIP limits, batch sizing, cost of delay
- Tag each reference with how it applies to tool design (not creative output)
- Prefer concrete, applicable principles over abstract ones

Output a JSON list of research findings, each with: source, excerpt, relevance, concepts."""

CONCEPT_PROMPT = """You are the Concept agent in an emergent orchestration system for building
specialized software tools for creative professionals.

Your job: transform research findings into structured tool design concepts.

Rules:
- Synthesize findings into actionable architectural decisions
- Each concept maps to a concrete tool feature or system behavior
- Apply NKS: small composable rules -> powerful emergent capabilities
- Apply Dev Flow: optimize for flow, minimize WIP, reduce batch size, sequence by value

Output a JSON list of concepts, each with: id, title, description, implementation_hints, references."""

LAYOUT_PROMPT = """You are the Layout agent in an emergent orchestration system for building
specialized software tools for creative professionals.

Your job: organize tool concepts into a structured system architecture and UI layout.

Rules:
- Design modular components that compose (Wolfram: simple parts -> complex wholes)
- Arrange features into workflows that minimize friction (Reinertsen: flow efficiency)
- Use grid-based layout for any UI components
- Prioritize the features that reduce cost of delay for the end user

Output JSON specifications: components, their relationships, and layout where applicable."""

CRITIQUE_PROMPT = """You are the Critique agent in an emergent orchestration system for building
specialized software tools for creative professionals.

Your job: evaluate whether the proposed tool design serves its target users well.

Rules:
- Score on: architectural soundness, user workflow fit, composability, simplicity
- Check: does it follow NKS principle (simple rules, emergent power)?
- Check: does it follow Dev Flow principle (optimize flow, not resource utilization)?
- Be specific: cite which design elements work and which don't
- Decide: APPROVE (build it) or ITERATE (revise with feedback)
- Max 3 iterations per design; after that, approve with notes

Output JSON: {verdict: "approve"|"iterate", scores: {...}, feedback: "...", iteration: N}."""


AGENT_PROMPTS = {
    "research": RESEARCH_PROMPT,
    "concept": CONCEPT_PROMPT,
    "layout": LAYOUT_PROMPT,
    "critique": CRITIQUE_PROMPT,
}


def invoke_agent(
    agent_name: str,
    input_text: str,
    brief: str = "",
    model: str | None = None,
    temperature: float = 0.7,
) -> str:
    """Invoke a single agent with knowledge retrieval and its rule-based system prompt."""
    llm = get_llm(model, temperature)
    system_prompt = AGENT_PROMPTS.get(agent_name, "You are a helpful creative agent.")

    # Retrieve relevant context from the knowledge base
    context_query = f"{brief} {input_text[:200]}" if brief else input_text[:400]
    knowledge_context = _retrieve_context(context_query, n=3)

    messages = [
        SystemMessage(content=system_prompt),
    ]

    if knowledge_context:
        messages.append(HumanMessage(content=f"Relevant knowledge from reference texts:\n\n{knowledge_context}"))

    if brief:
        messages.append(HumanMessage(content=f"Creative brief: {brief}"))

    messages.append(HumanMessage(content=input_text))

    response = llm.invoke(messages)
    return response.content
