# 0008 — Typed outcomes and per-Greek statuses

Status: accepted

## Decision
- Failures are `DomainError(code, message, details)` with stable codes: `invalid_contract`,
  `unsupported_contract`, `expired_contract`, `invalid_market_input`, `invalid_model_parameter`,
  `invalid_numerical_config`, `unsupported_combination`, `unknown_engine`, `work_limit_exceeded`,
  `invalid_tree_probability`, `numerical_failure`, `invalid_request`, `not_found`.
- Engine capabilities (exercise, dividend treatment, model family, σ = 0 support, Greek methods)
  are declared data; the application rejects unsupported combinations *before* computation and
  lists the engines that would support the request.
- Each Greek carries its own status (`ok`, `one_sided`, `undefined_at_kink`,
  `not_applicable_at_expiry`, `not_supported`, `failed`). A value is present iff the status is
  `ok`/`one_sided` (enforced in `GreekResult.__post_init__`). Zero is never a failure stand-in.
- IV has its own status set (0004). HTTP maps codes to 413 (work limit), 404 (unknown), 422 (all
  other domain failures), 503 (busy), 504 (timeout), always with the same JSON envelope.
