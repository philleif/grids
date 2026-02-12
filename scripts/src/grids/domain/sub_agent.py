"""Sub-agent -- obsessive about one specific aspect of the domain skill."""

import json

from langchain_core.messages import HumanMessage, SystemMessage

from grids.domain.config import DomainConfig, SubAgentConfig
from grids.knowledge.store import query_store
from grids.orchestration.agents import get_llm


class SubAgentScore:
    """Score from a single sub-agent's evaluation."""

    def __init__(self, agent_name: str, aspect: str, score: float, verdict: str, feedback: str):
        self.agent_name = agent_name
        self.aspect = aspect
        self.score = score  # 0.0 - 1.0
        self.verdict = verdict  # "pass" or "fail"
        self.feedback = feedback

    def to_dict(self) -> dict:
        return {
            "agent_name": self.agent_name,
            "aspect": self.aspect,
            "score": round(self.score, 3),
            "verdict": self.verdict,
            "feedback": self.feedback,
        }


def _build_sub_prompt(sa_config: SubAgentConfig, domain_config: DomainConfig, context: str) -> str:
    """Build a narrow, obsessive system prompt for this sub-agent."""
    prompt = (
        f"You are a specialist sub-agent in the domain: {domain_config.domain.name}\n"
        f"Your ONLY concern is: {sa_config.aspect}\n\n"
        f"You are obsessively focused on this single aspect. You evaluate work product "
        f"SOLELY through the lens of: {sa_config.aspect}\n\n"
        f"Key concepts you care about: {', '.join(sa_config.concepts)}\n\n"
        f"Strictness level: {sa_config.strictness:.0%} "
        f"({'extremely strict' if sa_config.strictness >= 0.9 else 'strict' if sa_config.strictness >= 0.7 else 'moderate'})\n"
    )
    if context:
        prompt += f"\nRelevant domain knowledge:\n{context}\n"
    return prompt


class SubAgent:
    """A sub-agent obsessive about one aspect of domain expertise."""

    def __init__(self, sa_config: SubAgentConfig, domain_config: DomainConfig):
        self.config = sa_config
        self.domain_config = domain_config

    def _retrieve_context(self, query: str, n: int = 3) -> str:
        """Retrieve context focused on this sub-agent's concepts."""
        concept_query = f"{query} {' '.join(self.config.concepts)}"
        parts = []
        for coll in self.domain_config.master.knowledge_collections:
            try:
                hits = query_store(concept_query, coll, n_results=n)
                for hit in hits:
                    source = hit.get("metadata", {}).get("source", coll)
                    excerpt = hit["text"][:400]
                    parts.append(f"[{source}] {excerpt}")
            except Exception:
                continue
        return "\n---\n".join(parts)

    def score(self, work_product: dict, brief: str) -> SubAgentScore:
        """Score work product on this agent's aspect only."""
        llm = get_llm(temperature=max(0.1, 0.5 - self.config.strictness * 0.4))

        context = self._retrieve_context(brief)
        system = _build_sub_prompt(self.config, self.domain_config, context)

        product_str = json.dumps(work_product, indent=2)[:3000]

        messages = [
            SystemMessage(content=system),
            HumanMessage(content=(
                f"Score this work product on your aspect: {self.config.aspect}\n\n"
                f"Brief: {brief}\n\n"
                f"Work product:\n{product_str}\n\n"
                f"Evaluate ONLY your aspect. Be {'extremely ' if self.config.strictness >= 0.9 else ''}strict.\n\n"
                "Output ONLY a JSON object:\n"
                "{\n"
                '  "score": 0.0-1.0,\n'
                '  "verdict": "pass" or "fail",\n'
                '  "feedback": "specific feedback referencing domain principles"\n'
                "}"
            )),
        ]

        response = llm.invoke(messages)
        text = response.content.strip()

        try:
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            parsed = json.loads(text)
            return SubAgentScore(
                agent_name=self.config.name,
                aspect=self.config.aspect,
                score=float(parsed.get("score", 0.5)),
                verdict=parsed.get("verdict", "fail"),
                feedback=parsed.get("feedback", ""),
            )
        except (json.JSONDecodeError, IndexError, ValueError):
            return SubAgentScore(
                agent_name=self.config.name,
                aspect=self.config.aspect,
                score=0.5,
                verdict="fail",
                feedback=f"Failed to parse scoring response: {text[:200]}",
            )
