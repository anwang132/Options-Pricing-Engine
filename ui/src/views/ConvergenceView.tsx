import { useState } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, type MCConvergenceResponse, type TreeConvergenceResponse } from "../api/client";
import { Empty, ErrorBox, Loading } from "../components/common";
import { sig } from "../lib/decimal";
import { instrumentPayload, modelPayload, type InstrumentForm } from "../lib/instrument";
import { usePalette } from "../lib/theme";
import { useAction } from "../lib/useAction";

const TREE_STEPS = [10, 11, 20, 21, 40, 41, 80, 81, 160, 161, 320, 321, 640, 641, 1280, 1281, 2560, 2561];
const MC_PATHS = [2000, 5000, 10000, 20000, 50000, 100000, 200000, 500000, 1000000];

export function ConvergenceView({ form, valid }: { form: InstrumentForm; valid: boolean }) {
  const pal = usePalette();
  const [tree, setTree] = useState<TreeConvergenceResponse | null>(null);
  const [mc, setMc] = useState<MCConvergenceResponse | null>(null);
  const { busy, error, run: act } = useAction();
  const [mcOpts, setMcOpts] = useState({ antithetic: true, control_variate: "none" as "none" | "terminal_underlying", seed: 20260928 });

  const base = () => ({ schema_version: "1" as const, ...instrumentPayload(form), model: modelPayload(form), greeks: [] });

  const runTree = async () => {
    const out = await act("tree", () => api.treeConvergence({ ...base(), steps: TREE_STEPS }));
    if (out) setTree(out);
  };
  const runMc = async () => {
    const out = await act("mc", () => api.mcConvergence({ ...base(), path_counts: MC_PATHS, ...mcOpts }));
    if (out) setMc(out);
  };

  const treeRef = tree?.reference_price ?? null;
  const treeSeries = (odd: boolean) =>
    (tree?.points ?? [])
      .filter((p) => p.price !== null && (p.steps % 2 === 1) === odd)
      .map((p) => ({
        steps: p.steps,
        value: treeRef !== null ? Math.max(Math.abs(p.error_vs_reference ?? 0), 1e-16) : (p.price as number),
      }));
  const mcData = (mc?.points ?? []).map((p) => ({ paths: p.paths, price: p.price, band: [p.ci_low, p.ci_high] as [number, number], covers: p.covers_reference }));

  return (
    <section className="panel" aria-labelledby="conv-h">
      <h2 id="conv-h">Convergence and uncertainty</h2>
      <ErrorBox error={error} />
      <div className="two-col">
        <div>
          <h3>CRR tree: error vs steps</h3>
          <p className="note">
            Odd and even step counts converge along different paths; convergence need not be monotone. Error is measured
            against the closed-form price (European only; American shows raw prices).
          </p>
          <button type="button" onClick={runTree} disabled={!valid || busy !== null}>
            {busy === "tree" ? "Running…" : "Run tree study"}
          </button>
          {busy === "tree" && <Loading label="Building trees…" />}
          {!tree && busy !== "tree" && <Empty>Run the study to plot error against steps.</Empty>}
          {tree && (
            <figure className="chart">
              <figcaption>{treeRef !== null ? "|CRR − closed form| (log scale) vs tree steps (log scale)" : "CRR price vs steps (no closed-form reference)"}</figcaption>
              <ResponsiveContainer width="100%" height={300}>
                <LineChart margin={{ top: 10, right: 20, bottom: 30, left: 10 }}>
                  <CartesianGrid stroke={pal.grid} />
                  <XAxis type="number" dataKey="steps" scale="log" domain={["dataMin", "dataMax"]} stroke={pal.ink2} label={{ value: "steps N", position: "bottom", fill: pal.ink2 }} allowDuplicatedCategory={false} />
                  <YAxis type="number" dataKey="value" scale={treeRef !== null ? "log" : "auto"} domain={["auto", "auto"]} stroke={pal.ink2} tickFormatter={(v: number) => sig(v, 2)} width={70} />
                  <Tooltip formatter={(v) => sig(Number(v), 4)} labelFormatter={(l) => `N = ${l}`} />
                  <Legend verticalAlign="top" />
                  <Line data={treeSeries(false)} dataKey="value" name="even N" stroke={pal.series[0]} strokeWidth={2} dot={{ r: 4, fill: pal.series[0], stroke: pal.surface, strokeWidth: 2 }} isAnimationActive={false} />
                  <Line data={treeSeries(true)} dataKey="value" name="odd N" stroke={pal.series[1]} strokeWidth={2} dot={{ r: 4, fill: pal.series[1], stroke: pal.surface, strokeWidth: 2 }} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
              <details>
                <summary>Data table</summary>
                <table>
                  <thead>
                    <tr><th>steps</th><th className="num">price</th><th className="num">error vs reference</th><th>note</th></tr>
                  </thead>
                  <tbody>
                    {tree.points.map((p) => (
                      <tr key={p.steps}><td>{p.steps}</td><td className="num">{sig(p.price, 10)}</td><td className="num">{sig(p.error_vs_reference, 3)}</td><td>{p.failure ?? ""}</td></tr>
                    ))}
                  </tbody>
                </table>
              </details>
            </figure>
          )}
        </div>
        <div>
          <h3>Monte Carlo: estimate and 95% CI vs paths</h3>
          <div className="grid">
            <label className="check">
              <input type="checkbox" checked={mcOpts.antithetic} onChange={(e) => setMcOpts({ ...mcOpts, antithetic: e.target.checked })} /> Antithetic
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={mcOpts.control_variate === "terminal_underlying"}
                onChange={(e) => setMcOpts({ ...mcOpts, control_variate: e.target.checked ? "terminal_underlying" : "none" })}
              />{" "}
              Control variate
            </label>
          </div>
          <button type="button" onClick={runMc} disabled={!valid || busy !== null || form.exercise !== "european"}>
            {busy === "mc" ? "Running…" : "Run Monte Carlo study"}
          </button>
          {form.exercise !== "european" && <p className="note">Terminal Monte Carlo supports European exercise only.</p>}
          {busy === "mc" && <Loading label="Simulating…" />}
          {!mc && busy !== "mc" && <Empty>Run the study to see the estimate narrow as paths grow.</Empty>}
          {mc && (
            <figure className="chart">
              <figcaption>Estimate with 95% confidence band; the line is the closed-form reference. {mc.note}</figcaption>
              <ResponsiveContainer width="100%" height={300}>
                <ComposedChart data={mcData} margin={{ top: 10, right: 20, bottom: 30, left: 10 }}>
                  <CartesianGrid stroke={pal.grid} />
                  <XAxis type="number" dataKey="paths" scale="log" domain={["dataMin", "dataMax"]} stroke={pal.ink2} tickFormatter={(v: number) => sig(v, 2)} label={{ value: "payoff evaluations", position: "bottom", fill: pal.ink2 }} />
                  <YAxis domain={["auto", "auto"]} stroke={pal.ink2} tickFormatter={(v: number) => sig(v, 4)} width={70} />
                  <Tooltip
                    content={({ payload }) => {
                      const p = payload?.[0]?.payload as (typeof mcData)[number] | undefined;
                      if (!p) return null;
                      return (
                        <div className="tooltip">
                          <strong>{p.paths.toLocaleString()} paths</strong>
                          <div>estimate {sig(p.price, 7)}</div>
                          <div>95% CI [{sig(p.band[0], 7)}, {sig(p.band[1], 7)}]</div>
                          {p.covers !== null && <div>{p.covers ? "covers" : "misses"} the reference</div>}
                        </div>
                      );
                    }}
                  />
                  <Area dataKey="band" fill={pal.series[0]} fillOpacity={0.12} stroke="none" isAnimationActive={false} />
                  <Line dataKey="price" stroke={pal.series[0]} strokeWidth={2} dot={{ r: 4, fill: pal.series[0], stroke: pal.surface, strokeWidth: 2 }} isAnimationActive={false} />
                  {mc.reference_price !== null && <ReferenceLine y={mc.reference_price} stroke={pal.ink2} label={{ value: "closed form", fill: pal.ink2, position: "insideTopRight" }} />}
                </ComposedChart>
              </ResponsiveContainer>
              <details>
                <summary>Data table</summary>
                <table>
                  <thead>
                    <tr><th className="num">paths</th><th className="num">estimate</th><th className="num">SE</th><th>covers reference</th></tr>
                  </thead>
                  <tbody>
                    {mc.points.map((p) => (
                      <tr key={p.paths}><td className="num">{p.paths.toLocaleString()}</td><td className="num">{sig(p.price, 8)}</td><td className="num">{sig(p.standard_error, 3)}</td><td>{p.covers_reference === null ? "—" : p.covers_reference ? "yes" : "no"}</td></tr>
                    ))}
                  </tbody>
                </table>
              </details>
            </figure>
          )}
        </div>
      </div>
    </section>
  );
}
