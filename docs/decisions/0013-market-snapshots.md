# 0013 — Immutable market snapshots and explicit quality outcomes

Status: accepted

## Decision
- One documented file format, `options-snapshot/v1` (JSON; numbers as strings to keep decimal
  text; `synthetic` must be stated explicitly). Parser: `adapters/snapshots/format_v1.py`.
- Snapshot id = hash(raw bytes, parser version, quality policy). The store writes raw file,
  normalised quotes and manifest once (temp dir + atomic rename, read-only files); re-ingesting
  identical bytes is a no-op; `load` re-verifies the normalised-data hash.
- File-level problems (invalid JSON, missing header fields, naive timestamps, non-positive spot)
  reject the file. Row-level problems quarantine the row with reasons; they never reject silently.
- Quality policy v1 (recorded in every manifest): quarantine unparseable/non-finite fields,
  negative prices, missing bid or ask, crossed quotes, zero bids, spreads > 50% of mid, expired
  contracts, conflicting duplicates (all copies), identical duplicates (all but first), stale
  quotes (> 900 s before as_of), quote vs spot observation gap > 60 s, non-standard
  deliverables, settlement lags. Flag but keep: unknown quote time, missing sizes.
- Unknown metadata stays `null` (e.g. quote time, carry inputs); nothing is defaulted to "fresh"
  or to a rate. A snapshot without carry cannot supply market inputs.
- Ingestion is instrument-agnostic: American quotes are kept; consumers decide eligibility.
- Providers: the file adapter and a seeded synthetic generator with labelled defects. No live
  provider is implemented (ADR 0012); any future provider must emit this format.
