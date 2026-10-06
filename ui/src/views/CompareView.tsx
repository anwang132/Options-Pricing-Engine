import { useState } from "react";
import { CartesianGrid, ErrorBar, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { api, type CompareResponse } from "../api/client";
import { Empty, ErrorBox, Loading, humanCode } from "../components/common";
import { sig } from "../lib/decimal";
import { ENGINE_LABELS, instrumentPayload, modelPayload, type InstrumentForm } from "../lib/instrument";
import { usePalette } from "../lib/theme";
import { useAction } from "../lib/useAction";

export function CompareView({ form, valid }: { form: InstrumentForm; valid: boolean }) {
  const [res, setRes] = useState<CompareResponse | null>(null);
  const { busy, error, run: act } = useAction();
  const pal = usePalette();

  const run = async () =>
    setRes(
      (await act("compare", () =>
        api.compare({ schema_version: "1", ...instrumentPayload(form), model: modelPayload(form) }),
      )) ?? null,
    );

  const points =
    res?.comparisons
      .filter((c) => c.result && c.difference_vs_reference !== null && c.difference_vs_reference !== undefined)
      .map((c, i) => {
        const u = c.result!.uncertainty;
        return {
          engine: ENGINE_LABELS[c.engine_id] ?? c.engine_id,
          y: i,
          diff: c.difference_vs_reference as number,
          ci: u ? (u.ci_high - u.ci_low) / 2 : 0,
          hasCi: !!u,
        };
      }) ?? [];

  return (
    <section className="panel" aria-labelledby="cmp-h">
      <h2 id="cmp-h">Cross-engine comparison</h2>
      <p className="note">
        Runs every engine whose declared capabilities cover these inputs, with default settings (CRR 1000 steps; MC
        200,000 antithetic paths, seed 20260928). Differences are explained as sampling error (Monte Carlo, in
        standard errors) or discretisation error (tree, vs its odd/even spread).
      </p>
      <button type="button" onClick={run} disabled={!valid || busy !== null}>
        {busy ? "Running…" : "Compare engines"}
      </button>
      {busy && <Loading label="Running all compatible engines…" />}
      <ErrorBox error={error} />
      {!res && !busy && !error && <Empty>Compare the engines on the current inputs.</Empty>}
      {res && (
        <>
          <p className="callout">{res.reference_note}</p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col">Engine</th>
                  <th scope="col" className="num">Price</th>
                  <th scope="col" className="num">Difference vs reference</th>
                  <th scope="col" className="num">Std. error</th>
                  <th scope="col">Interpretation</th>
                  <th scope="col" className="num">Runtime</th>
                </tr>
              </thead>
              <tbody>
                {res.comparisons.map((c) => (
                  <tr key={c.engine_id}>
                    <th scope="row">{ENGINE_LABELS[c.engine_id] ?? c.engine_id}</th>
                    <td className="num">{c.result ? sig(c.result.price, 10) : "—"}</td>
                    <td className="num">{c.difference_vs_reference == null ? (c.engine_id === res.reference_engine ? "reference" : "—") : sig(c.difference_vs_reference, 3)}</td>
                    <td className="num">{c.result?.uncertainty ? sig(c.result.uncertainty.standard_error, 3) : "—"}</td>
                    <td>{c.error ? `${humanCode(c.error.code)}: ${c.error.message}` : c.interpretation ?? "—"}</td>
                    <td className="num">{c.result ? `${sig(c.result.elapsed_seconds * 1000, 3)} ms` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {points.length > 0 && (
            <figure className="chart">
              <figcaption>
                Difference from the closed-form price (price units). Monte Carlo shows a 95% confidence interval; the tree
                shows a point (its error is deterministic).
              </figcaption>
              <ResponsiveContainer width="100%" height={60 + points.length * 50}>
                <ScatterChart margin={{ top: 10, right: 30, bottom: 30, left: 10 }}>
                  <CartesianGrid stroke={pal.grid} horizontal={false} />
                  <XAxis type="number" dataKey="diff" stroke={pal.ink2} tickFormatter={(v: number) => sig(v, 2)} domain={["auto", "auto"]} label={{ value: "engine − closed form", position: "bottom", fill: pal.ink2 }} />
                  <YAxis type="number" dataKey="y" stroke={pal.ink2} width={180} ticks={points.map((p) => p.y)} domain={[-0.5, points.length - 0.5]} tickFormatter={(v: number) => points[v]?.engine ?? ""} />
                  <ZAxis range={[160, 160]} />
                  <ReferenceLine x={0} stroke={pal.ink2} />
                  <Tooltip
                    cursor={false}
                    content={({ payload }) => {
                      const p = payload?.[0]?.payload as (typeof points)[number] | undefined;
                      if (!p) return null;
                      return (
                        <div className="tooltip">
                          <strong>{p.engine}</strong>
                          <div>difference {sig(p.diff, 4)}</div>
                          {p.hasCi && <div>95% CI half-width {sig(p.ci, 3)}</div>}
                        </div>
                      );
                    }}
                  />
                  <Scatter data={points} fill={pal.series[0]} stroke={pal.surface} strokeWidth={2} isAnimationActive={false}>
                    <ErrorBar dataKey="ci" direction="x" width={10} stroke={pal.series[0]} strokeWidth={2} isAnimationActive={false} />
                  </Scatter>
                </ScatterChart>
              </ResponsiveContainer>
            </figure>
          )}
        </>
      )}
    </section>
  );
}
