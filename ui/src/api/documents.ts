/**
 * Types for documented JSON documents that are versioned by their own format
 * fields rather than by pydantic response models: snapshot manifests
 * (options-snapshot/v1) and surface fit artifacts (fit code version).
 * Only the fields the UI reads are declared.
 */

export interface SnapshotSummary {
  snapshot_id: string;
  source: string;
  synthetic: boolean;
  as_of: string;
  ingested_at: string;
  underlying: string;
  accepted: number;
  total_quotes: number;
}

export interface SnapshotManifest {
  snapshot_id: string;
  source: string;
  synthetic: boolean;
  as_of: string;
  parser_version: string;
  raw_sha256: string;
  underlying: { id: string; currency: string; spot: string; spot_observed_at: string | null };
  carry: { rate: string; dividend_yield: string; source: string } | null;
  quality_policy: Record<string, unknown>;
  quality: {
    total_quotes: number;
    accepted: number;
    quarantined: number;
    quarantine_reasons: Record<string, number>;
    flags_on_kept_or_quarantined: Record<string, number>;
    accepted_by_expiry: Record<string, number>;
    freshness: { quotes_with_known_time: number; quotes_with_unknown_time: number; note: string };
  };
}

export interface QuoteRow {
  row: number;
  contract_id: string | null;
  option_type: string | null;
  strike: string | null;
  expiry: string | null;
  exercise_style: string | null;
  bid: string | null;
  ask: string | null;
  quote_time: string | null;
  issues: string[];
  accepted: boolean;
}

export interface FitStats {
  n: number;
  price_rmse?: number;
  max_abs_price_error?: number;
  weighted_residual_rms?: number;
  max_abs_weighted_residual?: number;
  bid_ask_containment?: number;
}

export interface FitResidual {
  expiry: string;
  T: number;
  strike: number;
  k: number;
  type: string;
  bid: number;
  ask: number;
  mid: number;
  model: number;
  residual: number;
  inside_bid_ask: boolean;
  held_out: boolean;
  model_iv: number;
  iv_bid: number | null;
  iv_mid: number | null;
  iv_ask: number | null;
}

export interface FitArtifact {
  fit_id: string;
  created_at: string;
  status: "ok" | "failed";
  failure_reason?: string;
  failure_detail?: string;
  snapshot_id: string;
  snapshot_as_of: string;
  later_snapshot_id: string | null;
  exclusions: Record<string, number>;
  forwards: Record<string, { T: number; method: string; F?: number; D?: number; parity_pairs: number }>;
  surface?: { rho: number; eta: number; gamma: number; expiries: number[]; thetas: number[]; interpolation: string };
  guarantee?: { conditions: Record<string, number | boolean>; statement: string };
  arbitrage_diagnostics?: {
    statement: string;
    min_butterfly_density_g: number;
    sampled_violations_total: number;
  };
  bound_hits?: string[];
  in_sample?: FitStats;
  held_out?: FitStats;
  later_snapshot?: FitStats & { snapshot_id: string; elapsed_days: number; method: string };
  truth_recovery?: { max_abs_iv_error: number; k_range: number[] } | null;
  optimizer_runs?: { status: number; message: string; success: boolean; nfev: number; cost: number }[];
  residuals?: FitResidual[];
}

export interface FitSummary {
  fit_id: string;
  status: string;
  created_at: string;
  snapshot_id: string;
  snapshot_as_of: string;
  failure_reason: string | null;
  held_out_containment: number | null;
  stale: boolean;
}
