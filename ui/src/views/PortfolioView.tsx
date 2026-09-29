import { useEffect, useState } from "react";
import {
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, type FitSummary, type PortfolioResponse } from "../api/client";
import { Empty, ErrorBox, Field, Loading } from "../components/common";
import { isDecimalText, percentToDecimal, sig } from "../lib/decimal";
import { instrumentPayload, type InstrumentForm } from "../lib/instrument";
import { usePalette, type Palette } from "../lib/theme";
import { useAction } from "../lib/useAction";

interface PositionRow {
  id: string;
  type: "call" | "put";
  exercise: "european" | "american";
  strike: string;
  expiry: string; // datetime-local UTC
  quantity: string;
  multiplier: string;
  volPct: string; // blank => from surface
}

const DEFAULT_BOOK: PositionRow[] = [
  { id: "long-call", type: "call", exercise: "european", strike: "42", expiry: "2026-12-28T20:00", quantity: "10", multiplier: "100", volPct: "20" },
  { id: "short-call", type: "call", exercise: "european", strike: "46", expiry: "2026-12-28T20:00", quantity: "-10", multiplier: "100", volPct: "19" },
  { id: "short-put", type: "put", exercise: "american", strike: "38", expiry: "2027-03-29T20:00", quantity: "-5", multiplier: "100", volPct: "23" },
];

const listOf = (s: string) =>
  s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean)
    .map(Number);

