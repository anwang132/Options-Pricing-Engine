import type { FitArtifact, FitSummary, HestonArtifact, QuoteRow, SnapshotManifest, SnapshotSummary } from "./documents";
import type { components } from "./schema";

export type * from "./documents";

export type Schemas = components["schemas"];
export type PriceRequest = Schemas["PriceRequestIn-Input"];
export type PriceResponse = Schemas["PriceResponse"];
export type CompareRequest = Schemas["CompareRequestIn"];
export type CompareResponse = Schemas["CompareResponse"];
export type TreeConvergenceRequest = Schemas["TreeConvergenceIn"];
export type TreeConvergenceResponse = Schemas["TreeConvergenceResponse"];
export type MCConvergenceRequest = Schemas["MCConvergenceIn"];
export type MCConvergenceResponse = Schemas["MCConvergenceResponse"];
export type ImpliedVolRequest = Schemas["ImpliedVolIn"];
export type ImpliedVolResponse = Schemas["ImpliedVolResponse"];
export type RunManifest = Schemas["RunManifest-Output"];
export type GreekOut = Schemas["GreekOut"];
export type PortfolioRequest = Schemas["PortfolioRequestIn"];
export type ProfileResponse = Schemas["ProfileResponse"];
export type ExerciseBoundaryResponse = Schemas["ExerciseBoundaryResponse"];
export type SmileResponse = Schemas["SmileResponse"];
export type HedgingRequest = Schemas["HedgingIn"];
export type HedgingResponse = Schemas["HedgingResponse"];
export type SurfaceViewsResponse = Schemas["SurfaceViewsResponse"];
export type PaperSummary = Schemas["PaperSummaryOut"];
export type PaperAccountRow = Schemas["PaperAccountRow"];
export type PaperTradeIn = Schemas["PaperTradeIn"];
export type PaperMarkIn = Schemas["PaperMarkIn"];
export type MarkResult = Schemas["MarkResultOut"];
export type PaperHistoryPoint = Schemas["PaperHistoryPoint"];
export type LedgerEvent = Schemas["LedgerEventOut"];
export type PortfolioResponse = Schemas["PortfolioResponse"];

export interface EngineInfo {
  engine_id: string;
  version: string;
  description: string;
  config_type: string;
  capabilities: {
    exercise_styles: string[];
    dividend_treatments: string[];
    model_families: string[];
    greeks: Record<string, string>;
    stochastic: boolean;
    batching: boolean;
    diagnostics: string[];
    zero_volatility: boolean;
  };
}

/** Structured error returned by every endpoint (see ErrorResponse in the API). */
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

async function request<T>(path: string, body?: unknown, raw = false): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : raw ? (body as string) : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network_error", "Could not reach the API server. Is it running?");
  }
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new ApiError(res.status, "bad_response", `Unexpected response (${res.status})`);
  }
  if (!res.ok) {
    const err = (data as { error?: { code: string; message: string; details?: Record<string, unknown> } })
      ?.error;
    throw new ApiError(res.status, err?.code ?? "http_error", err?.message ?? res.statusText, err?.details);
  }
  return data as T;
}

export const api = {
  engines: () => request<EngineInfo[]>("/api/v1/engines"),
  price: (b: PriceRequest) => request<PriceResponse>("/api/v1/price", b),
  manifest: (b: PriceRequest) => request<RunManifest>("/api/v1/price/manifest", b),
  compare: (b: CompareRequest) => request<CompareResponse>("/api/v1/compare", b),
  treeConvergence: (b: TreeConvergenceRequest) =>
    request<TreeConvergenceResponse>("/api/v1/convergence/tree", b),
  mcConvergence: (b: MCConvergenceRequest) => request<MCConvergenceResponse>("/api/v1/convergence/mc", b),
  impliedVol: (b: ImpliedVolRequest) => request<ImpliedVolResponse>("/api/v1/implied-vol", b),
  portfolio: (b: PortfolioRequest) => request<PortfolioResponse>("/api/v1/portfolio/scenarios", b),
  snapshots: () => request<SnapshotSummary[]>("/api/v1/snapshots"),
  snapshot: (id: string) => request<SnapshotManifest>(`/api/v1/snapshots/${id}`),
  snapshotQuotes: (id: string) => request<QuoteRow[]>(`/api/v1/snapshots/${id}/quotes`),
  loadBundled: () =>
    request<{ file: string; created: boolean; snapshot_id: string }[]>("/api/v1/snapshots/bundled", {}),
  // Snapshot files are uploaded verbatim so their bytes (and hash) are preserved.
  uploadSnapshot: (raw: string) =>
    request<{ created: boolean; manifest: SnapshotManifest }>("/api/v1/snapshots", raw, true),
  fits: () => request<FitSummary[]>("/api/v1/surface/fits"),
  fit: (snapshot_id: string, later_snapshot_id: string | null) =>
    request<{ created: boolean; artifact: FitArtifact }>("/api/v1/surface/fits", { snapshot_id, later_snapshot_id }),
  fitArtifact: (id: string) => request<FitArtifact>(`/api/v1/surface/fits/${id}`),
  surfaceViews: (id: string) => request<SurfaceViewsResponse>(`/api/v1/surface/fits/${id}/views`),
  paperAccounts: () => request<PaperAccountRow[]>("/api/v1/paper/accounts"),
  paperOpen: (name: string, starting_cash: string, opened_at: string) =>
    request<PaperSummary>("/api/v1/paper/accounts", { name, starting_cash, opened_at, currency: "USD" }),
  paperSummary: (id: string) => request<PaperSummary>(`/api/v1/paper/accounts/${id}`),
  paperTrade: (id: string, b: PaperTradeIn) => request<PaperSummary>(`/api/v1/paper/accounts/${id}/trades`, b),
  paperVoid: (id: string, trade_id: string, reason: string, timestamp: string) =>
    request<PaperSummary>(`/api/v1/paper/accounts/${id}/voids`, { trade_id, reason, timestamp }),
  paperSettle: (id: string, underlying: string, expiry: string, settlement_price: string, timestamp: string) =>
    request<PaperSummary>(`/api/v1/paper/accounts/${id}/settlements`, { underlying, expiry, settlement_price, timestamp }),
  paperMark: (id: string, b: PaperMarkIn) => request<MarkResult>(`/api/v1/paper/accounts/${id}/marks`, b),
  paperHistory: (id: string) => request<PaperHistoryPoint[]>(`/api/v1/paper/accounts/${id}/history`),
  paperEvents: (id: string) => request<LedgerEvent[]>(`/api/v1/paper/accounts/${id}/events`),
  profile: (b: PriceRequest) => request<ProfileResponse>("/api/v1/visuals/profile", b),
  hestonCalibrate: (snapshot_id: string, later_snapshot_id: string | null) =>
    request<{ created: boolean; artifact: HestonArtifact }>("/api/v1/heston/calibrations", { snapshot_id, later_snapshot_id }),
  hedging: (b: HedgingRequest) => request<HedgingResponse>("/api/v1/analysis/hedging", b),
  smile: (b: PriceRequest) => request<SmileResponse>("/api/v1/visuals/smile", b),
  exerciseBoundary: (b: PriceRequest) => request<ExerciseBoundaryResponse>("/api/v1/visuals/exercise-boundary", b),
};
