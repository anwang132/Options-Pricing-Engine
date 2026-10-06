/**
 * Decimal-text helpers. Unit conversion (percent -> decimal) is done on the
 * decimal string so no binary floating-point rounding is introduced before the
 * value reaches the API. No pricing logic lives in the frontend.
 */

const DECIMAL = /^[+-]?(\d+\.?\d*|\.\d+)$/;

export function isDecimalText(s: string): boolean {
  return DECIMAL.test(s.trim());
}

/** "5.25" (percent) -> "0.0525" (decimal); exact string shift by two places. */
export function percentToDecimal(text: string): string {
  const s = text.trim();
  if (!isDecimalText(s)) throw new Error(`not a number: ${text}`);
  const neg = s.startsWith("-");
  const body = s.replace(/^[+-]/, "");
  const [intPart, frac = ""] = body.split(".");
  const digits = (intPart || "0") + frac;
  const pointPos = (intPart || "0").length - 2;
  let out: string;
  if (pointPos > 0) {
    out = digits.slice(0, pointPos) + (digits.length > pointPos ? "." + digits.slice(pointPos) : "");
  } else {
    out = "0." + "0".repeat(-pointPos) + digits;
  }
  out = out.replace(/^0+(?=\d)/, "");
  if (out.startsWith(".")) out = "0" + out;
  return (neg && /[1-9]/.test(out) ? "-" : "") + out;
}

/** Display a float with a fixed number of significant digits; never used for computation. */
export function sig(x: number | null | undefined, digits = 6): string {
  if (x === null || x === undefined) return "—";
  if (!Number.isFinite(x)) return String(x);
  if (x === 0) return "0";
  const abs = Math.abs(x);
  if (abs >= 1e-4 && abs < 1e7) return Number(x.toPrecision(digits)).toString();
  return x.toExponential(digits - 1);
}

export function pct(x: number | null | undefined, digits = 4): string {
  if (x === null || x === undefined) return "—";
  return `${sig(x * 100, digits)}%`;
}
