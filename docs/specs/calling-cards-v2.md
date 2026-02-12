# Calling Cards Studio v2: Emergent MVP Spec

_Generated from current app state + 7 domain agent perspectives._
_Each section attributes which domain(s) contributed the feature._

---

## Current State Captured

The v1 app is a Tauri desktop application with:
- **3-panel layout**: CLI chat (left), SVG artboard (center), accordion sidebar (right)
- **Chat-driven design**: user types natural language, LLM returns SVG loaded to artboard
- **RAG-augmented**: chat server queries 28 ChromaDB collections (3,689 chunks from 31 reference texts)
- **Artboard**: fixed 3.07" x 2.61" card with bleed/trim/safe guides, front only
- **Sidebar controls**: grid cols/rows, margins, CMYK sliders, reference library with Figma MCP
- **Export pipeline**: /typeset (LaTeX), /impose (print PDF), /idml (InDesign), /export (SVG)
- **Backend pipeline**: 10-phase Python pipeline (research, moodboard, sharpen, specify, execute, multi-domain validate, memory, reflections, IDML, decisions)

### What Works
- Chat generates SVG and loads it to artboard
- CMYK sliders exist but don't affect the design
- Reference library supports drag-drop, Figma links, file upload
- LaTeX typesetting compiles and produces PDFs
- Print imposition calculates 2x5 layout on 11x17 stock
- IDML export generates valid InDesign files
- Multi-domain validation runs primary + secondary domain checks

### What's Missing or Stub
- No front/back toggle -- artboard is front-only
- CMYK sliders are cosmetic (no binding to SVG)
- No card variant management (Working Documents says "No card variants yet")
- No preview of imposed sheet
- Grid controls don't affect the artboard
- No font selection or type specimen preview
- No direct editing of SVG elements (click-to-select)
- No undo/redo
- No dark/light theme toggle
- No keyboard shortcuts

---

## V2 Spec: Emergent Features by Domain

### 1. Document Pane (production-tech + design)

**Replace the static "Controls" accordion with a full Document pane.**

The production-tech domain identifies that the current sidebar treats the card as a static rectangle. A real production tool needs to surface the full fabrication chain.

#### 1.1 Card Face Switcher
- **Front / Back toggle** at the top of the artboard panel header
- Each face maintains its own SVG state
- Visual indicator showing which face is active
- Mirror-preview mode: show front + back side-by-side at reduced scale

#### 1.2 Stock & Imposition Preview
- Mini diagram showing card placement on 11x17 stock (2x5 grid)
- Click a position to set the current card's slot
- Shows front sheet (page 1) and back sheet (page 2) with flip axis indicator
- Crop mark preview overlay
- _Source: production-tech (delivery-specs, adobe-design-print), design (print-design)_

#### 1.3 Paper Stock Selector
- Dropdown: Mohawk Superfine, French Paper, Crane Lettra, Neenah Classic, Custom
- Each stock shows weight options (80lb cover, 100lb cover, 110lb cover)
- Texture indicator (smooth, vellum, felt, laid)
- When stock is selected, artboard background subtly tints to suggest texture
- _Source: production-tech (packaging-environmental), design (print-design)_

#### 1.4 Print Method Selector
- Options: Offset lithography, Letterpress, Risograph, Digital (laser/inkjet), Screen print
- Each method constrains available features:
  - **Letterpress**: max 2 spot colors, no gradients, no photos, deboss/impression depth slider
  - **Risograph**: limited soy-based ink palette (fluorescent pink, blue, green, etc.), halftone screen angle, registration tolerance warning
  - **Screen print**: spot color only, minimum line weight warnings, mesh count selector
  - **Offset**: full CMYK, spot + process, overprint preview
  - **Digital**: RGB-safe palette, bleed warnings for toner coverage
- _Source: production-tech (adobe-design-print, fabrication-manufacturing), creative-production (craft-quality), culture-crit (zine-craft)_

#### 1.5 Finishing Options
- Checkboxes: die cut, rounded corners (radius slider), foil stamp (gold/silver/copper), emboss/deboss, edge painting, spot UV
- Each finishing option shows a cost-complexity indicator ($ to $$$$)
- When enabled, the artboard shows a visual overlay indicating the finishing area
- _Source: production-tech (packaging-environmental, fabrication-manufacturing)_

### 2. Color System (design + production-tech + culture-crit)

**Replace the bare CMYK sliders with a proper color management panel.**

#### 2.1 Color Mode Selector
- Radio buttons: 1-color, 2-color, 3-color, Full process CMYK
- Mode constrains the palette (1-color = single ink + paper white, etc.)
- _Source: design (color), production-tech (delivery-specs)_

#### 2.2 Ink Definitions
- Each ink slot shows: CMYK recipe, Pantone match (nearest), swatch preview, ink name
- Pantone lookup: type a Pantone code (e.g., "PMS 485") and get the CMYK equivalent
- For spot colors: flag that separations are needed for the print shop
- Ink coverage estimator: shows approximate % coverage per ink on the current face
- _Source: design (color), production-tech (adobe-design-print, delivery-specs)_

