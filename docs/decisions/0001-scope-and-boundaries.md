# 0001 — Scope: a validation workbench, built in milestones

Status: accepted (2026-09-28)

## Context
The build plan (`OPTIONS_ENGINE_PLAN_ENHANCED.md`) warns against breadth before depth. This project
is separate from the "Not My Debt" application and shares no code with it.

## Decision
- Build a *production-oriented research workbench*: every result is inspectable, replayable, and
  labelled with its assumptions and numerical reliability.
- Deliver Release A (European BSM, CRR, terminal MC, IV, validation report, CLI, UI) before
  Release B (snapshots, American exercise with cash dividends, portfolio scenarios, surface fit).
- Excluded: exotic payoffs, execution/brokerage, real-time feeds, portfolio accounting,
  cross-currency aggregation, trade recommendations, VaR (see 0012).
- The core demo and CI run offline with synthetic inputs and committed fixtures.

## Consequences
Advanced models (PDE, Heston) wait until the foundational gates are reliable.
