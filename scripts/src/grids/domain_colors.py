"""Shared domain color registry.

Single source of truth for domain -> color mappings across:
- TUI (Rich markup names)
- Video recorder (RGB tuples)
- D3 viz builder (hex strings)

Unknown domains get auto-assigned from an overflow palette so new domains
work in the UI without any manual registration.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from grids.orchestration.grid import AgentGrid


# --- Canonical color definitions (one entry per known domain) ---
# Format: (rich_style, rgb_tuple, hex_string)

_REGISTRY: dict[str, tuple[str, tuple[int, int, int], str]] = {
    "design":              ("cyan",            (91, 163, 217),  "#4a9eff"),
    "production-tech":     ("green",           (91, 217, 128),  "#5bd980"),
    "editorial":           ("yellow",          (217, 184, 91),  "#d9b85b"),
    "creative-production": ("magenta",         (217, 91, 217),  "#d95bd9"),
    "culture-crit":        ("red",             (217, 91, 91),   "#8b5cf6"),
    "execution":           ("bright_white",    (200, 200, 200), "#22d3ee"),
    "general":             ("dim",             (100, 100, 100), "#888888"),
    "agency-mix":          ("bright_blue",     (255, 215, 0),   "#ffd700"),
    "dating-advice":       ("bright_magenta",  (255, 121, 198), "#ff79c6"),
    "gen-z-trend-expert":  ("bright_green",    (80, 250, 123),  "#50fa7b"),
    "ocd-ux-nerd":         ("bright_cyan",     (139, 233, 253), "#8be9fd"),
    "trend-reporter":      ("bright_yellow",   (241, 250, 140), "#f1fa8c"),
    "dataviz":             ("blue",            (74, 158, 255),  "#4a9eff"),
    "dieter-rams":         ("red",             (255, 121, 198), "#ff79c6"),
    "product":             ("medium_purple3",  (192, 132, 252), "#c084fc"),
    "coordination":        ("bright_cyan",     (103, 232, 249), "#67e8f9"),
    "code-quality":        ("green",           (74, 222, 128),  "#4ade80"),
    "ux-review":           ("yellow",          (251, 191, 36),  "#fbbf24"),
}

# Overflow palettes for auto-assignment (one per format)
_OVERFLOW_RICH = [
    "bright_red", "dodger_blue2", "orange3", "orchid", "spring_green3",
    "deep_sky_blue1", "gold3", "medium_purple3", "salmon1", "turquoise2",
]
_OVERFLOW_RGB = [
    (255, 85, 85), (30, 144, 255), (205, 133, 0), (218, 112, 214), (0, 205, 102),
    (0, 191, 255), (205, 173, 0), (147, 112, 219), (250, 128, 114), (64, 224, 208),
]
_OVERFLOW_HEX = [
    "#ff5555", "#1e90ff", "#cd8500", "#da70d6", "#00cd66",
    "#00bfff", "#cdad00", "#9370db", "#fa8072", "#40e0d0",
]

_overflow_idx = 0


def _auto_assign(domain: str) -> tuple[str, tuple[int, int, int], str]:
    """Auto-assign colors to an unknown domain (stable per-process)."""
    global _overflow_idx
    idx = _overflow_idx % len(_OVERFLOW_RICH)
    _overflow_idx += 1
    entry = (_OVERFLOW_RICH[idx], _OVERFLOW_RGB[idx], _OVERFLOW_HEX[idx])
    _REGISTRY[domain] = entry
    return entry


def _ensure(domain: str) -> tuple[str, tuple[int, int, int], str]:
    if domain in _REGISTRY:
        return _REGISTRY[domain]
    return _auto_assign(domain)


# --- Public accessors ---

def rich_color(domain: str) -> str:
    """Rich markup color name for TUI rendering."""
    return _ensure(domain)[0]


def rgb(domain: str) -> tuple[int, int, int]:
    """RGB tuple for Pillow/video rendering."""
    return _ensure(domain)[1]


def hex_color(domain: str) -> str:
    """Hex color string for D3/HTML rendering."""
    return _ensure(domain)[2]


def rich_colors_for_grid(grid: "AgentGrid") -> dict[str, str]:
    """Build a complete domain->rich_color map for every domain in a grid."""
    colors: dict[str, str] = {}
    for cell in grid.all_cells():
        colors[cell.domain] = rich_color(cell.domain)
    return colors


def rgb_colors_for_grid(grid: "AgentGrid") -> dict[str, tuple[int, int, int]]:
    """Build a complete domain->RGB map for every domain in a grid."""
    colors: dict[str, tuple[int, int, int]] = {}
    for cell in grid.all_cells():
        colors[cell.domain] = rgb(cell.domain)
    return colors


def hex_colors_for_grid(grid: "AgentGrid") -> dict[str, str]:
    """Build a complete domain->hex map for every domain in a grid."""
    colors: dict[str, str] = {}
    for cell in grid.all_cells():
        colors[cell.domain] = hex_color(cell.domain)
    return colors
