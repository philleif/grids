"""Video recorder -- renders the CA grid run to .mp4 in parallel with the TUI.

Each tick (and sub-tick LLM token events) generates a frame.
Frames are piped to ffmpeg as raw RGB, producing an MP4 artifact.

Layout matches the TUI:
  Left:  CA grid (colored cells)
  Right: Agent stream (scrolling LLM output)
  Bottom: Metrics bar
"""

from __future__ import annotations

import io
import os
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from grids.orchestration.grid import AgentGrid, AgentCell
from grids.orchestration.rules import AgentState, Action
from grids.orchestration.tick import TickResult
from grids.domain_colors import rgb as domain_rgb, rgb_colors_for_grid


# --- Config ---

WIDTH = 1920
HEIGHT = 1080
FPS = 10
GRID_PANEL_WIDTH = 560
METRICS_HEIGHT = 100
BG_COLOR = (26, 26, 26)       # --bg: #1a1a1a
PANEL_BG = (34, 34, 34)       # --bg-panel: #222
BORDER_COLOR = (58, 58, 58)   # --border: #3a3a3a
TEXT_COLOR = (224, 224, 224)   # --text: #e0e0e0
DIM_COLOR = (136, 136, 136)   # --text-dim: #888
ACCENT_COLOR = (91, 163, 217) # --accent: #5ba3d9

STATE_COLORS_RGB = {
    AgentState.IDLE:        None,  # use domain color dimmed
    AgentState.WORKING:     (255, 255, 255),  # bright
    AgentState.CRITIQUING:  (217, 91, 91),
    AgentState.WAITING:     (136, 136, 136),
    AgentState.BLOCKED:     (200, 50, 50),
}

CELL_SIZE = 40
CELL_PAD = 4

FONT_NAME = "Menlo"
FONT_SIZE_GRID = 18
FONT_SIZE_STREAM = 13
FONT_SIZE_METRICS = 14
FONT_SIZE_TITLE = 16


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT_NAME, size)
    except Exception:
        return ImageFont.load_default()


@dataclass
class StreamEvent:
    """A line of text in the agent stream panel."""
    text: str
    color: tuple[int, int, int] = TEXT_COLOR
    timestamp: float = field(default_factory=time.time)