export function PortfolioView({ form, version }: { form: InstrumentForm; version: number }) {
  const pal = usePalette();
  const [book, setBook] = useState<PositionRow[]>(DEFAULT_BOOK);
  const [spots, setSpots] = useState("-10, -5, -2, 0, 2, 5, 10");
  const [vols, setVols] = useState("-5, 0, 5");
  const [rates, setRates] = useState("0");
  const [days, setDays] = useState("0, 7");
  const [dynamics, setDynamics] = useState<"sticky_strike" | "sticky_moneyness">("sticky_strike");
  const [fitId, setFitId] = useState("");
  const [fits, setFits] = useState<FitSummary[]>([]);
  const [res, setRes] = useState<PortfolioResponse | null>(null);
  const { busy: busyLabel, error, run: act } = useAction();
  const busy = busyLabel !== null;
  const [slice, setSlice] = useState({ rate: 0, days: 0 });

  useEffect(() => {
    api.fits().then((f) => setFits(f.filter((x) => x.status === "ok")), () => undefined);
  }, [version]);

  const set = (i: number, patch: Partial<PositionRow>) => setBook(book.map((p, j) => (j === i ? { ...p, ...patch } : p)));
  const listsOk = [spots, vols, rates, days].every((s) => listOf(s).every(Number.isFinite) && listOf(s).length > 0);
  const rowsOk = book.every(
    (p) => p.id && isDecimalText(p.strike) && isDecimalText(p.quantity) && isDecimalText(p.multiplier) && (p.volPct === "" ? !!fitId : isDecimalText(p.volPct)),
  );

  const run = async () => {
    const { valuation, market } = instrumentPayload(form);
    const out = await act("scenarios", () =>
      api.portfolio({
        schema_version: "1",
        valuation,
        market,
        fit_id: fitId || null,
        positions: book.map((p) => ({
          position_id: p.id,
          quantity: p.quantity,
          volatility: p.volPct === "" ? null : Number(percentToDecimal(p.volPct)),
          contract: {
            underlying: form.underlying,
            currency: form.currency,
            strike: p.strike,
            option_type: p.type,
            exercise_style: p.exercise,
            expiry: `${p.expiry}:00Z`,
            multiplier: p.multiplier,
          },
        })),
        scenarios: {
          spot_shocks_pct: listOf(spots),
          vol_shocks_pts: listOf(vols),
          rate_shocks_bp: listOf(rates),
          time_roll_days: listOf(days),
          surface_dynamics: dynamics,
        },
      }),
    );
    setRes(out ?? null);
    if (out) setSlice({ rate: listOf(rates)[0], days: listOf(days)[0] });
  };

  const sc = (res?.scenarios ?? []).filter((s) => s.rate_shock_bp === slice.rate && s.time_roll_days === slice.days);
  const spotAxis = [...new Set(sc.map((s) => s.spot_shock_pct))].sort((a, b) => a - b);
  const volAxis = [...new Set(sc.map((s) => s.vol_shock_pts))].sort((a, b) => b - a);
  const maxAbs = Math.max(1e-12, ...sc.map((s) => Math.abs(s.pnl)));
  const line = spotAxis.map((ds) => {
    const s = sc.find((x) => x.spot_shock_pct === ds && x.vol_shock_pts === 0) ?? sc.find((x) => x.spot_shock_pct === ds);
    return { ds, full: s?.pnl ?? null, approx: s?.approx_pnl ?? null, unexplained: s?.unexplained ?? null };
  });
  const g = res?.base.greeks_display as Record<string, number | null> | undefined;

  return (
    <section className="panel" aria-labelledby="pf-h">
      <h2 id="pf-h">Portfolio scenarios (full repricing)</h2>
      <p className="note">
        Uses the valuation time and market inputs from the left panel (spot {form.spot}, rate {form.ratePct}%, yield{" "}
        {form.yieldPct}%, underlying {form.underlying}, {form.currency}); the contract fields there are ignored. The
        book below is synthetic. Positions are not stored or logged by the server.
      </p>
      <div className="table-wrap">
        <table className="editable">
          <thead>
            <tr>
              <th scope="col">Id</th><th scope="col">Type</th><th scope="col">Exercise</th><th scope="col">Strike</th>
              <th scope="col">Expiry (UTC)</th><th scope="col">Qty (signed contracts)</th><th scope="col">Multiplier</th>
              <th scope="col">Vol % (blank = surface)</th><th scope="col" />
            </tr>
          </thead>
          <tbody>
            {book.map((p, i) => (
              <tr key={i}>
                <td><input aria-label={`Position ${i + 1} id`} value={p.id} onChange={(e) => set(i, { id: e.target.value })} /></td>
                <td>
                  <select aria-label={`Position ${i + 1} type`} value={p.type} onChange={(e) => set(i, { type: e.target.value as "call" | "put" })}>
                    <option value="call">call</option><option value="put">put</option>
                  </select>
                </td>
                <td>
                  <select aria-label={`Position ${i + 1} exercise`} value={p.exercise} onChange={(e) => set(i, { exercise: e.target.value as "european" | "american" })}>
                    <option value="european">European</option><option value="american">American</option>
                  </select>
                </td>
                <td><input aria-label={`Position ${i + 1} strike`} inputMode="decimal" value={p.strike} onChange={(e) => set(i, { strike: e.target.value })} /></td>
                <td><input aria-label={`Position ${i + 1} expiry`} type="datetime-local" value={p.expiry} onChange={(e) => set(i, { expiry: e.target.value })} /></td>
                <td><input aria-label={`Position ${i + 1} quantity`} inputMode="decimal" value={p.quantity} onChange={(e) => set(i, { quantity: e.target.value })} /></td>
                <td><input aria-label={`Position ${i + 1} multiplier`} inputMode="decimal" value={p.multiplier} onChange={(e) => set(i, { multiplier: e.target.value })} /></td>
                <td><input aria-label={`Position ${i + 1} volatility percent`} inputMode="decimal" value={p.volPct} onChange={(e) => set(i, { volPct: e.target.value })} /></td>
                <td><button type="button" className="link" onClick={() => setBook(book.filter((_, j) => j !== i))}>Remove</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button type="button" className="secondary" onClick={() => setBook([...book, { ...DEFAULT_BOOK[0], id: `pos-${book.length + 1}` }])}>
        Add position
      </button>
      <fieldset>
        <legend>Scenario grid</legend>
        <div className="grid">
          <Field label="Spot shocks" unit="% of spot, comma-separated">{(id) => <input id={id} value={spots} onChange={(e) => setSpots(e.target.value)} />}</Field>
          <Field label="Vol shocks" unit="vol points">{(id) => <input id={id} value={vols} onChange={(e) => setVols(e.target.value)} />}</Field>
          <Field label="Rate shocks" unit="basis points">{(id) => <input id={id} value={rates} onChange={(e) => setRates(e.target.value)} />}</Field>
          <Field label="Time roll" unit="calendar days">{(id) => <input id={id} value={days} onChange={(e) => setDays(e.target.value)} />}</Field>
          <Field label="Surface dynamics" hint="Sticky moneyness needs a fitted surface">
            {(id, d) => (
              <select id={id} aria-describedby={d} value={dynamics} onChange={(e) => setDynamics(e.target.value as typeof dynamics)}>
                <option value="sticky_strike">Sticky strike (vol per position fixed)</option>
                <option value="sticky_moneyness">Sticky moneyness (vol re-read at new forward moneyness)</option>
              </select>
            )}
          </Field>
          <Field label="Surface fit" hint="Supplies vols for blank rows">
            {(id, d) => (
              <select id={id} aria-describedby={d} value={fitId} onChange={(e) => setFitId(e.target.value)}>
                <option value="">None</option>
                {fits.map((f) => (
                  <option key={f.fit_id} value={f.fit_id}>{f.fit_id}{f.stale ? " (stale)" : ""}</option>
                ))}
              </select>
            )}
          </Field>
        </div>
        {!listsOk && <p className="error-text">Shock lists must be comma-separated numbers.</p>}
        {!rowsOk && <p className="error-text">Every position needs id, numeric strike/quantity/multiplier, and a vol unless a surface fit is selected.</p>}
      </fieldset>
      <div className="actions">
        <button type="button" onClick={run} disabled={busy || !listsOk || !rowsOk}>{busy ? "Repricing…" : "Run scenarios"}</button>
      </div>
      {busy && <Loading label="Full repricing of every position in every scenario…" />}
      <ErrorBox error={error} />
      {!res && !busy && !error && <Empty>Run the scenario grid to see P&amp;L by spot and volatility shock.</Empty>}
      {res && g && (
        <div className="result">
          <div className="hero">
            <div>
              <div className="hero__label">Portfolio value</div>
              <div className="hero__value">{sig(res.base.value as number, 8)} <span className="unit">{String(res.base.currency)}</span></div>
            </div>
          </div>
          <dl className="kv">
            <div><dt>Delta (units of underlying)</dt><dd>{sig(g.delta_units_of_underlying, 6)}</dd></div>
            <div><dt>Delta notional</dt><dd>{sig(g.delta_notional, 6)}</dd></div>
            <div><dt>Gamma (units per 1.0 spot)</dt><dd>{sig(g.gamma_units_per_1_spot, 6)}</dd></div>
            <div><dt>Vega per vol point</dt><dd>{sig(g.vega_per_vol_point, 6)}</dd></div>
            <div><dt>Theta per calendar day</dt><dd>{sig(g.theta_per_calendar_day, 6)}</dd></div>
            <div><dt>Rho per 1%</dt><dd>{sig(g.rho_per_1pct, 6)}</dd></div>
          </dl>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Position</th><th>Contract</th><th className="num">Qty × mult</th><th className="num">Vol</th><th>Engine</th><th className="num">Price/unit</th><th className="num">Value</th></tr></thead>
              <tbody>
                {res.positions.map((p) => (
                  <tr key={p.position_id}><th scope="row">{p.position_id}</th><td className="small">{p.contract}</td><td className="num">{p.quantity} × {p.multiplier}</td><td className="num">{sig(p.volatility * 100, 4)}%</td><td>{p.engine}</td><td className="num">{sig(p.price_per_unit, 8)}</td><td className="num">{sig(p.value, 8)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="grid">
            <Field label="Rate shock slice (bp)">
              {(id) => (
                <select id={id} value={slice.rate} onChange={(e) => setSlice({ ...slice, rate: Number(e.target.value) })}>
                  {[...new Set(res.scenarios.map((s) => s.rate_shock_bp))].map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
              )}
            </Field>
            <Field label="Time roll slice (days)">
              {(id) => (
                <select id={id} value={slice.days} onChange={(e) => setSlice({ ...slice, days: Number(e.target.value) })}>
                  {[...new Set(res.scenarios.map((s) => s.time_roll_days))].map((d) => <option key={d} value={d}>{d}</option>)}
                </select>
              )}
            </Field>
          </div>
          <figure className="chart">
            <figcaption>P&amp;L vs base ({String(res.base.currency)}) by spot shock (columns) and vol shock (rows). Blue = gain, red = loss.</figcaption>
            <div className="table-wrap">
              <table className="heatmap">
                <thead>
                  <tr><th scope="col">vol \ spot</th>{spotAxis.map((ds) => <th scope="col" key={ds} className="num">{ds > 0 ? "+" : ""}{ds}%</th>)}</tr>
                </thead>
                <tbody>
                  {volAxis.map((dv) => (
                    <tr key={dv}>
                      <th scope="row">{dv > 0 ? "+" : ""}{dv} pts</th>
                      {spotAxis.map((ds) => {
                        const s = sc.find((x) => x.spot_shock_pct === ds && x.vol_shock_pts === dv);
                        if (!s) return <td key={ds}>—</td>;
                        const { bg, fg } = diverging(s.pnl / maxAbs, pal);
                        return (
                          <td key={ds} className="num" style={{ background: bg, color: fg }} title={s.positions_settled ? `${s.positions_settled} position(s) settled at intrinsic` : undefined}>
                            {sig(s.pnl, 4)}{s.positions_settled ? " *" : ""}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {sc.some((s) => s.positions_settled) && <p className="small">* scenario rolls past an expiry: that position settles at intrinsic value.</p>}
          </figure>
          <figure className="chart">
            <figcaption>Full repricing P&amp;L vs delta-gamma-vega-theta-rho approximation (vol shock 0). The gap is the unexplained residual.</figcaption>
            <ResponsiveContainer width="100%" height={300}>
              <ComposedChart data={line} margin={{ top: 10, right: 20, bottom: 30, left: 10 }}>
                <CartesianGrid stroke={pal.grid} />
                <XAxis type="number" dataKey="ds" domain={["dataMin", "dataMax"]} stroke={pal.ink2} tickFormatter={(v: number) => `${v}%`} label={{ value: "spot shock", position: "bottom", fill: pal.ink2 }} />
                <YAxis stroke={pal.ink2} tickFormatter={(v: number) => sig(v, 3)} width={80} />
                <Tooltip formatter={(v) => (v === null ? "not meaningful" : sig(Number(v), 6))} labelFormatter={(l) => `spot shock ${l}%`} />
                <Legend verticalAlign="top" />
                <Line dataKey="full" name="full repricing" stroke={pal.series[0]} strokeWidth={2} dot={{ r: 4, fill: pal.series[0], stroke: pal.surface, strokeWidth: 2 }} isAnimationActive={false} />
                <Line dataKey="approx" name="Greek approximation" stroke={pal.series[1]} strokeWidth={2} dot={{ r: 4, fill: pal.series[1], stroke: pal.surface, strokeWidth: 2 }} isAnimationActive={false} connectNulls={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </figure>
          <details>
            <summary>Assumptions</summary>
            <dl className="kv">{Object.entries(res.assumptions).map(([k, v]) => <div key={k}><dt>{k.replace(/_/g, " ")}</dt><dd>{String(v)}</dd></div>)}</dl>
          </details>
          <details>
            <summary>All scenarios ({res.scenarios.length})</summary>
            <div className="table-wrap">
              <table>
                <thead><tr><th className="num">spot %</th><th className="num">vol pts</th><th className="num">rate bp</th><th className="num">days</th><th className="num">value</th><th className="num">P&amp;L</th><th className="num">approx</th><th className="num">unexplained</th><th className="num">settled</th></tr></thead>
                <tbody>
                  {res.scenarios.map((s, i) => (
                    <tr key={i}><td className="num">{s.spot_shock_pct}</td><td className="num">{s.vol_shock_pts}</td><td className="num">{s.rate_shock_bp}</td><td className="num">{s.time_roll_days}</td><td className="num">{sig(s.value, 8)}</td><td className="num">{sig(s.pnl, 6)}</td><td className="num">{s.approx_pnl == null ? "n/a" : sig(s.approx_pnl, 6)}</td><td className="num">{s.unexplained == null ? "n/a" : sig(s.unexplained, 4)}</td><td className="num">{s.positions_settled}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </div>
      )}
    </section>
  );
}

/** Diverging blue (gain) / red (loss) scale with a neutral midpoint; text colour by luminance. */
function diverging(t: number, pal: Palette): { bg: string; fg: string } {
  const dark = pal.surface !== "#fcfcfb";
  const mid = dark ? [0x38, 0x38, 0x35] : [0xf0, 0xef, 0xec];
  const pos = dark ? [0x39, 0x87, 0xe5] : [0x2a, 0x78, 0xd6];
  const neg = dark ? [0xe6, 0x67, 0x67] : [0xe3, 0x49, 0x48];
  const end = t >= 0 ? pos : neg;
  const a = Math.min(Math.abs(t), 1);
  const c = mid.map((m, i) => Math.round(m + (end[i] - m) * a));
  const lum = (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255;
  return { bg: `rgb(${c.join(",")})`, fg: lum > 0.55 ? "#0b0b0b" : "#ffffff" };
}
