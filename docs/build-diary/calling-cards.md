# Build Diary: grids-calling-cards

Tracking the construction of the first fully ANKOS-native application.
Each entry documents what the domain agents contributed vs what was manual,
and captures friction points for later architectural analysis.

---

## Entry 0: Project Scaffolding

**Date**: 2026-02-07
**Stage**: ANKOS formalization + build diary setup
**Duration**: ~15min

### What happened
- Created `ANKOS.md` formalizing the framework name and contract.
- Created this build diary.
- Verified system dependencies: LuaLaTeX present, Rust/Cargo present, Node present.
- Missing CLI tools (graphicsmagick, potrace, inkscape, pdf2svg) to be installed at step 3.

### Agent contribution
None yet -- this is pure infrastructure scaffolding.

### Manual / human-directed
Everything. Framework naming, spec approval, dependency audit.

### Comparison to vanilla CLI
A vanilla CLI (e.g. Claude Code with no ANKOS) would skip this step entirely and jump
straight to generating code. The ANKOS formalization forces us to define the contract
*before* building, which should pay off when wiring agents as the execution backend.

### Friction points
None yet.

---

## Entry 1: Intake TUI + LaTeX Pipeline + Image Tools + Print PDF + Studio App

**Date**: 2026-02-07
**Stage**: Full implementation pass (spec build order steps 1-6)
**Duration**: ~45min of AI implementation time

### What was built

**Intake TUI** (`tui/intake/`, 4 Rust files, ~700 lines):
- 9-step wizard: name/type, physical specs, color system, typography, brief, domains, references, output, review
- Scaffolds project directory with `project.yaml`, `brief.md`, card subdirectories
- Compiles clean with zero warnings

**LaTeX Typesetting** (`scripts/src/grids/typeset/`, 3 Python files, ~400 lines):
- `engine.py`: Card face TeX generation with TikZ + fontspec + microtype + CMYK xcolor
- `cli.py`: `grids-typeset` and `grids-typeset-gen` commands
- Verified: LuaLaTeX compilation produces PDF from generated TeX (30KB PDF)

**Image Toolchain** (`scripts/src/grids/image/`, 3 Python files, ~350 lines):
- `gm.py`: GraphicsMagick wrapper (CMYK convert, threshold, crop, resize, levels, identify)
- `trace.py`: Potrace wrapper with auto-convert for JPEG/PNG inputs
- `svg_tools.py`: Inkscape CLI wrapper (text-to-path, simplify, export PDF/PNG, info)

**Print PDF Imposer** (`scripts/src/grids/typeset/impose.py`, ~250 lines):
- ReportLab-based imposition calculator: 2x5 = 10 cards per 11x17" sheet
- 2-page PDF: fronts (page 1), mirrored backs (page 2) for duplex
- Crop marks at each card boundary, CMYK color space
- Verified: creates valid PDF with correct imposition math

**Tauri Studio App** (`apps/calling-cards/`, 8 files):
- 3-panel layout matching wireframe: Chat/CLI, Artboard Canvas, Accordion Sidebar
- Dark theme CSS with monospace UI
- Ink Manager with CMYK sliders
- SVG canvas with card template (bleed, trim, safe zone guides)
- Rust backend with Tauri commands (load_project, run_cli, save_svg, list_card_files)
- Not yet compilable (Tauri crate needs `cargo install tauri-cli`)

**ANKOS Wiring**:
- Updated `calling_cards.py` to accept `--project` flag for project.yaml
- Updated `capture.py` to support LaTeX artifacts (compile -> capture)
- Updated `runner.py` to include LaTeX in visual critique loop
- 6 new CLI commands registered in pyproject.toml

### Agent contribution
None in this pass. This was pure infrastructure implementation -- building the tools
that the domain agents will route through. The design master, typography sub-agent,
and other domain agents haven't been invoked yet. That happens in step 7 (end-to-end run).

### What was manual / human-directed
Everything. The spec was human-approved, and all code was written by AI-as-tool
(Claude Code / Droid), not by the ANKOS domain agent system.

### Comparison to vanilla CLI
A vanilla CLI would have produced essentially the same output for this step --
infrastructure code doesn't benefit from domain expertise. The difference comes
when we actually *run* the pipeline and the design master generates work orders
that the typography, composition, and visual-systems agents validate.

This entry is the baseline: "here's what pure tool-assisted coding looks like."
The next entry should show the delta when ANKOS agents are actually in the loop.

### Friction points
- LuaLaTeX's `-output-directory` flag doesn't work well with absolute paths;
  had to use cwd instead.
- Tauri app can't compile without `cargo install tauri-cli` (external dep).
- System tools (gm, potrace, inkscape, pdf2svg) not yet installed -- will need
  `brew install graphicsmagick potrace inkscape pdf2svg` before full pipeline runs.

---

## Entry 2: IDML Export + OpenCode In-App CLI Integration

**Date**: 2026-02-07
**Stage**: Export format expansion + agent interface architecture
**Duration**: ~20min

### What was built

**IDML Export** (`scripts/src/grids/typeset/idml.py`, ~300 lines):
- Full InDesign Markup Language generator: ZIP archive of XML files
- Maps CardSpec dimensions (with bleed), CMYK swatches, font declarations,
  paragraph/character styles, positioned text frames per card face
- `grids-idml` CLI command from project.yaml
- Wired into calling_cards.py pipeline as Phase 8
- Intake TUI default output formats now include "idml"
- Verified: generates valid ZIP with correct IDML structure (designmap.xml,
  Stories/, Spreads/, Resources/)

**OpenCode In-App CLI** (architecture + wiring):
- Replaced stub "agent not connected" in Tauri chat panel with OpenCode
  server integration (`opencode serve` at localhost:4096)
- Session management: auto-create on connect, abort support
- Natural language input -> OpenCode HTTP API -> LLM -> tool use -> grids-* CLIs
- SSE streaming for real-time agent responses in the chat panel
- Added to ANKOS contract as optional capability
- Documented in ARCHITECTURE.md with routing diagram

### Agent contribution
None -- still infrastructure. The domain agents haven't been invoked yet.

### What was manual / human-directed
User specified IDML as a feature requirement and OpenCode as the in-app CLI
framework. Implementation was AI-as-tool (Droid).

### Comparison to vanilla CLI
IDML export is pure infrastructure -- no agent benefit. The OpenCode integration
is architecturally significant: it means every GRIDS app gets a full AI coding
agent for free, with the user's choice of LLM provider, rather than us building
bespoke chat infrastructure. This is the kind of "leverage" decision that ANKOS
should eventually make on its own (meta-agent suggesting tool adoption).

### Friction points
- Python 3.14 is too new for ChromaDB/LangChain (Pydantic v1 breaks). Had to
  install python@3.13 via brew and recreate the venv.
- IDML spec is large; our generator covers the minimum viable subset (stories,
  spreads, styles, fonts, graphic swatches). InDesign may want additional XML
  files (Preferences.xml, BackingStory) for a perfectly clean open.

---
