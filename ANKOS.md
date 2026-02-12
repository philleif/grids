# ANKOS: An Emergent Orchestration Framework

**ANKOS** is the architectural framework underlying GRIDS. It defines how AI-powered creative tools are built from three foundational pillars: local behavioral rules, economic flow management, and deep domain expertise.

Any application built on ANKOS routes through this architecture -- agents are the execution backend, not an optional enhancement.

---

## The Three Pillars

### 1. Wolfram Local Rules (Agent Behavior)

From *A New Kind of Science*: complex, sophisticated output emerges from agents following extremely simple local rules.

Each agent has a small **rule table** mapping `(current_state, input_signal) → (action, next_state)`. Agents only see their own queue and immediate neighbors. There is no global planner.

```
IDLE    + NEW_ITEM        → PROCESS  / WORKING
WORKING + BATCH_COMPLETE  → EMIT     / IDLE
WORKING + CRITIQUE_NEEDED → CRITIQUE / CRITIQUING
WORKING + NEIGHBOR_IDLE   → SPLIT_BATCH / WORKING
```

Behavior is changed by swapping rule tables, not rewriting prompts.

### 2. Reinertsen Flow Economics (Pipeline Management)

From *The Principles of Product Development Flow*: optimize for **flow**, not resource utilization.

- **WSJF prioritization**: `Cost of Delay / Job Size`. High-value, small-effort items first.
- **WIP limits**: Hard caps per queue prevent cascade failures.
- **Batch size control**: Small batches move faster, earlier feedback.
- **Iteration economics**: Re-entering work gets `cost_of_delay * 1.2` and `job_size * 0.7`, naturally rising in WSJF priority. The system economically self-corrects toward finishing over starting.

### 3. Domain Expertise Trees (Knowledge Grounding)

Each domain is a pluggable skill tree instantiated from a YAML config:

- **Master agent**: Senior expert with veto power, grounded in indexed textbooks.
- **Sub-agents**: Obsessive specialists (typography, composition, color, etc.) each scoring one narrow facet.
- **Knowledge store**: ChromaDB vector collections built from ingested PDFs and web research.

Agents don't hallucinate design principles -- they retrieve and cite specific passages from authoritative sources.

---

## The ANKOS Contract

Any GRIDS application that claims ANKOS compliance must satisfy:

### Required Wiring

1. **Domain config**: The app reads from a `domains/*.yaml` config specifying master, sub-agents, and knowledge sources.
2. **Work order queue**: All execution flows through the file-based WSJF queue. No direct LLM calls that bypass the queue.
3. **Rule-driven state machines**: Agent transitions are governed by Wolfram rule tables, not ad-hoc conditionals.
4. **Domain validation**: Every artifact passes through sub-agent scoring and master approval/veto before being considered complete.
5. **Provenance tracking**: All design decisions are logged with influences, alternatives considered, and confidence scores.
6. **Project spec**: The app reads from a `project.yaml` (produced by `grids-intake` or equivalent) as its canonical configuration.

### Optional Capabilities

- **Visual critique loop**: Playwright capture + vision LLM scoring (for visual artifacts).
- **Sharpen-the-axe**: Pre-task skill acquisition from the book finder + skill registry. The book finder uses a 5-source cascade (Internet Archive, Open Library, DuckDuckGo `filetype:pdf`, Anna's Archive, IA page-rip) to maximize the chance of finding downloadable PDFs for any reference text.
- **Session memory**: Auto-summarize completed work for cross-session recall.
- **Struggle protocol**: Agents surface their work-in-progress when stuck.
- **Reflections journal**: Post-task introspection and system-refine proposals.
- **OpenCode in-app CLI**: Any GRIDS app with a UI may embed an [OpenCode](https://opencode.ai) server (`opencode serve`) as its interactive agent interface. The user types natural language or commands in the app's chat panel; OpenCode routes to the LLM (via CLIProxyAPI or any configured provider), with full tool use (bash, file edit, search) for invoking `grids-*` CLI commands. This gives every GRIDS app a customizable AI coding agent without building bespoke chat infrastructure.

### What ANKOS Is Not

- It is **not** a wrapper around a single LLM call.
- It is **not** a chat interface that optionally invokes tools.
- It is **not** a pipeline where "AI" means "we called GPT once at the end."
- The agents **are** the execution backend. Human interaction is direction and refinement, not generation.

---

## Architecture Diagram

```
User Brief
  │
  ▼
┌─────────────┐     ┌──────────────┐
│ grids-intake │────▶│ project.yaml │
│   (TUI)     │     └──────┬───────┘
└─────────────┘            │
                           ▼
                  ┌─────────────────┐
                  │  Domain Master   │  ← knowledge-grounded senior expert
                  │  (design.yaml)   │
                  └────────┬────────┘
                           │ specify
                           ▼
                  ┌─────────────────┐
                  │  Work Order      │  ← WSJF-ranked JSON queue
                  │  Queue           │
                  └────────┬────────┘
                           │ pick up (Reinertsen flow)
                           ▼
              ┌────────────────────────┐
              │  Wolfram Rule Engine    │  ← (state, signal) → (action, next_state)
              └────────────┬───────────┘
                           │
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
      ┌──────────┐  ┌──────────┐  ┌──────────┐
      │  Coder   │  │  Tester  │  │  Visual   │
      │  Agent   │  │  Agent   │  │  Critique │
      └────┬─────┘  └────┬─────┘  └────┬─────┘
           │              │              │
           └──────────────┼──────────────┘
                          │ artifacts
                          ▼
              ┌────────────────────────┐
              │  Sub-Agent Validation   │  ← typography, composition, color, ...
              └────────────┬───────────┘
                           │ scores
                           ▼
              ┌────────────────────────┐
              │  Master Approve/Veto   │
              └────────────┬───────────┘
                           │
                ┌──────────┴──────────┐
                ▼                     ▼
           ✓ Approved            ✗ Iterate
           (load to canvas)      (WSJF bump, re-queue)
```

---

## Current ANKOS Applications

| Application | Status | Domain Configs | Output Formats |
|-------------|--------|----------------|----------------|
| `grids-calling-cards` | In development | design, creative-production | SVG, PDF, IDML, imposed print PDF |
| `grids-zine` | Scaffold only | design, editorial | — |

---

## Reference Texts

- Stephen Wolfram, *A New Kind of Science* (2002) -- local rules, emergent complexity
- Donald Reinertsen, *The Principles of Product Development Flow* (2009) -- economic flow, WSJF, WIP limits, batch sizing
- Domain-specific sources indexed per `domains/*.yaml` config