#### 2.3 Overprint Preview
- Toggle to simulate overprint behavior (how inks interact when layered)
- Essential for 2-color work where you want a third color from overprint
- Show knockout vs overprint per element
- _Source: production-tech (adobe-design-print), design (print-design)_

#### 2.4 Paper Color
- Not all stock is white -- selector for cream, natural, grey, black, colored stocks
- SVG rendering adjusts background to show how inks appear on the selected stock
- _Source: production-tech (packaging-environmental), culture-crit (zine-craft)_

### 3. Typography Panel (design + editorial)

**New accordion section for type management.**

#### 3.1 Font Selector
- Two slots: Primary (headings, name) and Secondary (body, details)
- Font preview showing sample text at the card's actual size
- Categories: Serif, Sans-serif, Monospace, Display, Script
- System fonts + any fonts in the project's `fonts/` directory
- _Source: design (typography), editorial (chicago-style)_

#### 3.2 Type Specimen
- Live preview strip showing the selected font at sizes used on the card
- Shows: name (14pt), title (10pt), organization (9pt), contact (7pt)
- Vertical rhythm indicator (baseline grid alignment)
- _Source: design (typography)_

#### 3.3 OpenType Feature Toggles
- Checkboxes for: small caps, old-style figures, ligatures, swashes, tabular figures
- Only enabled when the selected font supports the feature
- _Source: design (typography, design-tech)_

#### 3.4 Hierarchy Preview
- Mini diagram showing the typographic hierarchy: which elements are primary, secondary, tertiary
- Drag to reorder hierarchy levels
- _Source: editorial (short-form, longform), design (typography)_

### 4. Reference & Moodboard (agency-mix + culture-crit + creative-production)

**Upgrade the reference library into a real moodboard system.**

#### 4.1 Moodboard Canvas
- Drag references from the grid into a freeform moodboard canvas
- Auto-zone: references cluster by similarity (typography refs, color refs, layout refs)
- Resizable thumbnails
- _Source: creative-production (creative-process), agency-mix (creative-director)_

#### 4.2 Style DNA Panel
- Shows the "aesthetic DNA" of the current moodboard:
  - Era indicators (e.g., "1960s Swiss", "1890s letterpress", "2020s neo-brutalist")
  - Reference lineage: "Mueller-Brockmann grid → Emigre type experimentation → current"
  - Cultural context notes
- _Source: culture-crit (cultural-deconstruction, trend-forensics, scene-history)_

#### 4.3 Brief Sidebar
- Always-visible brief text (from project.yaml or user-entered)
- Editable inline
- "Does this solve the brief?" checklist auto-generated from acceptance criteria
- _Source: agency-mix (strategist, creative-director), editorial (idea-generation)_

### 5. Working Documents (creative-production + design)

**Card variant management -- the core creative workflow.**

#### 5.1 Variant Tree
- Tree view showing all card variants: `v1-front`, `v1-back`, `v2-front`, etc.
- Branch from any variant to create a new exploration
- Star/flag favorites
- _Source: creative-production (creative-process, software-production)_

#### 5.2 Comparison View
- Select 2-4 variants and view side-by-side
- Diff overlay: highlight what changed between variants
- _Source: creative-production (creative-process), design (graphic-design)_

#### 5.3 Iteration Log
- Each variant stores: prompt that generated it, domain validation scores, feedback
- Timeline view of the design evolution
- _Source: creative-production (software-production), agency-mix (creative-director)_

### 6. Artboard Enhancements (design + production-tech + dataviz)

#### 6.1 Smart Guides
- Snap-to-grid when chat-generated elements are close to grid lines
- Alignment guides when elements share edges or centers
- Golden ratio / rule-of-thirds overlay toggle
- _Source: design (graphic-design, typography)_

#### 6.2 Measurement Tool
- Hover between elements to see distances in pt, mm, and inches
- Click an element to see its dimensions and position
- _Source: design (design-tech), production-tech (fabrication-manufacturing)_

#### 6.3 Accessibility Checker
- Minimum text size warning (< 6pt for print)
- Contrast checker for text on background colors
- Touch target size validator (for QR codes / URLs that lead to digital)
- _Source: design (interactive-design), production-tech (web-frontend)_

#### 6.4 Data Fields / Variable Data
- Mark text elements as "variable" (name, title, email, phone, etc.)
- Import a CSV to generate N personalized cards from one template
- Preview individual records
- _Source: dataviz (typography-labels), production-tech (adobe-design-print -- InDesign data merge)_

### 7. Export & Production (production-tech + design)

#### 7.1 Export Presets
- One-click export profiles:
  - **Print shop**: PDF/X-4 with bleed, crop marks, color bars, slug info
  - **Digital proof**: RGB PDF for screen review
  - **Web preview**: PNG at 150dpi
  - **Press-ready**: imposed PDF + separated spot colors
  - **Source files**: SVG + LaTeX + IDML bundle
