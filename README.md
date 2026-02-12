# GRIDS

**Emergent orchestration for creative AI.** Complex creative output from simple local rules. No central planner.

GRIDS arranges AI agents on a 2D cellular automaton grid where coordination emerges from local interactions only. Each agent follows a small rule table -- not a prompt. Behavior is changed by swapping rule sets, not rewriting prose.

[Whitepaper (PDF)](docs/GRIDS-whitepaper.pdf) | [Interactive Visualization](https://gist.githack.com/philleif/20db97dfc535814cb812f50bc69252f0/raw/visualization-d3.html) | [philleif.com](https://philleif.com)

---

## The Three Pillars

### 1. Wolfram Local Rules (Agent Behavior)

From *A New Kind of Science*: complex output emerges from agents following simple local rules.

```
IDLE    + NEW_ITEM        -> PROCESS    / WORKING
WORKING + BATCH_COMPLETE  -> EMIT       / IDLE
WORKING + CRITIQUE_NEEDED -> CRITIQUE   / CRITIQUING
IDLE    + STALE           -> CHALLENGE  / CRITIQUING
```

Totalistic signal detection. Class 4 anti-quiescence. Evolutionary rule space search.

### 2. Reinertsen Flow Economics (Pipeline Management)

From *Principles of Product Development Flow*: optimize for flow, not resource utilization.

- **WSJF prioritization**: `cost_of_delay / job_size`. High-value, small-effort items first.
- **WIP limits**: Hard caps per cell prevent cascade failures.
- **Iteration economics**: Rework gets `CoD × 1.2`, `size × 0.7` -- automatically outprioritizes new work.

### 3. Domain Expertise Trees (Knowledge Grounding)

Agents grounded in canonical texts, not generic prompts. 1168+ indexed chunks from 24+ books:

- **Design**: Bringhurst, Mueller-Brockmann, Norman, Lupton, Albers, Bierut, NASA
- **Culture-Crit**: Barthes, Sontag, Benjamin, Hebdige, Marcus, and 12 more
- **Editorial**: Chicago Manual of Style + web research
- **Production**: Film, music, and software production pipelines

Master agents with veto power. Sub-agents obsessively score narrow aspects (typography at 0.9 strictness, color at 0.8).

---

## Architecture

```
User Brief
  │
  ▼
┌──────────────────────────────────────────┐
│         2D Agent Grid (CA Lattice)       │
│                                          │
│  Research ── Domain Masters ── Sub-Agents│
│      │            │              │       │
│   Totalistic Signal Detection (NKS)      │
│      │            │              │       │
│   Reinertsen Flow (WSJF, WIP limits)     │
└──────────────┬───────────────────────────┘
               │
    ┌──────────┼──────────┐
    ▼          ▼          ▼
  Coder     Tester    Visual Critique
    │          │          │
    └──────────┼──────────┘
               │
    Sub-Agent Validation → Master Veto
               │
     ┌─────────┴─────────┐
     ▼                   ▼
  Approved          Iterate (WSJF bump)
```

**4-phase tick cycle** (simultaneous across all cells):
1. **READ**: Snapshot neighbor states, totalistic signal detection
2. **COMPUTE**: Apply rule tables -- deterministic given same inputs
3. **EXECUTE**: Perform actions (LLM calls, critique, emit, challenge)
4. **PROPAGATE**: Deliver outputs to neighbor inboxes, WSJF ordered, WIP enforced

---

## How It Differs

| Standard Multi-Agent | GRIDS |
|---|---|
| Central planner decides agent actions | Agents react to local state only |
| Fixed iteration counts | Economic iteration via WSJF |
| Unlimited work-in-progress | WIP limits prevent cascades |
| Generic LLM prompts | Knowledge-grounded from canonical texts |
| Behavior change = rewrite prompts | Behavior change = swap rule tables |
| Hand-designed coordination | Rules discovered via search (NKS) |
| System stops when tasks complete | Anti-quiescence perturbation |
| Critique is optional feedback | Critique FAIL forces rework |

---

## What It Produces

Input a creative brief. Get production-ready artifacts:

- SVG designs (calling cards, layouts)
- IDML files (InDesign-editable)
- Imposed print PDFs (for press)
- LaTeX-typeset documents
- Design decision provenance
- Moodboards + research archives

Every output is critiqued by obsessive sub-agents and approved by a knowledge-grounded master.

---

## Domain Configuration

Domains are pluggable YAML configs:

```yaml
domain:
  name: design
  description: "Comprehensive design expertise..."
sources:
  pdfs:
    - path: "REFERENCE/design/elements-of-typographic-style.pdf"
      collection: design-typography
master:
  knowledge_collections: [design-foundations, design-typography]
sub_agents:
  - name: typography
    aspect: "Typographic correctness, hierarchy, readability"
    strictness: 0.9
  - name: color
    aspect: "Color theory, contrast, palette coherence"
    strictness: 0.8
rules:
  approval_threshold: 0.75
  master_veto_threshold: 0.5
```

9 domains included: design, production-tech, editorial, creative-production, culture-crit, dataviz, agency-mix, dieter-rams, ocd-ux-nerd.

---

## Seed System

A seed YAML defines initial conditions. Same rules + different seeds = different emergent behavior:

```yaml
grid:
  width: 10
  height: 8
  neighborhood: moore

domains: [design, editorial, culture-crit]

execution:
  min_domain_coverage: 2
  relevance_threshold: 0.2

initial_work:
  - target: masters
    kind: brief_chunk
    cost_of_delay: 5.0
    content: "Your creative brief..."
```

Relevance filtering: sub-agents below the cosine similarity threshold against the brief are excluded. A print project won't waste cells on video specialists.

---

## The ANKOS Contract

**ANKOS** is the compliance framework. Any GRIDS app must have:

1. Domain config from `domains/*.yaml`
2. All execution through WSJF-prioritized queues
3. Rule-driven state machines (not ad-hoc conditionals)
4. Domain validation (sub-agent scoring + master veto)
5. Provenance tracking
6. Project spec as canonical config

ANKOS is **not** a wrapper around a single LLM call. The agents *are* the execution backend.

---

## Current State (February 2026)

- Full CA grid engine (Rust + Python)
- 9 domain configs, 1168+ indexed knowledge chunks
- PDF ingestion pipeline (profile, extract, OCR, structure, chunk)
- Execution agents (coder, tester, runner)
- Visual feedback loop (Playwright + vision LLM critique)
- Book finder with 5-source cascade
- Seed YAML system with relevance filtering
- Rich TUI with streaming output and grid visualization
- LaTeX typesetting, IDML export, print PDF imposition
- Session memory, provenance tracking, reflections journal
- 30+ CLI tools

---

## References

1. Stephen Wolfram, *A New Kind of Science* (2002)
2. Donald Reinertsen, *Principles of Product Development Flow* (2009)
3. Robert Bringhurst, *Elements of Typographic Style*
4. Josef Mueller-Brockmann, *Grid Systems in Graphic Design*
5. Roland Barthes, *Mythologies*
6. Susan Sontag, *Against Interpretation*

---

Built by [Phil Leif](https://github.com/philleif). Under active development.
