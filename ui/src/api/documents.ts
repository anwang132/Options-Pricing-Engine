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

export interface HestonStats extends FitStats {
  iv_rmse_vol_points?: number | null;
}

export interface HestonResidual {
  expiry: string;
  T: number;
  strike: number;
  k: number;
  type: string;
  bid: number;
  ask: number;
  mid: number;
  model: number;
  inside_bid_ask: boolean;
  held_out: boolean;
  model_iv: number | null;
  iv_bid: number | null;
  iv_mid: number | null;
  iv_ask: number | null;
}

/** Heston calibration artifact (application/heston_calibration.py, code version 1.x). */
export interface HestonArtifact {
  calibration_id: string;
  created_at: string;
  status: "ok" | "failed";
  failure_reason?: string;
  failure_detail?: string;
  snapshot_id: string;
  later_snapshot_id: string | null;
  parameters?: Record<"v0" | "kappa" | "theta" | "sigma" | "rho", number>;
  uncertainty?: {
    standard_errors: Record<string, number>;
    standard_errors_quote_noise_only: Record<string, number>;
    correlation: number[][];
    condition_number_scaled_jtj: number;
    strongly_correlated_pairs: [string, string, number][];
  };
  feller_satisfied?: boolean;
  feller_ratio?: number;
  bound_hits?: string[];
  in_sample?: HestonStats;
  held_out?: HestonStats;
  residuals?: HestonResidual[];
  truth_recovery?: {
    generating_parameters: Record<string, number>;
    errors_in_standard_errors: Record<string, number | null>;
    max_abs_iv_error_vs_truth: number;
  };
  later_snapshot?: {
    snapshot_id: string;
    elapsed_days: number;
    no_refit: HestonStats;
    v0_refit: { v0: number; standard_errors: Record<string, number>; held_out: HestonStats; truth_v0?: number };
  };
  optimizer_runs?: { success: boolean; nfev: number; cost: number }[];
}
