import type { EngineInfo, Schemas } from "../api/client";
import { isDecimalText, percentToDecimal } from "./decimal";

export interface DividendRow {
  exDate: string; // datetime-local, interpreted as UTC
  amount: string;
}

/** Form state: every numeric field is kept as the text the user typed. */
export interface InstrumentForm {
  underlying: string;
  currency: string;
  optionType: "call" | "put";
  exercise: "european" | "american";
  strike: string;
  multiplier: string;
  expiry: string; // datetime-local, UTC
  asOf: string; // datetime-local, UTC
  spot: string;
  ratePct: string;
  yieldPct: string;
  volPct: string;
  model: "black_scholes" | "heston";
  heston: HestonForm;
  dividends: DividendRow[];
}

/** Heston inputs; variances are entered as their square-root vol in percent. */
export interface HestonForm {
  spotVolPct: string; // sqrt(v0)
  longVolPct: string; // sqrt(theta)
  kappa: string;
  volOfVol: string; // sigma
  rho: string;
}

export const DEFAULT_HESTON: HestonForm = {
  spotVolPct: "20",
  longVolPct: "20",
  kappa: "1.5",
  volOfVol: "0.5",
  rho: "-0.7",
};

export const DEFAULT_FORM: InstrumentForm = {
  underlying: "SYNTH",
  currency: "USD",
  optionType: "call",
  exercise: "european",
  strike: "40",
  multiplier: "100",
  expiry: "2027-03-30T08:00",
  asOf: "2026-09-28T20:00",
  spot: "42",
  ratePct: "10",
  yieldPct: "0",
  volPct: "20",
  model: "black_scholes",
  heston: DEFAULT_HESTON,
  dividends: [],
};

export type FieldErrors = Partial<Record<keyof InstrumentForm | keyof HestonForm | "general", string>>;

const utc = (local: string) => (local.length === 16 ? `${local}:00Z` : `${local}Z`);

/** Client-side checks only guide the user; the server re-validates everything. */
export function validate(f: InstrumentForm): FieldErrors {
  const e: FieldErrors = {};
  const positive = (k: keyof InstrumentForm, label: string) => {
    const v = f[k] as string;
    if (!isDecimalText(v)) e[k] = `${label} must be a number`;
    else if (Number(v) <= 0) e[k] = `${label} must be positive`;
  };
  positive("strike", "Strike");
  positive("spot", "Spot");
  positive("multiplier", "Multiplier");
  for (const [k, label] of [
    ["ratePct", "Rate"],
    ["yieldPct", "Dividend yield"],
  ] as const) {
    if (!isDecimalText(f[k])) e[k] = `${label} must be a number (percent)`;
  }
  if (f.model === "heston") Object.assign(e, validateHeston(f.heston, f.dividends.length > 0));
  else if (!isDecimalText(f.volPct)) e.volPct = "Volatility must be a number (percent)";
  else if (Number(f.volPct) < 0) e.volPct = "Volatility cannot be negative";
  if (!f.expiry) e.expiry = "Expiry is required";
  if (!f.asOf) e.asOf = "Valuation time is required";
  if (f.expiry && f.asOf && f.expiry < f.asOf) e.expiry = "Expiry is before the valuation time (expired contract)";
  f.dividends.forEach((d, i) => {
    if (!d.exDate || !isDecimalText(d.amount) || Number(d.amount) <= 0)
      e.general = `Dividend ${i + 1} needs an ex-date and a positive amount`;
  });
  return e;
}

function validateHeston(h: HestonForm, hasDividends: boolean): FieldErrors {
  const e: FieldErrors = {};
  const num = (k: keyof HestonForm, label: string, lo: number, hi: number, open = false) => {
    const v = h[k];
    if (!isDecimalText(v)) e[k] = `${label} must be a number`;
    else if (open ? !(Number(v) > lo && Number(v) < hi) : !(Number(v) >= lo && Number(v) <= hi))
      e[k] = `${label} must be ${open ? "strictly " : ""}between ${lo} and ${hi}`;
  };
  num("spotVolPct", "Initial vol", 0, 200);
  num("longVolPct", "Long-run vol", 0, 200);
  num("kappa", "Mean reversion", 0, 50);
  num("volOfVol", "Vol of vol", 0, 5);
  num("rho", "Correlation", -1, 1, true);
  if (hasDividends) e.general = "The Heston engine supports a continuous dividend yield only; remove the cash dividends";
  return e;
}

/** 2 kappa theta >= sigma^2 keeps variance away from zero; violation is allowed and reported. */
export function fellerSatisfied(h: HestonForm): boolean | null {
  const [lv, k, s] = [h.longVolPct, h.kappa, h.volOfVol].map(Number);
  if ([lv, k, s].some((x) => !Number.isFinite(x))) return null;
  return 2 * k * (lv / 100) ** 2 >= s * s;
}

export function instrumentPayload(f: InstrumentForm) {
  return {
    contract: {
      underlying: f.underlying,
      currency: f.currency,
      strike: f.strike.trim(),
      option_type: f.optionType,
      exercise_style: f.exercise,
      expiry: utc(f.expiry),
      multiplier: f.multiplier.trim(),
    },
    valuation: { as_of: utc(f.asOf) },
    market: {
      spot: f.spot.trim(),
      rate: percentToDecimal(f.ratePct),
      dividend_yield: percentToDecimal(f.yieldPct),
      cash_dividends: f.dividends.map((d) => ({ ex_date: utc(d.exDate), amount: d.amount.trim() })),
      source: "user_input",
    },
  } satisfies Pick<Schemas["CompareRequestIn"], "contract" | "valuation" | "market">;
}

export function modelPayload(f: InstrumentForm): Schemas["ModelIn"] | Schemas["HestonModelIn"] {
  if (f.model === "heston") {
    const variance = (pct: string) => Number(percentToDecimal(pct)) ** 2;
    return {
      family: "heston",
      v0: variance(f.heston.spotVolPct),
      theta: variance(f.heston.longVolPct),
      kappa: Number(f.heston.kappa),
      sigma: Number(f.heston.volOfVol),
      rho: Number(f.heston.rho),
    };
  }
  return { family: "black_scholes", volatility: Number(percentToDecimal(f.volPct)) };
}

/** Engines whose declared capabilities cover this form (server re-checks). */
export function supportedEngines(engines: EngineInfo[], f: InstrumentForm): EngineInfo[] {
  const dividendTreatment = f.dividends.length ? "escrowed_cash" : "continuous_yield";
  const zeroVol = f.model === "black_scholes" && isDecimalText(f.volPct) && Number(f.volPct) === 0;
  return engines.filter(
    (e) =>
      e.capabilities.model_families.includes(f.model) &&
      e.capabilities.exercise_styles.includes(f.exercise) &&
      e.capabilities.dividend_treatments.includes(dividendTreatment) &&
      (!zeroVol || e.capabilities.zero_volatility),
  );
}

export const ENGINE_LABELS: Record<string, string> = {
  bsm_analytic: "Closed-form BSM",
  crr_tree: "CRR binomial tree",
  mc_terminal_gbm: "Monte Carlo (terminal GBM)",
  heston_fourier: "Heston (Fourier)",
  lsm_american: "Longstaff–Schwartz MC",
};
