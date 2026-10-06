import { useMemo, useState } from "react";
import { CartesianGrid, ComposedChart, ErrorBar, Legend, Line, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis } from "recharts";
import { api, type HestonArtifact, type HestonStats } from "../api/client";
import { pct, sig } from "../lib/decimal";
import { usePalette } from "../lib/theme";
import { useAction } from "../lib/useAction";
import { ErrorBox, Field, Loading } from "./common";

const PARAMS: [keyof NonNullable<HestonArtifact["parameters"]>, string][] = [
  ["v0", "v₀ (initial variance)"],
  ["kappa", "κ (mean reversion)"],
  ["theta", "θ (long-run variance)"],
  ["sigma", "σ (vol of variance)"],
  ["rho", "ρ (spot/variance correlation)"],
];

/** Heston calibration of the selected snapshot, compared with the market smile per expiry. */
export function HestonCalibrationPanel({ snapshotId, laterId }: { snapshotId: string; laterId: string }) {
  const pal = usePalette();
  const [art, setArt] = useState<HestonArtifact | null>(null);
  const [expiry, setExpiry] = useState("");
  const { busy, error, run } = useAction();

  const calibrate = async () => {
    const out = await run("heston", () => api.hestonCalibrate(snapshotId, laterId || null));
    if (!out) return;
    setArt(out.artifact);
    setExpiry(String(out.artifact.residuals?.[0]?.T ?? ""));
  };

  const expiries = useMemo(() => [...new Set((art?.residuals ?? []).map((r) => r.T))], [art]);
  const rows = (art?.residuals ?? []).filter((r) => String(r.T) === expiry && r.iv_mid !== null);
  const market = rows.map((r) => ({
    k: r.k,
    iv: r.iv_mid,
    err: r.iv_bid !== null && r.iv_ask !== null && r.iv_mid !== null ? [r.iv_mid - r.iv_bid, r.iv_ask - r.iv_mid] : [0, 0],
  }));
  const curve = [...rows].sort((a, b) => a.k - b.k).map((r) => ({ k: r.k, model_iv: r.model_iv }));
  const se = art?.uncertainty?.standard_errors ?? {};

  return (
    <section className="viz" aria-labelledby="heston-cal-h">
      <h3 id="heston-cal-h">Heston calibration (same snapshot)</h3>
      <p className="note">
        Same eligible quotes, parity forwards, held-out strikes and weights as the SSVI fit, so the two are comparable.
        Standard errors come from the Gauss–Newton covariance and include the uncertainty of the parity-implied forwards.
        With a later snapshot, the fit is evaluated with all parameters held and with only v₀ (the variance state)
        refitted.
      </p>
      <button type="button" onClick={calibrate} disabled={!snapshotId || busy !== null}>
        {busy === "heston" ? "Calibrating…" : "Calibrate Heston"}
      </button>
      {busy === "heston" && <Loading label="Least squares over five parameters from several starts…" />}
      <ErrorBox error={error} />
      {art && art.status === "failed" && (
        <div className="callout callout--error" role="alert">
          <strong>Calibration failed: {art.failure_reason?.replace(/_/g, " ")}</strong>
          <p>{art.failure_detail}</p>
        </div>
      )}
      {art && art.status === "ok" && art.parameters && (
        <div className="result">
          <div className="two-col">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Parameter</th>
                    <th scope="col" className="num">Estimate</th>
                    <th scope="col" className="num">Std. error</th>
                    {art.truth_recovery && <th scope="col" className="num">Error / SE vs truth</th>}
                  </tr>
                </thead>
                <tbody>
                  {PARAMS.map(([key, label]) => (
                    <tr key={key}>
                      <td>{label}</td>
                      <td className="num">{sig(art.parameters?.[key], 5)}</td>
                      <td className="num">{sig(se[key], 2)}</td>
                      {art.truth_recovery && (
                        <td className="num">{sig(art.truth_recovery.errors_in_standard_errors[key], 2)}</td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <dl className="kv">
              <div>
                <dt>Feller 2κθ/σ²</dt>
                <dd>
                  {sig(art.feller_ratio, 3)} {art.feller_satisfied ? "(satisfied)" : "(violated: variance can reach 0; reported, not rejected)"}
                </dd>
              </div>
              <div>
                <dt>Strongly correlated pairs</dt>
                <dd>
                  {art.uncertainty?.strongly_correlated_pairs.length
                    ? art.uncertainty.strongly_correlated_pairs.map(([a, b, c]) => `${a}/${b} (${sig(c, 2)})`).join(", ")
                    : "none (|corr| ≤ 0.9: the data separate all five)"}
                </dd>
              </div>
              <div><dt>Parameters at bounds</dt><dd>{art.bound_hits?.length ? art.bound_hits.join(", ") : "none"}</dd></div>
              <div>
                <dt>Optimizer</dt>
                <dd>{art.optimizer_runs?.filter((r) => r.success).length}/{art.optimizer_runs?.length} restarts converged</dd>
              </div>
            </dl>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col">Sample</th>
                  <th scope="col" className="num">Quotes</th>
                  <th scope="col" className="num">Inside bid/ask</th>
                  <th scope="col" className="num">IV RMSE (vol pts)</th>
                </tr>
              </thead>
              <tbody>
                <HestonRow label="In-sample (fitted)" s={art.in_sample} />
                <HestonRow label="Held-out strikes" s={art.held_out} />
                {art.later_snapshot && (
                  <>
                    <HestonRow label={`Later (+${sig(art.later_snapshot.elapsed_days, 3)} d), all parameters held`} s={art.later_snapshot.no_refit} />
                    <HestonRow
                      label={`Later, only v₀ refitted (v₀ = ${sig(art.later_snapshot.v0_refit.v0, 4)}), held-out strikes`}
                      s={art.later_snapshot.v0_refit.held_out}
                    />
                  </>
                )}
              </tbody>
            </table>
          </div>
          {art.truth_recovery && (
            <p className="note">
              Synthetic data generated by Heston: max |IV error| vs the generating model{" "}
              {sig(art.truth_recovery.max_abs_iv_error_vs_truth * 100, 3)} vol points.
            </p>
          )}
          <Field label="Expiry">
            {(id) => (
              <select id={id} value={expiry} onChange={(e) => setExpiry(e.target.value)}>
                {expiries.map((T) => (
                  <option key={T} value={String(T)}>
                    T = {sig(T, 4)} years
                  </option>
                ))}
              </select>
            )}
          </Field>
          <figure className="chart">
            <figcaption>Market mid implied vol with bid–ask range (dots) and the calibrated Heston smile (line).</figcaption>
            <ResponsiveContainer width="100%" height={300}>
              <ComposedChart margin={{ top: 10, right: 20, bottom: 30, left: 10 }}>
                <CartesianGrid stroke={pal.grid} />
                <XAxis type="number" dataKey="k" domain={["auto", "auto"]} stroke={pal.ink2} tickFormatter={(v: number) => sig(v, 2)} label={{ value: "k = ln(K/F)", position: "bottom", fill: pal.ink2 }} />
                <YAxis type="number" domain={["auto", "auto"]} stroke={pal.ink2} tickFormatter={(v: number) => pct(v, 3)} width={70} />
                <Tooltip formatter={(v) => pct(Number(v), 4)} labelFormatter={(l) => `k = ${sig(Number(l), 4)}`} />
                <Legend verticalAlign="top" />
                <Scatter data={market} dataKey="iv" name="market mid IV" fill={pal.ink2} isAnimationActive={false}>
                  <ErrorBar dataKey="err" direction="y" stroke={pal.ink2} width={3} />
                </Scatter>
                <Line data={curve} dataKey="model_iv" name="Heston" stroke={pal.series[1]} strokeWidth={2} dot={false} isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </figure>
        </div>
      )}
    </section>
  );
}

function HestonRow({ label, s }: { label: string; s?: HestonStats }) {
  if (!s || !s.n) return null;
  return (
    <tr>
      <td>{label}</td>
      <td className="num">{s.n}</td>
      <td className="num">{pct(s.bid_ask_containment, 3)}</td>
      <td className="num">{sig(s.iv_rmse_vol_points, 3)}</td>
    </tr>
  );
}
