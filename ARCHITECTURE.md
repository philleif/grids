# GRIDS Architecture

An emergent orchestration system for building specialized AI-powered software tools for creative professionals. The system produces complex, sophisticated output from simple local rules and economic flow management, with pluggable domain expertise built from books, academic research, and web sources.

## Project Structure

```
GRIDS/
├── apps/                    # Rust applications
│   ├── demo/                # Demo app
│   └── zine/                # Zine CLI
├── libs/                    # Rust libraries
│   └── layout/              # Layout engine (Grid, Page, Block, render)
├── scripts/                 # Python orchestration + tools
│   ├── bin/                 # CLI entry points (grids-ingest, grids-session, etc.)
│   └── src/grids/
│       ├── pdf/             # PDF processing pipeline (profile, extract, OCR, structure, chunk, figures)
│       ├── knowledge/       # ChromaDB vector store, embeddings, concept tagging
│       ├── orchestration/   # Wolfram rules, Reinertsen flow, LangGraph agents, agent wrapper
│       ├── domain/          # Domain agent system (master, sub-agents, work orders)
│       ├── execution/       # Execution agents (coder, tester, runner) -- consume work orders
│       ├── visual/          # Visual feedback loop (Playwright capture, vision LLM critique)
│       ├── memory/          # Session memory (auto-summarize, recall prior decisions)
│       ├── provenance/      # Python decision tree tracker (mirrors Rust, design notes)
│       ├── book/            # Book finder (IA, Open Library, DDG filetype:pdf, Anna's Archive, IA page-rip)
│       ├── skills/          # Skill registry + sharpen-the-axe pre-task acquisition
│       ├── meta/            # Reflections journal + system-refine meta-agent
│       ├── comms/           # Struggle protocol (agents show work when stuck)
│       ├── moodboard/       # Deep research + moodboard generation + visual iteration
│       ├── apps/            # Domain-specific applications (calling cards, etc.)
│       └── bridge.py        # Python-to-Rust bridge (agent output → layout JSON)
├── domains/                 # Domain YAML configs
│   ├── design.yaml          # Design domain (6 books, 1168 chunks, 5 sub-agents)
│   ├── dataviz.yaml         # Data visualization domain (Tufte, 4 sub-agents)
│   ├── editorial.yaml       # Editorial writing domain (Chicago style, 5 sub-agents)
│   ├── creative-production.yaml  # Creative production domain (workflows, 4 sub-agents)
│   └── agency-mix.yaml      # Agency roles domain (strategy, creative, copy, 5 sub-agents)
├── REFERENCE/               # Source PDFs (not in git)
│   └── design/              # Norman, Bringhurst, Lupton, Bierut, Mueller-Brockmann, NASA
├── tui/                     # TUI viewer
└── tmp/                     # Working data, ChromaDB, session output
```

## Foundational Frameworks

### Wolfram / A New Kind of Science → Agent Behavior

Complex output emerges from agents following extremely simple local rules. Each agent has a small **rule table** mapping `(current_state, input_signal) → (action, next_state)`. Agents only see their own queue and immediate neighbors. There is no global planner.

```
IDLE    + NEW_ITEM        → PROCESS  / WORKING
WORKING + BATCH_COMPLETE  → EMIT     / IDLE
WORKING + CRITIQUE_NEEDED → CRITIQUE / CRITIQUING
WORKING + NEIGHBOR_IDLE   → SPLIT_BATCH / WORKING   (break monotony)
```

Rule tables are swappable: change agent behavior by swapping rule sets, not rewriting prompts.

### Reinertsen / Principles of Product Development Flow → Pipeline Economics

Optimize for **flow**, not resource utilization.

- **WSJF prioritization**: `Cost of Delay / Job Size`. High-value, small-effort items process first.
- **WIP limits**: Hard caps per queue prevent cascade failures.
- **Batch size control**: Small batches move faster, giving earlier feedback.
- **Iteration economics**: Re-entering work gets `cost_of_delay * 1.2` and `job_size * 0.7`, naturally rising in WSJF priority -- the system economically self-corrects toward finishing over starting.

## Domain Agent System

A **domain** is a pluggable skill tree instantiated from a YAML config. Each domain has one **master agent** (owns the "deep skill") and N **sub-agents** (each obsessive about one narrow facet).

