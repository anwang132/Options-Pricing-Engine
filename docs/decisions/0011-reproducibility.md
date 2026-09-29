# 0011 — Reproducibility: manifests and reports

Status: accepted

## Decision
- **Run manifest** (CLI `--manifest`, API `/price/manifest`, UI download): exact request, result
  and environment. `options-engine replay` recomputes it and reports bitwise agreement.
- **Request hash:** SHA-256 of canonical JSON (sorted keys, Decimal text, UTC ISO timestamps,
  shortest float repr) over contract, market, model, engine id and full numerical config
  including seed.
- **Validation report** (`options-engine validate`): commit, dirty flag and a dirty-state hash
  over result-affecting paths (tracked diff + untracked file contents), uv.lock hash, fixture
  hashes, policy hash, platform/CPU/BLAS/thread env, every check with tolerances, exclusions and
  failures, statistical results, benchmarks (warm-up separated), plots, JSON + Markdown.
- README headline numbers are copied from committed reports under `reports/`.
