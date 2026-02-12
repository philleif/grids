"""Parse stream.jsonl into structured event lists for analysis.

Reads the JSONL log produced by `grids-run --log-stream` and produces:
- LLM call pairs (start + end) with domain, agent, action, tokens, response
- Tick events with per-cell action breakdowns
- Phase transitions and consolidation events
- Chat summaries (LLM-generated or truncated response)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LLMCall:
    seq: int
    ts: float
    domain: str
    agent: str
    role: str
    action: str
    pos: str
    tokens: int
    response: str
    work_preview: str = ""
    score: int | float | None = None
    verdict: str | None = None
    chat_summary: str = ""
    response_chars: int = 0
    tick: int = 0

    def to_chat_dict(self) -> dict:
        return {
            "seq": self.seq,
            "domain": self.domain,
            "agent": self.agent,
            "role": self.role,
            "action": self.action,
            "pos": self.pos,
            "tokens": self.tokens,
            "score": self.score,
            "verdict": self.verdict,
            "chat_summary": self.chat_summary,
            "response_chars": self.response_chars,
            "work_preview": self.work_preview[:200],
        }


@dataclass
class TickEvent:
    seq: int
    ts: float
    tick: int
    actions: int
    llm_calls: int
    emitted: int
    elapsed: float
    cell_actions: list[dict] = field(default_factory=list)
    # Two-level metrics (GRD-6) -- present in newer stream logs
    routing_scheduled: int = 0
    routing_delivered: int = 0
    routing_rejected: int = 0
    critique_scores: list[float] = field(default_factory=list)
    critique_verdicts: list[str] = field(default_factory=list)
    rework_count: int = 0


@dataclass
class RoutingSummary:
    """Retroactively computed routing metrics from stream.jsonl tick events."""
    items_scheduled: int = 0
    items_delivered: int = 0
    items_rejected: int = 0

    @property
    def routing_efficiency(self) -> float:
        if self.items_scheduled == 0:
            return 0.0
        return self.items_delivered / self.items_scheduled

    def to_dict(self) -> dict:
        return {
            "items_scheduled": self.items_scheduled,
            "items_delivered": self.items_delivered,
            "items_rejected": self.items_rejected,
            "routing_efficiency": round(self.routing_efficiency, 3),
        }


@dataclass
class QualitySummary:
    """Retroactively computed quality metrics from stream.jsonl."""
    critique_scores: list[float] = field(default_factory=list)
    critique_verdicts: list[str] = field(default_factory=list)
    rework_count: int = 0

    @property
    def avg_critique_score(self) -> float | None:
        if not self.critique_scores:
            return None
        return sum(self.critique_scores) / len(self.critique_scores)

    @property
    def verdict_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for v in self.critique_verdicts:
            counts[v] = counts.get(v, 0) + 1
        return counts

    def to_dict(self) -> dict:
        avg = self.avg_critique_score
        return {
            "critique_scores": self.critique_scores,
            "avg_critique_score": round(avg, 2) if avg is not None else None,
            "critique_verdicts": self.verdict_counts,
            "rework_count": self.rework_count,
        }


@dataclass
class ParsedStream:
    """All structured data extracted from a stream.jsonl file."""
    run_start: dict = field(default_factory=dict)
    run_end: dict = field(default_factory=dict)
    llm_calls: list[LLMCall] = field(default_factory=list)
    ticks: list[TickEvent] = field(default_factory=list)
    phase_starts: list[dict] = field(default_factory=list)
    consolidations: list[dict] = field(default_factory=list)
    brief: str = ""
    seed: str = ""
    grid_size: str = ""
    cell_count: int = 0
    total_tokens: int = 0

    @property
    def domains(self) -> list[str]:
        seen = {}
        for c in self.llm_calls:
            if c.domain not in seen:
                seen[c.domain] = True
        return list(seen.keys())

    @property
    def active_ticks(self) -> int:
        return sum(1 for t in self.ticks if t.llm_calls > 0)

    @property
    def quiescent_ticks(self) -> int:
        return sum(1 for t in self.ticks if t.llm_calls == 0)

    def calls_by_domain(self) -> dict[str, list[LLMCall]]:
        out: dict[str, list[LLMCall]] = {}
        for c in self.llm_calls:
            out.setdefault(c.domain, []).append(c)
        return out

    def calls_by_action(self) -> dict[str, list[LLMCall]]:
        out: dict[str, list[LLMCall]] = {}
        for c in self.llm_calls:
            out.setdefault(c.action, []).append(c)
        return out

    def critique_scores(self) -> list[dict]:
        return [
            {"domain": c.domain, "agent": c.agent, "score": c.score, "verdict": c.verdict, "seq": c.seq}
            for c in self.llm_calls
            if c.score is not None
        ]

    def compute_routing_summary(self) -> RoutingSummary:
        """Retroactively compute routing metrics from tick events."""
        rs = RoutingSummary()
        for t in self.ticks:
            # Try new fields first (GRD-6 format)
            sched = t.routing_scheduled
            deliv = t.routing_delivered
            rej = t.routing_rejected
            if sched > 0:
                rs.items_scheduled += sched
                rs.items_delivered += deliv
                rs.items_rejected += rej
            else:
                # Fallback: old format uses emitted as proxy
                rs.items_scheduled += t.emitted
                rs.items_delivered += t.emitted
        return rs

    def compute_quality_summary(self) -> QualitySummary:
        """Retroactively compute quality metrics from LLM call scores/verdicts."""
        qs = QualitySummary()
        for c in self.llm_calls:
            if c.score is not None:
                qs.critique_scores.append(float(c.score))
            if c.verdict is not None:
                qs.critique_verdicts.append(c.verdict)
                if c.verdict in ("fail", "iterate"):
                    qs.rework_count += 1
        # Also check tick events for GRD-6 format
        for t in self.ticks:
            for s in t.critique_scores:
                if s not in qs.critique_scores:
                    qs.critique_scores.append(s)
            for v in t.critique_verdicts:
                qs.critique_verdicts.append(v)
            qs.rework_count += t.rework_count
        return qs


def parse_stream(stream_path: str | Path) -> ParsedStream:
    """Parse a stream.jsonl file into structured data."""
    stream_path = Path(stream_path)
    result = ParsedStream()

    pending_starts: dict[str, dict] = {}  # keyed by "domain/agent"
    current_tick = 0
    # LLM calls that happen before the first tick event belong to tick 1
    first_tick_seen = False

    with open(stream_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = entry.get("type", "")

            if etype == "run_start":
                result.run_start = entry
                result.brief = entry.get("brief", "")
                result.seed = entry.get("seed", "")
                result.grid_size = entry.get("grid_size", "")
                result.cell_count = entry.get("cell_count", 0)

            elif etype == "run_end":
                result.run_end = entry

            elif etype == "phase_start":
                result.phase_starts.append(entry)

            elif etype in ("consolidation_start", "consolidation_end"):
                result.consolidations.append(entry)

            elif etype == "tick":
                te = TickEvent(
                    seq=entry.get("seq", 0),
                    ts=entry.get("ts", 0),
                    tick=entry.get("tick", 0),
                    actions=entry.get("actions", 0),
                    llm_calls=entry.get("llm_calls", 0),
                    emitted=entry.get("emitted", 0),
                    elapsed=entry.get("elapsed", 0),
                    cell_actions=entry.get("cell_actions", []),
                    routing_scheduled=entry.get("routing_scheduled", 0),
                    routing_delivered=entry.get("routing_delivered", 0),
                    routing_rejected=entry.get("routing_rejected", 0),
                    critique_scores=entry.get("critique_scores", []),
                    critique_verdicts=entry.get("critique_verdicts", []),
                    rework_count=entry.get("rework_count", 0),
                )
                result.ticks.append(te)
                # Backfill: LLM calls before the first tick event belong to tick 1
                if not first_tick_seen:
                    first_tick_seen = True
                    for call in result.llm_calls:
                        if call.tick == 0:
                            call.tick = te.tick
                current_tick = te.tick

            elif etype == "llm_start":
                key = f"{entry.get('domain', '')}/{entry.get('agent', '')}"
                pending_starts[key] = entry

            elif etype == "llm_end":
                key = f"{entry.get('domain', '')}/{entry.get('agent', '')}"
                start = pending_starts.pop(key, {})
                response = entry.get("response", "")
                tokens = entry.get("token_count", 0)
                result.total_tokens += tokens

                score, verdict = _extract_score_verdict(response)

                call = LLMCall(
                    seq=entry.get("seq", 0),
                    ts=entry.get("ts", 0),
                    domain=entry.get("domain", ""),
                    agent=entry.get("agent", ""),
                    role=start.get("role", ""),
                    action=entry.get("action", ""),
                    pos=start.get("pos", ""),
                    tokens=tokens,
                    response=response,
                    work_preview=start.get("work_preview", ""),
                    score=score,
                    verdict=verdict,
                    response_chars=len(response),
                    tick=current_tick,
                )
                result.llm_calls.append(call)

    return result


def _extract_score_verdict(response: str) -> tuple[int | float | None, str | None]:
    """Extract score and verdict from a response that may contain JSON."""
    score = None
    verdict = None
    try:
        blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", response, re.DOTALL)
        for block in blocks:
            try:
                parsed = json.loads(block)
                if isinstance(parsed, dict):
                    if "score" in parsed:
                        score = parsed["score"]
                    if "verdict" in parsed:
                        verdict = parsed["verdict"]
                    if score is not None:
                        return score, verdict
            except json.JSONDecodeError:
                continue
        # Try parsing the whole response as JSON
        parsed = json.loads(response.strip())
        if isinstance(parsed, dict):
            score = parsed.get("score")
            verdict = parsed.get("verdict")
    except (json.JSONDecodeError, ValueError):
        pass
    return score, verdict


def generate_chat_summaries(
    parsed: ParsedStream,
    use_llm: bool = True,
    max_inline_chars: int = 600,
) -> None:
    """Generate chat_summary for each LLM call.

    For short responses, uses the first paragraph directly.
    For longer responses, calls the LLM to summarize (if use_llm=True).
    Mutates the LLMCall objects in place.
    """
    calls_needing_summary = []

    for call in parsed.llm_calls:
        response = call.response.strip()
        # Strip JSON code blocks for summary purposes
        clean = re.sub(r"```(?:json)?\s*\n.*?```", "", response, flags=re.DOTALL).strip()
        if not clean:
            clean = response[:500]

        if len(clean) <= max_inline_chars:
            call.chat_summary = clean
        else:
            # Take first meaningful paragraph
            paragraphs = [p.strip() for p in clean.split("\n\n") if p.strip()]
            preview = paragraphs[0] if paragraphs else clean[:400]
            if len(preview) > max_inline_chars:
                preview = preview[:max_inline_chars] + "..."
            if use_llm:
                calls_needing_summary.append(call)
            else:
                call.chat_summary = preview

    if use_llm and calls_needing_summary:
        _batch_summarize(calls_needing_summary)


def _batch_summarize(calls: list[LLMCall]) -> None:
    """Summarize multiple LLM call responses via a single batched LLM call."""
    try:
        from grids.orchestration.agents import get_llm
        from langchain_core.messages import HumanMessage, SystemMessage
    except ImportError:
        # Fallback: use first paragraph
        for call in calls:
            clean = re.sub(r"```(?:json)?\s*\n.*?```", "", call.response, flags=re.DOTALL).strip()
            paragraphs = [p.strip() for p in clean.split("\n\n") if p.strip()]
            call.chat_summary = (paragraphs[0] if paragraphs else clean[:400])[:600]
        return

    llm = get_llm(temperature=0.1)

    # Process in batches of 5 to keep context manageable
    batch_size = 5
    for i in range(0, len(calls), batch_size):
        batch = calls[i:i + batch_size]
        entries = []
        for j, call in enumerate(batch):
            response_preview = call.response[:2000]
            entries.append(
                f"[{j}] {call.domain}/{call.agent} ({call.action}):\n{response_preview}\n"
            )

        prompt = (
            "Summarize each agent output below in 2-3 sentences. "
            "Capture the key contribution, any scores/verdicts, and the most distinctive insight. "
            "Output one summary per line, prefixed with the index number.\n\n"
            + "\n---\n".join(entries)
        )

        try:
            response = llm.invoke([
                SystemMessage(content="You produce concise summaries of AI agent outputs for a session report."),
                HumanMessage(content=prompt),
            ])
            text = response.content.strip()
            lines = text.split("\n")
            for line in lines:
                match = re.match(r"\[?(\d+)\]?\s*[:\-]?\s*(.*)", line.strip())
                if match:
                    idx = int(match.group(1))
                    summary = match.group(2).strip()
                    if 0 <= idx < len(batch) and summary:
                        batch[idx].chat_summary = summary
        except Exception:
            pass

        # Fallback for any that didn't get summarized
        for call in batch:
            if not call.chat_summary:
                clean = re.sub(r"```(?:json)?\s*\n.*?```", "", call.response, flags=re.DOTALL).strip()
                paragraphs = [p.strip() for p in clean.split("\n\n") if p.strip()]
                call.chat_summary = (paragraphs[0] if paragraphs else clean[:400])[:600]