```
                        Domain Config (YAML)
                              │
                    ┌─────────▼─────────┐
                    │   Domain Master    │  ← senior expert, veto power
                    └─────────┬─────────┘
                              │
               ┌──────────────┼──────────────┐
               ▼              ▼              ▼
          Sub-Agent A    Sub-Agent B    Sub-Agent C     ← obsessive specialists
               │              │              │
               └──────────────┼──────────────┘
                              │ scores
                    ┌─────────▼─────────┐
                    │    Validation      │  ← weighted aggregate + master veto
                    └─────────┬─────────┘
                              │ approve / iterate
                    ┌─────────▼─────────┐
                    │  Work Order Queue  │  ← JSON files, WSJF-ranked
                    └─────────┬─────────┘
                              │
               ┌──────────────┼──────────────┐
               ▼              ▼              ▼
            Coder          Tester         DevOps          ← execution agents (separate processes)
```

### Domain Config (`domains/*.yaml`)

Defines sources (PDFs + web research), master agent, sub-agents with strictness levels, and approval rules:

```yaml
domain:
  name: design
  description: "Comprehensive design expertise..."
sources:
  pdfs: [...]           # Books to ingest
  web_research:
    seed_queries: [...]  # LLM-expanded search queries for gaps
master:
  knowledge_collections: [design-foundations, design-typography, design-visual]
sub_agents:
  - name: typography
    aspect: "Typographic correctness, hierarchy, readability..."
    strictness: 0.9     # 0.0 = lenient, 1.0 = obsessive
rules:
  approval_threshold: 0.75
  master_veto_threshold: 0.5
```

### Work Orders (Decoupled Execution)

Domain agents emit work orders as JSON files. Execution agents (separate processes) pick them up, produce artifacts, and deposit results. No shared process state.

```
domain agents emit → work-orders/*.json → execution agent picks up →
produces artifact → artifacts/*.json → domain agents validate →
approve (done) | iterate (new work order with feedback + WSJF bump)
```

### Components

```
scripts/src/grids/domain/
├── config.py          # Pydantic models for domain.yaml
├── research.py        # LLM-driven web research (DuckDuckGo, query expansion)
├── master.py          # DomainMaster: specify, validate, veto
├── sub_agent.py       # SubAgent: aspect-specific OCD scoring
├── work_orders.py     # File-based WSJF queue + iteration economics
├── validation.py      # Aggregate scoring + iteration loop
└── cli.py             # grids-domain-init, grids-domain-validate, grids-domain-specify
```

## Orchestration Engine

```
scripts/src/grids/orchestration/
├── rules.py      # Wolfram-inspired local rule tables per agent type
├── flow.py       # Reinertsen-inspired WSJF queues, WIP limits, batch sizing
├── graph.py      # LangGraph state machine (research → concept → layout → critique)
├── agents.py     # Agent definitions, LLM invocation, knowledge retrieval
└── session.py    # End-to-end session runner
```

### Orchestration Graph

```
Brief → Research → Concept → Layout → Critique
                      ↑                   │
                      └───── iterate ──────┘
                              (max 3)
```

## Knowledge Store

```
scripts/src/grids/knowledge/
├── store.py       # ChromaDB vector store, dynamic collections, cross-book retrieval
├── embeddings.py  # Local sentence-transformers (all-MiniLM-L6-v2)
└── concepts.py    # Regex tagging (NKS/DevFlow) + LLM tagging (arbitrary domains)
```

### Current Collections

| Collection | Documents | Sources |
|---|---|---|
| `design-foundations` | 701 | Norman (Design of Everyday Things), Mueller-Brockmann (Grid Systems) |
| `design-typography` | 273 | Bringhurst (Elements of Typographic Style), Lupton (Thinking with Type) |
| `design-visual` | 194 | Bierut (79 Short Essays on Design), NASA Graphics Standards Manual |

Total: **1,168 indexed chunks** across 6 books for the design domain.

## PDF Processing Pipeline

```
grids-ingest <pdf>    # Full pipeline: profile → extract/OCR → structure → chunk → figures
grids-pdf-profile     # Analyze PDF characteristics, recommend extraction strategy
grids-pdf-extract     # Text extraction (native + fallback OCR)
grids-pdf-ocr         # Full OCR for scanned documents
grids-pdf-structure   # Heading/section extraction
grids-pdf-chunk       # Semantic chunking with overlap
grids-pdf-figures     # Figure/image extraction
```

