import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, type HedgingRequest, type HedgingResponse } from "../api/client";
import { Empty, ErrorBox, Field, Loading } from "../components/common";
import { isDecimalText, percentToDecimal, sig } from "../lib/decimal";
import { modelPayload, type InstrumentForm } from "../lib/instrument";
import { usePalette } from "../lib/theme";
import { useAction } from "../lib/useAction";

const STRATEGY_LABELS: Record<string, string> = {
  bsm: "Black-Scholes delta",
  heston: "Heston delta ∂C/∂S",
  heston_mv: "Minimum-variance delta",
};

const years = (asOf: string, expiry: string) =>
  (Date.parse(`${expiry}:00Z`) - Date.parse(`${asOf}:00Z`)) / (365 * 86400 * 1000);

/** Discrete delta hedging of a short option, in the world given by the model inputs. */
export function HedgingView({ form, valid }: { form: InstrumentForm; valid: boolean }) {
  const pal = usePalette();
  const heston = form.model === "heston";
  const [rebalances, setRebalances] = useState("16,32,64,128,256");
  const [hestonRebalances, setHestonRebalances] = useState("8,16,32,64");
  const [paths, setPaths] = useState("5000");
  const [hedgeVolPct, setHedgeVolPct] = useState("");
  const [strategies, setStrategies] = useState<string[]>(["bsm", "heston", "heston_mv"]);
  const [result, setResult] = useState<HedgingResponse | null>(null);
  const { busy, error, run } = useAction();

  const T = years(form.asOf, form.expiry);
  const blockers = [
    form.exercise === "american" && "The experiment hedges a European option; switch exercise to European.",
    form.dividends.length > 0 && "Cash dividends are not modelled by the simulator; remove them (a yield is fine).",
    !(T > 0) && "Expiry must be after the valuation time.",
  ].filter(Boolean) as string[];

  const go = async () => {
    const model = modelPayload(form);
    const chosen = heston ? strategies : ["bsm"];
    const body: HedgingRequest = {
      option_type: form.optionType,
      spot: Number(form.spot),
      strike: Number(form.strike),
      time: T,
      rate: Number(percentToDecimal(form.ratePct)),
      dividend_yield: Number(percentToDecimal(form.yieldPct)),
      world: heston ? "heston" : "gbm",
      real_vol: "volatility" in model ? model.volatility : 0.2,
      heston: heston && model.family === "heston" ? model : null,
      hedge_vol: isDecimalText(hedgeVolPct) ? Number(percentToDecimal(hedgeVolPct)) : null,
      strategies: chosen as HedgingRequest["strategies"],
      rebalances: (heston ? hestonRebalances : rebalances).split(",").map((x) => Math.trunc(Number(x))),
      paths: Math.trunc(Number(paths)),
    };
    const out = await run("hedge", () => api.hedging(body));
    setResult(out ?? null);
  };

  const strategiesRun = result ? [...new Set(result.rows.map((r) => r.strategy))] : [];
  const ns = result ? [...new Set(result.rows.map((r) => r.rebalances))].sort((a, b) => a - b) : [];
  const stdRows = ns.map((n) => {
    const row: Record<string, number | null> = { n };
    for (const s of strategiesRun) row[s] = result?.rows.find((r) => r.strategy === s && r.rebalances === n)?.std ?? null;
    row.theory = result?.rows.find((r) => r.strategy === "bsm" && r.rebalances === n)?.leading_order_std ?? null;
    return row;
  });

  return (
    <section className="panel" aria-labelledby="hedge-h">
      <h2 id="hedge-h">Delta-hedging experiment</h2>
      <p className="note">
        Sell the option on the left at its model price and hedge it in the underlying at N equally spaced times; P&amp;L
        is discounted to today. With the <strong>Black-Scholes</strong> model the paths are GBM at that volatility, and
        the hedging error shrinks like 1/√N (dashed line: the leading-order Gamma formula on the same paths). With the{" "}
        <strong>Heston</strong> model the paths have stochastic variance: delta hedging cannot remove the volatility risk,
        and the minimum-variance delta ∂C/∂S + (ρσ/S)·∂C/∂v also hedges the part of the variance move that is correlated
        with spot.
      </p>
      <div className="grid">
        {heston ? (
          <Field label="Rebalancing counts N" hint="Each must divide the largest (paths share one grid)">
            {(id, d) => <input id={id} aria-describedby={d} value={hestonRebalances} onChange={(e) => setHestonRebalances(e.target.value)} />}
          </Field>
        ) : (
          <Field label="Rebalancing counts N" hint="Each must divide the largest (paths share one grid)">
            {(id, d) => <input id={id} aria-describedby={d} value={rebalances} onChange={(e) => setRebalances(e.target.value)} />}
          </Field>
        )}
        <Field label="Paths" hint={heston ? "Heston deltas are Fourier prices per path and date" : "Exact GBM sampling"}>
          {(id, d) => <input id={id} aria-describedby={d} inputMode="numeric" value={paths} onChange={(e) => setPaths(e.target.value)} />}
        </Field>
        <Field label="Black-Scholes hedge vol" unit="%" hint="Blank: the model's own (implied) vol">
          {(id, d) => <input id={id} aria-describedby={d} inputMode="decimal" value={hedgeVolPct} onChange={(e) => setHedgeVolPct(e.target.value)} />}
        </Field>
      </div>
      {heston && (
        <fieldset>
          <legend>Hedge strategies</legend>
          {Object.entries(STRATEGY_LABELS).map(([key, label]) => (
            <label key={key} className="check">
              <input
                type="checkbox"
                checked={strategies.includes(key)}
                onChange={(e) => setStrategies(e.target.checked ? [...strategies, key] : strategies.filter((s) => s !== key))}
              />
              {label}
            </label>
          ))}
        </fieldset>
      )}
      {blockers.map((b) => (
        <p key={b} className="error-text">{b}</p>
      ))}
      <div className="actions">
        <button type="button" onClick={go} disabled={!valid || blockers.length > 0 || busy !== null}>
          {busy === "hedge" ? "Simulating…" : "Run hedging experiment"}
        </button>
      </div>
      {busy === "hedge" && <Loading label="Simulating paths and hedging…" />}
      <ErrorBox error={error} />
      {!result && !busy && !error && <Empty>Run the experiment to see how the hedging error depends on N and on the model.</Empty>}
      {result && (
        <div className="result">
          <p className="note">
            {result.world === "gbm" ? "GBM world" : "Heston world (Andersen QE paths)"} · option model price{" "}
            {sig(result.model_price, 6)} (implied vol {sig(result.model_implied_vol * 100, 4)}%) · Black-Scholes hedge vol{" "}
            {sig(result.hedge_vol * 100, 4)}% · {result.paths.toLocaleString()} paths on a {result.grid_steps}-step grid.
          </p>
          <figure className="chart">
            <figcaption>
              Standard deviation of the hedged P&amp;L against the number of rebalances (log–log).
              {Object.entries(result.slope).map(([s, v]) => ` ${STRATEGY_LABELS[s] ?? s}: slope ${sig(v, 3)}.`)}
            </figcaption>
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={stdRows} margin={{ top: 10, right: 20, bottom: 30, left: 10 }}>
                <CartesianGrid stroke={pal.grid} />
                <XAxis dataKey="n" type="number" scale="log" domain={["dataMin", "dataMax"]} ticks={ns} stroke={pal.ink2} label={{ value: "rebalances N", position: "bottom", fill: pal.ink2 }} />
                <YAxis type="number" scale="log" domain={["auto", "auto"]} stroke={pal.ink2} tickFormatter={(v: number) => sig(v, 2)} width={60} />
                <Tooltip formatter={(v) => sig(Number(v), 5)} labelFormatter={(l) => `N = ${l}`} />
                <Legend verticalAlign="top" />
                {strategiesRun.map((s, i) => (
                  <Line key={s} dataKey={s} name={STRATEGY_LABELS[s] ?? s} stroke={pal.series[i]} strokeWidth={2} isAnimationActive={false} />
                ))}
                {result.world === "gbm" && (
                  <Line dataKey="theory" name="leading-order prediction" stroke={pal.ink2} strokeDasharray="5 4" dot={false} isAnimationActive={false} />
                )}
              </LineChart>
            </ResponsiveContainer>
          </figure>
          <div className="small-multiples">
            {strategiesRun.map((s, i) => {
              const h = result.histograms[s];
              const bars = h.counts.map((c, j) => ({ x: 0.5 * (h.edges[j] + h.edges[j + 1]), c }));
              return (
                <figure className="chart" key={s}>
                  <figcaption>
                    <strong>{STRATEGY_LABELS[s] ?? s}</strong> <span className="muted">P&amp;L at N = {result.histogram_rebalances}</span>
                  </figcaption>
                  <ResponsiveContainer width="100%" height={180}>
                    <BarChart data={bars} margin={{ top: 6, right: 12, bottom: 6, left: 0 }}>
                      <CartesianGrid stroke={pal.grid} />
                      <XAxis dataKey="x" stroke={pal.ink2} tickFormatter={(v: number) => sig(v, 2)} />
                      <YAxis stroke={pal.ink2} width={40} />
                      <Tooltip formatter={(v) => String(v)} labelFormatter={(l) => `P&L ≈ ${sig(Number(l), 4)}`} />
                      <Bar dataKey="c" name="paths" fill={pal.series[i]} isAnimationActive={false} />
                    </BarChart>
                  </ResponsiveContainer>
                </figure>
              );
            })}
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col">Strategy</th>
                  <th scope="col" className="num">N</th>
                  <th scope="col" className="num">Mean ± SE</th>
                  <th scope="col" className="num">Std</th>
                  <th scope="col" className="num">5% / 95%</th>
                  <th scope="col" className="num">Leading-order std</th>
                  <th scope="col" className="num">Gamma identity mean</th>
                </tr>
              </thead>
              <tbody>
                {result.rows.map((r) => (
                  <tr key={`${r.strategy}-${r.rebalances}`}>
                    <td>{STRATEGY_LABELS[r.strategy] ?? r.strategy}</td>
                    <td className="num">{r.rebalances}</td>
                    <td className="num">{sig(r.mean, 4)} ± {sig(r.se_mean, 2)}</td>
                    <td className="num">{sig(r.std, 4)}</td>
                    <td className="num">{sig(r.q05, 4)} / {sig(r.q95, 4)}</td>
                    <td className="num">{sig(r.leading_order_std, 4)}</td>
                    <td className="num">{sig(r.vol_mismatch_identity_mean, 4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="note small">{result.note}</p>
        </div>
      )}
    </section>
  );
}
