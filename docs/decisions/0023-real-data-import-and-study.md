# 0023 — Real option-chain import and the two-day model study

Status: accepted (2026-09-30)

## Context
All committed data is synthetic, and it has to stay that way: vendor and broker data is
usually licensed, and the project must not commit proprietary market data. What can be built
and tested here is the path from a real export to evidence.

## Decision
- **CSV importer** (`adapters/snapshots/csv_chain.py`) driven by a TOML column mapping rather
  than hard-coded vendor formats. Two layouts: long (one row per contract) and straddle (calls
  and puts side by side). Timestamps accept a strptime format, a list of formats, or ISO-8601;
  date-only expiries get a stated clock time in a stated IANA timezone. The tests cover the
  New York DST change, where the same 16:00 is 20:00Z in October and 21:00Z in December.
- **Nothing is guessed.** Exercise style is required in the mapping, because European index
  options and American equity options must not be mixed up. Spot, valuation time and carry are
  command-line inputs. Values that do not parse are passed through as text, so ingestion
  quarantines the row with a specific reason instead of the importer dropping it silently.
  Every import is labelled `synthetic: false` unless `--synthetic` is given, and its source
  records the SHA-256 of the file and of the mapping.
- **Two-day study** (`options-engine study <day1> <day2>`): SSVI fit and Heston calibration of
  day 1, each evaluated on day 2 without refitting; Heston also with v0 refitted. The metrics
  come only from `[real_data_study]`, declared before the code existed, and models are compared
  only on held-out and next-day metrics. There are no pass/fail thresholds, because real data
  has no known truth.
- **Output goes under the git-ignored data directory** by default (`data/studies/`), because a
  study of licensed data is itself derived data.

## Tested on
Synthetic CSVs in both layouts, generated from the Heston fixtures (`examples/csv/`), end to
end: import → ingest (quarantine counts per defect) → study. One finding from those tests: a
single strptime format rejected a timestamp without fractional seconds, which would have
quarantined a valid quote. Formats now accept a list.

## For real data
See `docs/real-data.md`. European, cash-settled index options (SPX, XSP, NDX, RUT) fit the
pipeline directly; American single-stock options are excluded from surface and Heston fits with
a reason.