## CLI Reference

```bash
# PDF ingestion
grids-ingest <pdf> [output-dir]          # Full pipeline

# Knowledge store
grids-index <chunks.jsonl> -c <collection>  # Index chunks into ChromaDB
grids-query "question" [-c collection]      # Query knowledge store

# Domain agents
grids-domain-init <domain.yaml>           # Ingest PDFs + web research + build collections
grids-domain-specify <domain.yaml> "request"  # Generate work specification
grids-domain-validate <domain.yaml> <artifact.json>  # Validate against domain expertise

# Execution
grids-execute <domain.yaml> [--mode one|all|daemon]  # Process work orders
grids-cycle <domain.yaml> "request"       # Full cycle: specify → execute → validate

# Visual feedback
grids-capture <file.svg>                  # Render SVG/HTML to PNG screenshot
grids-critique <screenshot.png> -b "brief"  # Vision LLM critique
grids-visual-loop <artifact.json> <domain.yaml>  # Full visual iteration loop

# Orchestration
grids-session "brief"                     # Run full creative production session

# Research + Moodboard
grids-research "query"                    # Deep research (Met, LoC APIs)
grids-moodboard "query"                   # Generate visual moodboard

# Book finder (multi-source)
grids-book-find "title" [--domain design]  # Search + download reference PDFs
grids-book-find "title" -d ./out           # Download to specific directory
grids-book-find "title" --skip-web         # Skip DuckDuckGo web search
grids-book-find "title" --skip-annas       # Skip Anna's Archive
grids-book-find "title" --rip              # Enable IA page-rip fallback for borrow-only books
grids-book-find "title" --json             # Output raw JSON results (no table)

# Typesetting + Export
grids-typeset <file.tex>                  # Compile .tex card via LuaLaTeX
grids-typeset-gen <project.yaml>          # Generate .tex from project spec
grids-idml <project.yaml>                 # Export card as IDML (InDesign Markup Language)
grids-print-pdf --project <project.yaml>  # Impose cards onto stock sheets for printing

# Image tools
grids-gm <command> [args]                 # GraphicsMagick wrapper (CMYK, threshold, etc.)
grids-trace <image>                       # Potrace bitmap-to-vector
grids-svg <command> [args]                # Inkscape CLI wrapper

# Apps
grids-calling-cards [--brief "..."]       # Full pipeline demo: vintage calling cards
```

## LLM Routing

### Backend agents (Python pipeline)

Agents call Claude via CLIProxyAPI (`localhost:8317`), which routes auth by `User-Agent: claude-code/1.0` header. Configured in `agents.py`.

Environment variables:
- `GRIDS_LLM_BASE_URL` (default: `http://localhost:8317/v1`)
- `GRIDS_LLM_MODEL` (default: `claude-opus-4-6`)

### In-app CLI (Tauri desktop apps)