- _Source: production-tech (delivery-specs, adobe-design-print, documentation-handoff)_

#### 7.2 Preflight Checklist
- Before export, run validation:
  - Bleed extends to edge? 
  - Text inside safe zone? 
  - Minimum line weight met? (0.25pt for offset, 0.5pt for digital)
  - Image resolution sufficient? (300dpi for print)
  - Fonts outlined / embedded?
  - Color space correct for method?
- Show pass/fail with fix suggestions
- _Source: production-tech (adobe-design-print, delivery-specs), design (print-design, design-tech)_

#### 7.3 Print Shop Package
- Bundle for handoff: PDF + IDML + fonts folder + readme with specs
- Auto-generated spec sheet: paper stock, print method, ink colors, finishing, quantity, trim size
- _Source: production-tech (documentation-handoff, asset-management)_

### 8. AI Chat Enhancements (editorial + agency-mix)

#### 8.1 Structured Commands
- `/critique` -- trigger multi-domain validation on current artboard SVG
- `/variants N` -- generate N variations of the current design
- `/refine "feedback"` -- iterate on current design with specific feedback
- `/brief` -- show/edit the project brief
- `/history` -- show design evolution timeline
- `/specs` -- show current production specs (paper, ink, method, finishing)
- _Source: editorial (short-form), agency-mix (strategist), creative-production (creative-process)_

#### 8.2 Domain-Aware Responses
- When the chat returns design suggestions, tag which domain agent's knowledge was cited
- E.g., "[typography: Bringhurst p.32] Set the name in small caps at 11pt with +30 tracking"
- _Source: all domains via RAG server_

#### 8.3 Copy Editor Mode
- For card text: auto-check spelling, punctuation, proper title case
- Style guide enforcement (Chicago Manual for formal, AP for journalistic)
- _Source: editorial (chicago-style, short-form, research-rigor)_

### 9. Keyboard Shortcuts & UX (creative-production)

| Shortcut | Action |
|----------|--------|
| `Cmd+N` | New card variant |
| `Cmd+S` | Save current state |
| `Cmd+E` | Export (opens preset selector) |
| `Cmd+Z` / `Cmd+Shift+Z` | Undo / Redo |
| `Cmd+/` | Focus CLI input |
| `Tab` | Cycle panels (CLI → Artboard → Sidebar) |
| `Cmd+1` / `Cmd+2` | Switch front / back |
| `Space` | Toggle grid overlay |
| `Cmd+P` | Preview imposed sheet |

_Source: creative-production (software-production), design (interactive-design)_

---

## Implementation Priority (WSJF-ranked)

| Phase | Features | Cost of Delay | Job Size | WSJF |
|-------|----------|---------------|----------|------|
| P1 | Front/back toggle, font selector, color mode, print method | 5.0 | 2.0 | 2.5 |
| P2 | Variant tree, comparison view, iteration log | 4.0 | 2.0 | 2.0 |
| P3 | Preflight checklist, export presets, print shop package | 4.5 | 3.0 | 1.5 |
| P4 | Paper stock, finishing options, overprint preview | 3.0 | 2.0 | 1.5 |
| P5 | Moodboard canvas, style DNA, brief sidebar | 3.0 | 2.5 | 1.2 |
| P6 | Smart guides, measurement tool, keyboard shortcuts | 2.0 | 2.0 | 1.0 |
| P7 | Variable data, accessibility checker, copy editor mode | 2.0 | 3.0 | 0.67 |

---

## Domain Attribution Summary

| Domain | Collections | Chunks | Features Contributed |
|--------|------------|--------|---------------------|
| design | 4 | 527 | Color system, typography panel, smart guides, artboard enhancements |
| production-tech | 4 | 310 | Document pane, print method, finishing, stock, preflight, export |
| editorial | 3 | 200 | Copy editor mode, hierarchy preview, brief management |
| agency-mix | 2 | 147 | Moodboard, brief checklist, strategic commands |
| culture-crit | 7 | 457 | Style DNA, zine-craft print methods, paper color awareness |
| creative-production | 2 | 308 | Variant tree, comparison view, keyboard shortcuts, workflow |
| dataviz | 2 | 195 | Variable data / data merge, label clarity |

---

## Architecture Notes

- All new UI panels are accordion sections in the existing sidebar or sub-panels
- New commands extend the existing `/command` pattern in main.js
- Print method constraints propagate via a `productionConfig` state object in JS that gates available features
- Font loading uses Tauri's `fs` plugin to scan project `fonts/` directory
- Variant management uses the existing `tmp/references/manifest.json` pattern (JSON file-based state)
- Preflight runs as a Tauri command that validates the SVG against the production config
- All domain validation happens through the existing `multi_domain_validate()` pipeline
