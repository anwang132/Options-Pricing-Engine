# 0009 — API runtime: bounded worker processes, budgets, no cache

Status: accepted

## Decision
- CPU work runs in a spawn-context `ProcessPoolExecutor` (default 2 workers, pre-warmed at
  startup), never on the event loop. `OPTIONS_ENGINE_WORKERS=0` runs in a thread (tests).
- Admission control: at most `OPTIONS_ENGINE_MAX_IN_FLIGHT` (8) jobs; excess requests get 503
  immediately instead of queueing without bound. A slot is released by the job's own completion
  callback, so a client timeout never frees capacity that is still computing (a bug in the first
  version, fixed with a regression test); a job still queued at timeout is cancelled.
- The client timeout (30 s → 504) does not kill the worker. Runtime is bounded by work limits
  validated before submission. Measured worst cases on an Apple M4: CRR 20,000 steps American
  with all Greeks 1.9 s; MC 20 M paths + 1 M pilot 0.6 s; convergence-study budgets < 0.2 s per
  run; portfolio scenarios at their limits 6.8–8.4 s (reports/post-review; ADR 0014);
  surface fit 0.3 s. A kill/cancel mechanism is deferred until a workload needs it.
- Snapshot listing/ingestion and fit listing are light file operations served from the
  threadpool; fitting and portfolio scenarios go through the process pool.
- No result cache: requests are cheap relative to the risk of a mis-keyed cache. Request hashes
  (canonical JSON incl. engine config and seed) exist and would be the cache key if one is added.
- Logs carry request id, method, path, status, duration — never bodies.
