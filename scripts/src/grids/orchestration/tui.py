"""Real-time TUI for the ANKOS grid run.

Rich Live display with:
- Left panel: CA grid state (colored cells by domain, state indicators)
- Right panel: streaming LLM output (token-by-token, tagged by agent)
- Bottom: tick counter, metrics, progress

Each cell in the grid is colored by domain and shows state:
  . = idle (no work)    W = working    C = critiquing
  ~ = waiting           X = blocked    1-9 = inbox count (idle with work)
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from typing import Any, Callable

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from grids.orchestration.grid import AgentGrid, AgentCell, CellOutput, WorkFragment
from grids.orchestration.rules import AgentState, Action
from grids.orchestration.tick import TickResult

from grids.domain_colors import rich_color, rich_colors_for_grid

STATE_CHARS = {
    AgentState.IDLE: ".",
    AgentState.WORKING: "W",
    AgentState.WAITING: "~",
    AgentState.CRITIQUING: "C",
    AgentState.BLOCKED: "X",
}

MAX_STREAM_LINES = 80
MAX_LINE_WIDTH = 90


class StreamBuffer:
    """Thread-safe buffer for LLM token streaming."""

    def __init__(self, max_lines: int = MAX_STREAM_LINES):
        self.max_lines = max_lines
        self.lines: deque[Text] = deque(maxlen=max_lines)
        self._current_line: Text | None = None
        self._current_label: str = ""
        self._lock = threading.Lock()

    def start_stream(self, label: str, style: str = ""):
        """Begin a new streaming line with a label."""
        with self._lock:
            self._current_label = label
            self._current_line = Text()
            self._current_line.append(f"[{label}] ", style=style or "bold")

    def append_token(self, token: str):
        """Append a token to the current streaming line."""
        with self._lock:
            if self._current_line is None:
                self._current_line = Text()
            self._current_line.append(token)
            # Wrap long lines
            if len(self._current_line.plain) > MAX_LINE_WIDTH:
                self.lines.append(self._current_line)
                self._current_line = Text()
                self._current_line.append("  ", style="dim")

    def end_stream(self):
        """Finalize the current streaming line."""
        with self._lock:
            if self._current_line is not None and len(self._current_line.plain) > 0:
                self.lines.append(self._current_line)
            self._current_line = None
            self._current_label = ""

    def add_event(self, text: str, style: str = ""):
        """Add a non-streaming event line."""
        with self._lock:
            line = Text(text, style=style)
            self.lines.append(line)

    def get_display(self) -> list[Text]:
        """Get current lines for display."""
        with self._lock:
            result = list(self.lines)
            if self._current_line is not None and len(self._current_line.plain) > 0:
                result.append(self._current_line)
            return result


class GridTUI:
    """Rich Live TUI for watching the CA grid run."""

    def __init__(self, grid: AgentGrid):
        self.grid = grid
        self.domain_colors = rich_colors_for_grid(grid)
        self.stream = StreamBuffer()
        self.last_tick: TickResult | None = None
        self.total_llm_calls = 0
        self.total_ticks = 0
        self.start_time = time.time()
        self._live: Live | None = None

    def make_layout(self) -> Layout:
        layout = Layout()
        layout.split_row(
            Layout(name="grid", ratio=1, minimum_size=30),
            Layout(name="stream", ratio=2),
        )
        layout["grid"].split_column(
            Layout(name="grid_view", ratio=3),
            Layout(name="metrics", ratio=1, minimum_size=8),
        )
        return layout

    def render_grid_panel(self) -> Panel:
        """Render the CA grid as a colored Rich panel."""
        grid_text = Text()

        # Header row with column numbers
        grid_text.append("   ", style="dim")
        for x in range(self.grid.width):
            grid_text.append(f"{x}", style="dim")
        grid_text.append("\n")

        for y in range(self.grid.height):
            grid_text.append(f"{y:2d} ", style="dim")
            for x in range(self.grid.width):
                cell = self.grid.get((x, y))
                if cell is None:
                    grid_text.append(" ", style="dim")
                else:
                    ch, style = _cell_char_style(cell, self.domain_colors)
                    grid_text.append(ch, style=style)
            grid_text.append("\n")

        # Legend
        grid_text.append("\n", style="dim")
        for domain, color in self.domain_colors.items():
            cells = self.grid.cells_by_domain(domain)
            if cells:
                grid_text.append(f"  {domain}", style=color)
                grid_text.append(f" ({len(cells)})", style="dim")
                grid_text.append("  ")
        grid_text.append("\n")
        grid_text.append("  . idle  W working  C critiquing  ~ waiting  X blocked", style="dim")

        return Panel(grid_text, title=f"Grid {self.grid.width}x{self.grid.height}", border_style="cyan")

    def render_metrics_panel(self) -> Panel:
        """Render tick metrics."""
        elapsed = time.time() - self.start_time
        t = self.last_tick

        metrics = Text()
        metrics.append(f"Tick: {self.total_ticks}", style="bold cyan")
        metrics.append(f"  LLM calls: {self.total_llm_calls}", style="bold")
        metrics.append(f"  Elapsed: {elapsed:.0f}s\n", style="dim")

        if t:
            metrics.append(f"Last tick: ", style="dim")
            metrics.append(f"actions={t.actions_taken} ", style="green" if t.actions_taken > 0 else "dim")
            metrics.append(f"emitted={t.items_emitted} ", style="yellow" if t.items_emitted > 0 else "dim")
            metrics.append(f"propagated={t.propagations}\n", style="blue" if t.propagations > 0 else "dim")

        # State distribution
        for state in AgentState:
            count = len(self.grid.cells_by_state(state))
            if count > 0:
                metrics.append(f"  {state.value}: {count}", style="bold" if state == AgentState.WORKING else "dim")
                metrics.append("  ")

        quiescent = self.grid.is_quiescent()
        if quiescent:
            metrics.append("\n  QUIESCENT", style="bold green")

        return Panel(metrics, title="Metrics", border_style="dim")

    def render_stream_panel(self) -> Panel:
        """Render the LLM output stream."""
        lines = self.stream.get_display()
        # Show last N lines that fit
        display_lines = lines[-40:]
        content = Group(*display_lines) if display_lines else Text("Waiting for first tick...", style="dim")
        return Panel(content, title="Agent Stream", border_style="yellow")

    def render(self) -> Layout:
        layout = self.make_layout()
        layout["grid_view"].update(self.render_grid_panel())
        layout["metrics"].update(self.render_metrics_panel())
        layout["stream"].update(self.render_stream_panel())
        return layout

    def on_tick(self, result: TickResult):
        """Callback for each tick completion."""
        self.last_tick = result
        self.total_ticks = result.tick
        self.total_llm_calls += result.llm_calls

        # Log tick event
        active = [a for a in result.cell_actions if not a.get("skipped")]
        self.stream.add_event(
            f"--- tick {result.tick} --- "
            f"actions={result.actions_taken} emitted={result.items_emitted} "
            f"({result.elapsed_seconds:.1f}s)",
            style="bold dim",
        )
        for a in active[:6]:
            domain = a.get("domain", "?")
            agent = a.get("agent", "?")
            action = a.get("action", "?")
            color = self.domain_colors.get(domain, "white")
            self.stream.add_event(f"  {domain}/{agent}: {action}", style=color)

    def make_streaming_invoke_fn(self, base_invoke_fn):
        """Wrap an invoke_fn to stream tokens through the TUI buffer."""
        from grids.orchestration.agents import get_llm
        from langchain_core.messages import HumanMessage, SystemMessage

        tui = self

        def streaming_invoke_fn(
            cell: AgentCell,
            action: Action,
            work: WorkFragment | None,
            neighbors: list[CellOutput],
        ) -> Any:
            domain = cell.domain
            agent = cell.agent_type
            color = tui.domain_colors.get(domain, "white")
            label = f"{domain}/{agent}"

            tui.stream.start_stream(label, style=color)
            tui.stream.append_token(f"{action.value}: ")

            # Use the base invoke fn but intercept for streaming
            # For now, call the base and stream the result character by character
            # (True streaming requires modifying invoke.py to use llm.stream())
            result = base_invoke_fn(cell, action, work, neighbors)

            if result is not None:
                result_str = result if isinstance(result, str) else json.dumps(result, indent=2)[:500]
                # Stream the result in chunks to simulate token flow
                chunk_size = 8
                for i in range(0, min(len(result_str), 400), chunk_size):
                    chunk = result_str[i:i + chunk_size]
                    tui.stream.append_token(chunk)
                    if tui._live:
                        tui._live.update(tui.render())
                if len(result_str) > 400:
                    tui.stream.append_token("...")

            tui.stream.end_stream()
            if tui._live:
                tui._live.update(tui.render())

            return result

        return streaming_invoke_fn

    def make_true_streaming_invoke_fn(self):
        """Create an invoke_fn that does real token-by-token LLM streaming.

        Each LLM call uses .stream() and pushes tokens to the TUI buffer
        as they arrive.
        """
        from grids.orchestration.agents import get_llm
        from grids.orchestration.invoke import (
            _get_cell_context, _neighbor_summary, _content_str,
            _master_system_prompt, _parse_json_or_text,
        )
        from grids.knowledge.store import query_store
        from langchain_core.messages import HumanMessage, SystemMessage

        tui = self

        def invoke_fn(
            cell: AgentCell,
            action: Action,
            work: WorkFragment | None,
            neighbors: list[CellOutput],
        ) -> Any:
            if work is None and action not in (Action.EMIT,):
                return None

            domain = cell.domain
            agent = cell.agent_type
            color = tui.domain_colors.get(domain, "white")
            label = f"{domain}/{agent}"

            messages = _build_messages(cell, action, work, neighbors)
            if messages is None:
                return None

            tui.stream.start_stream(label, style=color)
            tui.stream.append_token(f"[{action.value}] ")

            # Stream from LLM
            llm = get_llm(temperature=_temp_for_role(cell.role))
            collected = []

            try:
                for chunk in llm.stream(messages):
                    token = chunk.content if hasattr(chunk, "content") else str(chunk)
                    if token:
                        collected.append(token)
                        tui.stream.append_token(token)
                        if tui._live:
                            tui._live.update(tui.render())
            except Exception as e:
                tui.stream.append_token(f" [ERROR: {e}]")
                tui.stream.end_stream()
                return None

            tui.stream.end_stream()

            full_text = "".join(collected)
            return _parse_json_or_text(full_text)

        return invoke_fn


def _build_messages(cell: AgentCell, action: Action, work: WorkFragment | None, neighbors: list[CellOutput]):
    """Build LLM messages for any cell type. Mirrors invoke.py logic but returns messages."""
    from grids.orchestration.invoke import (
        _get_cell_context, _neighbor_summary, _content_str,
        _master_system_prompt,
    )
    from langchain_core.messages import HumanMessage, SystemMessage

    content = _content_str(work.content) if work else ""
    neighbor_ctx = _neighbor_summary(neighbors)
    context = _get_cell_context(cell, content[:300]) if cell.knowledge_collections else ""

    if cell.role == "master":
        if action == Action.PROCESS:
            return [
                SystemMessage(content=_master_system_prompt(cell, context)),
                HumanMessage(content=(
                    f"Decompose this into a structured work specification with concrete, "
                    f"buildable components. Your domain is: {cell.domain}.\n\n"
                    f"Input:\n{content}\n\n"
                    f"Output JSON with: title, description, components, acceptance_criteria."
                )),
            ]
        elif action == Action.CRITIQUE:
            return [
                SystemMessage(content=_master_system_prompt(cell, context)),
                HumanMessage(content=(
                    f"Validate this work product from your domain perspective ({cell.domain}).\n\n"
                    f"Work product:\n{content}\n\nNeighbor context:\n{neighbor_ctx}\n\n"
                    f"Score 0.0-1.0. JSON: {{\"score\": N, \"verdict\": \"approve\"|\"iterate\", \"feedback\": \"...\"}}"
                )),
            ]

    elif cell.role == "research":
        # Research: retrieve from knowledge store + synthesize
        query = content[:500]
        findings_parts = []
        for coll in cell.knowledge_collections[:4]:
            try:
                hits = query_store(query, coll, n_results=3)
                for hit in hits:
                    findings_parts.append(f"[{coll}] {hit['text'][:300]}")
            except Exception:
                continue
        findings_text = "\n---\n".join(findings_parts[:6])
        return [
            SystemMessage(content=(
                f"You are a research agent for {cell.domain}. "
                f"Synthesize these knowledge fragments into actionable findings."
            )),
            HumanMessage(content=(
                f"Task:\n{query}\n\nKnowledge:\n{findings_text}\n\n"
                f"Output 3-5 key findings with: principle, source, application."
            )),
        ]

    elif cell.role == "critique":
        return [
            SystemMessage(content=(
                f"You are a critique agent for {cell.domain}. "
                f"Strictness: {cell.strictness}. Evaluate quality and correctness."
            )),
            HumanMessage(content=(
                f"Evaluate:\n{content}\n\nIteration: {work.iteration if work else 0}\n"
                f"Neighbors:\n{neighbor_ctx}\n\n"
                f"JSON: {{\"score\": 0.0-1.0, \"verdict\": \"approve\"|\"iterate\", \"feedback\": \"...\"}}"
            )),
        ]

    elif cell.role == "execution":
        focus = {"coder": "Write production code.", "tester": "Write tests.", "runner": "Build and verify."}
        return [
            SystemMessage(content=f"You are the {cell.agent_type} agent. {focus.get(cell.agent_type, 'Execute.')}"),
            HumanMessage(content=(
                f"Spec:\n{content}\n\nIteration: {work.iteration if work else 0}\n"
                f"Neighbors:\n{neighbor_ctx}\n\nProduce the artifact."
            )),
        ]

    elif cell.role == "sub":
        if action == Action.CRITIQUE:
            return [
                SystemMessage(content=(
                    f"You are the {cell.agent_type} specialist in {cell.domain}. "
                    f"Strictness: {cell.strictness}.\n\nDomain knowledge:\n{context}"
                )),
                HumanMessage(content=(
                    f"Review from your {cell.agent_type} perspective:\n{content}\n"
                    f"Neighbors:\n{neighbor_ctx}\n\n"
                    f"JSON: {{\"score\": N, \"verdict\": \"pass\"|\"fail\", \"feedback\": \"...\"}}"
                )),
            ]
        else:
            return [
                SystemMessage(content=(
                    f"You are the {cell.agent_type} specialist in {cell.domain}.\n"
                    f"Domain knowledge:\n{context}"
                )),
                HumanMessage(content=(
                    f"Apply your {cell.agent_type} expertise:\n{content}\n"
                    f"Neighbors:\n{neighbor_ctx}\n\n"
                    f"Contribute specific improvements from your specialist perspective."
                )),
            ]

    return None


def _temp_for_role(role: str) -> float:
    return {"master": 0.4, "research": 0.3, "critique": 0.2, "execution": 0.4}.get(role, 0.5)


def _cell_char_style(cell: AgentCell, domain_colors: dict[str, str] | None = None) -> tuple[str, str]:
    """Get display character and Rich style for a cell."""
    domain_color = (domain_colors or {}).get(cell.domain) or rich_color(cell.domain)

    if cell.state == AgentState.IDLE:
        if cell.has_work:
            count = min(len(cell.inbox), 9)
            return str(count), f"bold {domain_color}"
        return ".", f"dim {domain_color}"
    elif cell.state == AgentState.WORKING:
        return "W", f"bold {domain_color} reverse"
    elif cell.state == AgentState.CRITIQUING:
        return "C", f"bold red"
    elif cell.state == AgentState.WAITING:
        return "~", f"{domain_color}"
    elif cell.state == AgentState.BLOCKED:
        return "X", f"bold red reverse"
    return "?", "dim"


def run_with_tui(
    grid: AgentGrid,
    max_ticks: int = 30,
    quiescence_ticks: int = 3,
    use_true_streaming: bool = True,
    video_path: str | None = None,
    stream_logger=None,
) -> Any:
    """Run the grid with the Rich Live TUI, optionally recording to MP4."""
    from grids.orchestration.tick import run
    from grids.orchestration.invoke import make_invoke_fn
    from grids.orchestration.recorder import VideoRecorder

    tui = GridTUI(grid)
    recorder = None

    if video_path:
        recorder = VideoRecorder(grid, video_path)
        recorder.start()
        # Write title card
        recorder.add_stream_event("ANKOS Grid Run", domain="design")
        recorder.add_stream_event(f"Grid: {grid.width}x{grid.height} ({grid.neighborhood.value})")
        recorder.add_stream_event(f"Cells: {len(grid.cells)}")
        recorder.write_hold(seconds=2.0)

    if use_true_streaming:
        invoke_fn = _make_recorded_streaming_invoke(tui, recorder, stream_logger=stream_logger)
    else:
        base_fn = make_invoke_fn(verbose=False)
        invoke_fn = tui.make_streaming_invoke_fn(base_fn)

    console = Console()

    def on_tick_combined(result):
        tui.on_tick(result)
        if recorder:
            recorder.on_tick(result)
            hold = max(0.5, result.elapsed_seconds * 0.3)
            recorder.write_hold(seconds=hold)
        if stream_logger:
            stream_logger.log("tick",
                              tick=result.tick,
                              actions=result.actions_taken,
                              llm_calls=result.llm_calls,
                              emitted=result.items_emitted,
                              elapsed=round(result.elapsed_seconds, 3),
                              routing_scheduled=result.propagations + result.rejected,
                              routing_delivered=result.propagations,
                              routing_rejected=result.rejected)

    with Live(tui.render(), console=console, refresh_per_second=4, screen=True) as live:
        tui._live = live

        result = run(
            grid,
            invoke_fn,
            max_ticks=max_ticks,
            quiescence_ticks=quiescence_ticks,
            verbose=False,
            on_tick=lambda r: (on_tick_combined(r), live.update(tui.render())),
        )

        tui._live = None

    if recorder:
        # Final frame hold
        recorder.add_stream_event("--- RUN COMPLETE ---")
        recorder.add_stream_event(
            f"Ticks: {result.total_ticks} | LLM: {result.total_llm_calls} | "
            f"Artifacts: {len(result.artifacts)}"
        )
        recorder.write_hold(seconds=3.0)
        recorder.stop()

    return result


def _make_recorded_streaming_invoke(tui: GridTUI, recorder, stream_logger=None):
    """Create an invoke_fn that streams to both TUI and video recorder."""
    from grids.orchestration.agents import get_llm
    from grids.orchestration.invoke import _parse_json_or_text
    from grids.knowledge.store import query_store
    import json

    def invoke_fn(
        cell: AgentCell,
        action: Action,
        work: WorkFragment | None,
        neighbors: list[CellOutput],
    ) -> Any:
        if work is None and action not in (Action.EMIT,):
            return None

        domain = cell.domain
        agent = cell.agent_type
        color = tui.domain_colors.get(domain, "white")
        label = f"{domain}/{agent}"

        messages = _build_messages(cell, action, work, neighbors)
        if messages is None:
            return None

        # Signal start to both TUI and recorder
        tui.stream.start_stream(label, style=color)
        tui.stream.append_token(f"[{action.value}] ")
        if recorder:
            recorder.on_llm_start(cell, action)

        if stream_logger:
            stream_logger.log("llm_start",
                              domain=domain, agent=agent,
                              role=cell.role, action=action.value,
                              pos=f"{cell.position[0]},{cell.position[1]}",
                              work_preview=str(work.content)[:300] if work else None)

        # Stream from LLM
        llm = get_llm(temperature=_temp_for_role(cell.role))
        collected = []
        token_count = 0

        try:
            for chunk in llm.stream(messages):
                token = chunk.content if hasattr(chunk, "content") else str(chunk)
                if token:
                    collected.append(token)
                    token_count += 1
                    tui.stream.append_token(token)

                    # Update TUI
                    if tui._live and token_count % 3 == 0:
                        tui._live.update(tui.render())

                    # Write video frame every ~20 tokens
                    if recorder and token_count % 20 == 0:
                        recorder.add_stream_event(
                            f"  {label}: {''.join(collected[-40:])[:80]}",
                            domain=domain,
                        )
                        recorder.write_frame()
        except Exception as e:
            tui.stream.append_token(f" [ERROR: {e}]")
            tui.stream.end_stream()
            return None

        tui.stream.end_stream()
        if tui._live:
            tui._live.update(tui.render())

        full_text = "".join(collected)

        # Record completion
        if recorder:
            preview = full_text[:120].replace("\n", " ")
            recorder.on_llm_end(cell, preview)

        if stream_logger:
            stream_logger.log("llm_end",
                              domain=domain, agent=agent,
                              action=action.value,
                              token_count=token_count,
                              response=full_text[:2000])

        return _parse_json_or_text(full_text)

    return invoke_fn