Desktop apps embed an [OpenCode](https://opencode.ai) server as the interactive agent interface:

```
Tauri webview  →  OpenCode serve (localhost:4096)  →  LLM provider
                       ↕
                 grids-* CLI tools (via bash tool)
```

- Start: `opencode serve --port 4096`
- The app's chat panel sends natural language to `POST /session/:id/message`
- OpenCode's agent has full tool use (bash, file edit, search) and can invoke any `grids-*` command
- Responses stream back via SSE (`GET /event`)
- LLM provider is configurable in OpenCode (CLIProxyAPI, Anthropic direct, OpenAI, local models via Ollama, etc.)

This avoids building bespoke chat/agent infrastructure in each app while giving users full customization of the AI backend.

## How This Differs from Standard Multi-Agent Systems

| Standard approach | GRIDS approach |
|---|---|
| Central planner decides agent actions | Agents react to local state only |
| Fixed iteration counts | Economically rational iteration via WSJF |
| Unlimited work-in-progress | WIP limits prevent cascade failures |
| Generic LLM prompts | Knowledge-grounded from domain texts |
| Behavior change = rewrite prompts | Behavior change = swap rule tables or domain config |
| Monolithic agent process | Decoupled work order queues (Reinertsen-pure) |
| One-size-fits-all validation | Obsessive sub-agents score narrow aspects independently |

## Execution Pipeline

The full loop from brief to validated artifact:

```
Brief → Domain Specify → Work Order → Sharpen (acquire skills) →
  Coder Agent (generate SVG/LaTeX/HTML) →
  Visual Critique Loop (Playwright capture → vision LLM → revise) →
  Domain Validation (sub-agents score → master veto) →
  Approve | Iterate (WSJF re-prioritized)
```

### Execution Agents

```
scripts/src/grids/execution/
├── coder.py    # Generates artifacts from work order specs via LLM
├── tester.py   # Structural validation + domain scoring pipeline
├── runner.py   # WSJF poll loop (one-shot, drain-all, daemon modes)
└── cli.py      # grids-execute, grids-cycle
```

### Visual Feedback Loop

```
scripts/src/grids/visual/
├── capture.py   # Playwright headless Chromium: SVG/HTML → PNG
├── critique.py  # Vision LLM scores: typography, composition, color, craft, intent
├── loop.py      # Automated render → capture → critique → revise loop
└── cli.py       # grids-capture, grids-critique, grids-visual-loop
```

### Agent Wrapper (Wolfram Rules → Real Execution)

`orchestration/agent_wrapper.py` binds rule tables to actual agent execution:
- Each agent is a state machine: `(state, signal) → (action, next_state)`
- Signal detection from queue state (QUEUE_FULL, NEIGHBOR_IDLE, etc.)
- Behavior is swappable by changing rule sets, not prompts

## Supporting Systems

### Session Memory

```
scripts/src/grids/memory/
├── session_memory.py  # Auto-summarize completed work → ChromaDB
└── recall.py          # Agents query prior decisions before new work
```

### Decision Tree (Python Bridge)

```
scripts/src/grids/provenance/
└── tracker.py  # Python DecisionTree mirroring Rust, lineage tracing, design notes, Rust JSON export
```

### Book Finder

Multi-strategy PDF search and download with cascading fallback:

```
scripts/src/grids/book/
└── finder.py  # Multi-source PDF search: IA, Open Library, web (DDG filetype:pdf),
               # Anna's Archive, IA page-rip fallback
```

**Search cascade** (in priority order):
1. **Internet Archive** -- advanced search + metadata API, tries all PDF filenames per item, detects borrow-only vs public access
2. **Open Library** -- finds IA identifiers for borrowable books
3. **DuckDuckGo `filetype:pdf`** -- web-wide search for direct PDF links (university servers, author sites, mirrors); highest success rate for in-copyright books
4. **Anna's Archive** -- shadow library aggregator (LibGen/Sci-Hub/Z-Library), tries multiple mirror domains (`.org`, `.se`, `.li`)
5. **IA page-image rip** -- downloads individual page images from the BookReader API and stitches into a PDF via `img2pdf` (for borrow-only items, requires `--rip` flag)

**Download validation**: checks `%PDF-` magic bytes, rejects HTML error pages, enforces minimum file size. Results are scored and ranked by likelihood of yielding a downloadable PDF (direct links first, then public IA items, then shadow library matches).

### Skill Registry + Sharpen

```
scripts/src/grids/skills/
├── registry.py  # YAML skill store with usage tracking
└── sharpen.py   # Pre-task skill acquisition (search + LLM + install)
```

### Reflections + System Refine

```
scripts/src/grids/meta/
├── reflections.py  # Post-task introspection journal, bottleneck analysis
└── refine.py       # Meta-agent proposes system improvements from reflection patterns
```

### Struggle Protocol

```
scripts/src/grids/comms/
└── struggle.py  # Agents show their work when stuck (attempt + what's failing + direction needed)
```

## Domain-Specific Apps

### Calling Cards (First Full Pipeline Demo)

`grids-calling-cards` runs the entire system end-to-end:
1. Deep research (Met Museum + Library of Congress vintage references)
2. Moodboard (curate, auto-zone, visual iterate)
3. Sharpen (acquire relevant skills)
4. Domain specify (work breakdown with acceptance criteria)
5. Execute (generate SVG calling cards)
6. Visual critique loop (capture → vision LLM → revise)
7. Domain validate (sub-agents score → master approve/iterate)
8. IDML export (InDesign-editable .idml files for each card face)
9. Decision tree (track all influences and alternatives)
10. Reflections (post-mortem analysis)
11. Output: SVG files, IDML files, imposed print PDF, design notes, moodboard, decisions.json