class VideoRecorder:
    """Records grid state + agent stream to MP4 via ffmpeg pipe."""

    def __init__(
        self,
        grid: AgentGrid,
        output_path: str,
        width: int = WIDTH,
        height: int = HEIGHT,
        fps: int = FPS,
    ):
        self.grid = grid
        self.output_path = output_path
        self.width = width
        self.height = height
        self.fps = fps

        self.stream_lines: deque[StreamEvent] = deque(maxlen=200)
        self.tick_count = 0
        self.total_llm_calls = 0
        self.start_time = time.time()
        self.frame_count = 0

        # Fonts
        self.font_grid = _load_font(FONT_SIZE_GRID)
        self.font_stream = _load_font(FONT_SIZE_STREAM)
        self.font_metrics = _load_font(FONT_SIZE_METRICS)
        self.font_title = _load_font(FONT_SIZE_TITLE)

        # ffmpeg process
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def start(self):
        """Start the ffmpeg process."""
        os.makedirs(os.path.dirname(self.output_path) or ".", exist_ok=True)
        self._proc = subprocess.Popen(
            [
                "ffmpeg", "-y",
                "-f", "rawvideo",
                "-vcodec", "rawvideo",
                "-s", f"{self.width}x{self.height}",
                "-pix_fmt", "rgb24",
                "-r", str(self.fps),
                "-i", "-",
                "-an",
                "-vcodec", "libx264",
                "-preset", "fast",
                "-crf", "23",
                "-pix_fmt", "yuv420p",
                self.output_path,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop(self):
        """Finalize and close the video."""
        if self._proc and self._proc.stdin:
            try:
                self._proc.stdin.close()
            except BrokenPipeError:
                pass
            self._proc.wait(timeout=30)
            self._proc = None

    def add_stream_event(self, text: str, domain: str = ""):
        """Add a line to the agent stream."""
        color = domain_rgb(domain) if domain else TEXT_COLOR
        self.stream_lines.append(StreamEvent(text=text, color=color))

    def on_tick(self, result: TickResult):
        """Called after each tick. Renders a frame."""
        self.tick_count = result.tick
        self.total_llm_calls += result.llm_calls

        # Add tick summary to stream
        self.add_stream_event(
            f"--- tick {result.tick} | actions={result.actions_taken} "
            f"emitted={result.items_emitted} ({result.elapsed_seconds:.1f}s) ---"
        )
        for a in result.cell_actions:
            if a.get("skipped"):
                continue
            domain = a.get("domain", "")
            agent = a.get("agent", "?")
            action = a.get("action", "?")
            self.add_stream_event(f"  {domain}/{agent}: {action}", domain=domain)

        self.write_frame()

    def on_llm_token(self, cell: AgentCell, token: str):
        """Called for each LLM token. Appends to current stream line and writes frame."""
        # We batch tokens -- write a frame every ~10 tokens to keep video reasonable
        pass

    def on_llm_start(self, cell: AgentCell, action: Action):
        """Called when an LLM call starts."""
        self.add_stream_event(
            f"[{cell.domain}/{cell.agent_type}] {action.value} ...",
            domain=cell.domain,
        )
        self.write_frame()

    def on_llm_end(self, cell: AgentCell, result_preview: str):
        """Called when an LLM call completes."""
        preview = result_preview[:120].replace("\n", " ")
        self.add_stream_event(
            f"  -> {preview}",
            domain=cell.domain,
        )
        self.write_frame()

    def write_frame(self, hold_frames: int = 1):
        """Render current state to a frame and write to ffmpeg."""
        if self._proc is None or self._proc.stdin is None:
            return

        img = self._render_frame()
        raw = img.tobytes()

        with self._lock:
            try:
                for _ in range(hold_frames):
                    self._proc.stdin.write(raw)
                    self.frame_count += 1
            except BrokenPipeError:
                pass

    def write_hold(self, seconds: float = 1.0):
        """Hold the current frame for N seconds."""
        frames = max(1, int(seconds * self.fps))
        self.write_frame(hold_frames=frames)

    def _render_frame(self) -> Image.Image:
        """Render the full frame."""
        img = Image.new("RGB", (self.width, self.height), BG_COLOR)
        draw = ImageDraw.Draw(img)

        # Grid panel (left)
        self._draw_grid_panel(draw, 0, 0, GRID_PANEL_WIDTH, self.height - METRICS_HEIGHT)

        # Stream panel (right)
        stream_x = GRID_PANEL_WIDTH + 2
        stream_w = self.width - stream_x
        self._draw_stream_panel(draw, stream_x, 0, stream_w, self.height - METRICS_HEIGHT)

        # Metrics bar (bottom)
        self._draw_metrics_bar(draw, 0, self.height - METRICS_HEIGHT, self.width, METRICS_HEIGHT)

        return img

    def _draw_grid_panel(self, draw: ImageDraw.Draw, x0: int, y0: int, w: int, h: int):
        """Draw the CA grid."""
        # Panel background + border
        draw.rectangle([x0, y0, x0 + w, y0 + h], fill=PANEL_BG, outline=BORDER_COLOR)

        # Title
        draw.text(
            (x0 + 10, y0 + 8),
            f"GRID {self.grid.width}x{self.grid.height}",
            fill=ACCENT_COLOR, font=self.font_title,
        )

        # Grid cells
        grid_y_start = y0 + 36
        for (cx, cy), cell in sorted(self.grid.cells.items()):
            px = x0 + 20 + cx * (CELL_SIZE + CELL_PAD)
            py = grid_y_start + cy * (CELL_SIZE + CELL_PAD)

            # Cell background
            domain_color = domain_rgb(cell.domain)
            if cell.state == AgentState.WORKING:
                bg = domain_color
            elif cell.state == AgentState.CRITIQUING:
                bg = (180, 60, 60)
            elif cell.state == AgentState.IDLE and cell.has_work:
                bg = tuple(max(30, c // 2) for c in domain_color)
            elif cell.state == AgentState.BLOCKED:
                bg = (150, 30, 30)
            else:
                bg = tuple(max(20, c // 4) for c in domain_color)

            draw.rectangle([px, py, px + CELL_SIZE, py + CELL_SIZE], fill=bg, outline=BORDER_COLOR)

            # State character
            ch = _state_char(cell)
            text_color = (255, 255, 255) if cell.state in (AgentState.WORKING, AgentState.CRITIQUING) else domain_color
            bbox = self.font_grid.getbbox(ch)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.text(
                (px + (CELL_SIZE - tw) // 2, py + (CELL_SIZE - th) // 2 - 2),
                ch, fill=text_color, font=self.font_grid,
            )

        # Legend (below grid)
        legend_y = grid_y_start + self.grid.height * (CELL_SIZE + CELL_PAD) + 10
        lx = x0 + 10
        for domain, color in rgb_colors_for_grid(self.grid).items():
            cells = self.grid.cells_by_domain(domain)
            if not cells:
                continue
            draw.rectangle([lx, legend_y, lx + 10, legend_y + 10], fill=color)
            label = f"{domain}({len(cells)})"
            draw.text((lx + 14, legend_y - 2), label, fill=DIM_COLOR, font=self.font_stream)
            bbox = self.font_stream.getbbox(label)
            lx += (bbox[2] - bbox[0]) + 24
            if lx > x0 + w - 40:
                lx = x0 + 10
                legend_y += 18

    def _draw_stream_panel(self, draw: ImageDraw.Draw, x0: int, y0: int, w: int, h: int):
        """Draw the agent stream panel."""
        draw.rectangle([x0, y0, x0 + w, y0 + h], fill=PANEL_BG, outline=BORDER_COLOR)

        draw.text(
            (x0 + 10, y0 + 8),
            "AGENT STREAM",
            fill=(217, 184, 91), font=self.font_title,
        )

        # Stream lines (bottom-aligned, scrolling up)
        line_height = 17
        max_visible = (h - 40) // line_height
        visible = list(self.stream_lines)[-max_visible:]

        ty = y0 + 36
        for event in visible:
            text = event.text[:100]
            draw.text((x0 + 10, ty), text, fill=event.color, font=self.font_stream)
            ty += line_height

    def _draw_metrics_bar(self, draw: ImageDraw.Draw, x0: int, y0: int, w: int, h: int):
        """Draw the bottom metrics bar."""
        draw.rectangle([x0, y0, x0 + w, y0 + h], fill=(30, 30, 30), outline=BORDER_COLOR)

        elapsed = time.time() - self.start_time

        # Left: tick + LLM count
        draw.text(
            (x0 + 12, y0 + 10),
            f"TICK {self.tick_count}",
            fill=ACCENT_COLOR, font=self.font_title,
        )
        draw.text(
            (x0 + 12, y0 + 34),
            f"LLM calls: {self.total_llm_calls}  |  Elapsed: {elapsed:.0f}s  |  Frames: {self.frame_count}",
            fill=DIM_COLOR, font=self.font_metrics,
        )

        # Center: state counts
        cx = x0 + 400
        for state in AgentState:
            count = len(self.grid.cells_by_state(state))
            if count == 0:
                continue
            color = STATE_COLORS_RGB.get(state) or DIM_COLOR
            label = f"{state.value}: {count}"
            draw.text((cx, y0 + 10), label, fill=color, font=self.font_metrics)
            bbox = self.font_metrics.getbbox(label)
            cx += (bbox[2] - bbox[0]) + 20

        # Right: quiescence indicator
        if self.grid.is_quiescent():
            draw.text(
                (x0 + w - 160, y0 + 10),
                "QUIESCENT",
                fill=(91, 217, 128), font=self.font_title,
            )

        # ANKOS branding
        draw.text(
            (x0 + w - 100, y0 + h - 22),
            "ANKOS/GRIDS",
            fill=(60, 60, 60), font=self.font_metrics,
        )


def _state_char(cell: AgentCell) -> str:
    if cell.state == AgentState.IDLE:
        if cell.has_work:
            return str(min(len(cell.inbox), 9))
        return "."
    return {
        AgentState.WORKING: "W",
        AgentState.CRITIQUING: "C",
        AgentState.WAITING: "~",
        AgentState.BLOCKED: "X",
    }.get(cell.state, "?")
