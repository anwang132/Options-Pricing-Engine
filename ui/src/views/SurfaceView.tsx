import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  ComposedChart,
  ErrorBar,
  Legend,
  Line,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, type FitArtifact, type FitStats, type FitSummary, type SnapshotSummary } from "../api/client";
import { Empty, ErrorBox, Field, Loading, StatusBadge } from "../components/common";
import { HestonCalibrationPanel } from "../components/HestonCalibrationPanel";
import { SurfaceCharts } from "../components/SurfaceCharts";
import { pct, sig } from "../lib/decimal";
import { useAction } from "../lib/useAction";
import { usePalette } from "../lib/theme";

export function SurfaceView({ version, onFitsChanged }: { version: number; onFitsChanged?: () => void }) {
  const pal = usePalette();
  const [snaps, setSnaps] = useState<SnapshotSummary[] | null>(null);
  const [fits, setFits] = useState<FitSummary[]>([]);
  const [snapshotId, setSnapshotId] = useState("");
  const [laterId, setLaterId] = useState("");
  const [artifact, setArtifact] = useState<FitArtifact | null>(null);
  const [expiry, setExpiry] = useState<string>("");
  const { busy: busyLabel, error, setError, run: act } = useAction();
  const busy = busyLabel === "fit";

  const refresh = useCallback(async () => {
    try {
      const [s, f] = await Promise.all([api.snapshots(), api.fits()]);
      setSnaps(s);
      setFits(f);
      setSnapshotId((cur) => cur || s[0]?.snapshot_id || "");
    } catch (e) {
      setError(e);
    }
  }, [setError]);
  useEffect(() => {
    void refresh();
  }, [refresh, version]);

  const show = (a: FitArtifact) => {
    setArtifact(a);
    setExpiry(a.surface ? String(a.residuals?.[0]?.T ?? "") : "");
  };

  const fit = async () => {
    const out = await act("fit", () => api.fit(snapshotId, laterId || null));
    if (!out) return;
    show(out.artifact);
    await refresh();
    onFitsChanged?.();
  };

  const openFit = async (id: string) => {
    const out = await act("open", () => api.fitArtifact(id));
    if (out) show(out);
  };

  const expiries = useMemo(() => [...new Set((artifact?.residuals ?? []).map((r) => r.T))], [artifact]);
  const rows = (artifact?.residuals ?? []).filter((r) => String(r.T) === expiry);
  const point = (r: (typeof rows)[number]) => ({
    k: r.k,
    iv: r.iv_mid,
    err: r.iv_bid !== null && r.iv_ask !== null && r.iv_mid !== null ? [r.iv_mid - r.iv_bid, r.iv_ask - r.iv_mid] : [0, 0],
    strike: r.strike,
    type: r.type,
    bid: r.bid,
    ask: r.ask,
    model: r.model,
    model_iv: r.model_iv,
    inside: r.inside_bid_ask,
  });
  const inSample = rows.filter((r) => !r.held_out && r.iv_mid !== null).map(point);
  const heldOut = rows.filter((r) => r.held_out && r.iv_mid !== null).map(point);
  const curve = [...rows].sort((a, b) => a.k - b.k).map((r) => ({ k: r.k, model_iv: r.model_iv }));

  return (
    <section className="panel" aria-labelledby="surf-h">
      <h2 id="surf-h">Volatility surface calibration (SSVI)</h2>
      <p className="note">
        Fits European quotes only (American quotes are excluded with a reason). Forwards come from put-call parity per
        expiry. Price-space least squares with spread-based weights floored at 0.05 or 0.5% of mid; every 4th strike is
        held out. Each fit is stored immutably, including failed fits.
      </p>
      {snaps && snaps.length === 0 && <Empty>No snapshots yet — load them in the Market data tab first.</Empty>}
      {snaps && snaps.length > 0 && (
        <div className="grid">
          <Field label="Snapshot to fit">
            {(id) => (
              <select id={id} value={snapshotId} onChange={(e) => setSnapshotId(e.target.value)}>
                {snaps.map((s) => (
                  <option key={s.snapshot_id} value={s.snapshot_id}>
                    {s.snapshot_id} · {s.as_of.slice(0, 16)} · {sourceLabel(s)}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <Field label="Later snapshot (optional)" hint="Evaluated with the surface held fixed — no refit">
            {(id, d) => (
              <select id={id} aria-describedby={d} value={laterId} onChange={(e) => setLaterId(e.target.value)}>
                <option value="">None</option>
                {snaps
                  .filter((s) => s.snapshot_id !== snapshotId)
                  .map((s) => (
                    <option key={s.snapshot_id} value={s.snapshot_id}>
                      {s.snapshot_id} · {s.as_of.slice(0, 16)} · {sourceLabel(s)}
                    </option>
                  ))}
              </select>
            )}
          </Field>
        </div>
      )}
      <div className="actions">
        <button type="button" onClick={fit} disabled={busy || !snapshotId}>
          {busy ? "Fitting…" : "Fit surface"}
        </button>
      </div>
      {busy && <Loading label="Calibrating…" />}
      <ErrorBox error={error} />
      {fits.length > 0 && (
        <details>
          <summary>Fit history ({fits.length})</summary>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col">Fit</th>
                  <th scope="col">Status</th>
                  <th scope="col">Created (UTC)</th>
                  <th scope="col">Snapshot as of</th>
                  <th scope="col">Held-out containment</th>
                  <th scope="col">Freshness</th>
                </tr>
              </thead>
              <tbody>
                {fits.map((f) => (
                  <tr key={f.fit_id}>
                    <th scope="row">
                      <button type="button" className="link" onClick={() => void openFit(f.fit_id)}>
                        {f.fit_id}
                      </button>
                    </th>
                    <td><StatusBadge status={f.status === "ok" ? "ok" : "failed"} /> {f.failure_reason ?? ""}</td>
                    <td>{f.created_at}</td>
                    <td>{f.snapshot_as_of}</td>
                    <td>{f.held_out_containment === null ? "—" : pct(f.held_out_containment, 3)}</td>
                    <td>{f.stale ? <span className="badge badge--warn">! stale: newer snapshot exists</span> : "latest data"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
      {!artifact && !busy && <Empty>Fit a snapshot or open a previous fit.</Empty>}
      {artifact && artifact.status === "failed" && (
        <div className="callout callout--error" role="alert">
          <strong>Fit failed: {artifact.failure_reason?.replace(/_/g, " ")}</strong>
          <p>{artifact.failure_detail}</p>
          <p>
            The failure is stored as artifact <code>{artifact.fit_id}</code>. Earlier fits are unchanged and keep their
            original timestamps; none is substituted silently.
          </p>
          <ExclusionList exclusions={artifact.exclusions} />
        </div>
      )}
      {artifact && artifact.status === "ok" && artifact.surface && (
        <div className="result">
          <h3>
            Fit <code>{artifact.fit_id}</code> of <code>{artifact.snapshot_id}</code>
          </h3>
          <dl className="kv">
            <div><dt>ρ (skew)</dt><dd>{sig(artifact.surface.rho, 5)}</dd></div>
            <div><dt>η</dt><dd>{sig(artifact.surface.eta, 5)}</dd></div>
            <div><dt>γ</dt><dd>{sig(artifact.surface.gamma, 5)}</dd></div>
            <div><dt>Parameters at bounds</dt><dd>{artifact.bound_hits?.length ? artifact.bound_hits.join(", ") : "none"}</dd></div>
            <div><dt>Created (UTC)</dt><dd>{artifact.created_at}</dd></div>
            <div><dt>Optimizer</dt><dd>{artifact.optimizer_runs?.filter((r) => r.success).length}/{artifact.optimizer_runs?.length} restarts converged</dd></div>
          </dl>
          <div className="two-col">
            <div className="callout">
              <strong>Parametric guarantee (conditional)</strong>
              <p className="small">{artifact.guarantee?.statement}</p>
            </div>
            <div className="callout">
              <strong>Sampled diagnostics</strong>
              <p className="small">
                {artifact.arbitrage_diagnostics?.statement}; minimum butterfly density g ={" "}
                {sig(artifact.arbitrage_diagnostics?.min_butterfly_density_g, 3)}.
              </p>
            </div>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col">Sample</th>
                  <th scope="col" className="num">Quotes</th>
                  <th scope="col" className="num">Price RMSE</th>
                  <th scope="col" className="num">Max |error|</th>
                  <th scope="col" className="num">Spread-weighted RMS</th>
                  <th scope="col" className="num">Inside bid/ask</th>
                </tr>
              </thead>
              <tbody>
                <StatsRow label="In-sample (fitted)" s={artifact.in_sample} />
                <StatsRow label="Held-out strikes" s={artifact.held_out} />
                {artifact.later_snapshot && (
                  <StatsRow label={`Later snapshot (+${sig(artifact.later_snapshot.elapsed_days, 3)} d, no refit)`} s={artifact.later_snapshot} />
                )}
              </tbody>
            </table>
          </div>
          {artifact.truth_recovery && (
            <p className="note">
              Synthetic data: the generating surface is known. Max |IV error| vs truth on k ∈ [{artifact.truth_recovery.k_range.join(", ")}]:{" "}
              {sig(artifact.truth_recovery.max_abs_iv_error * 100, 3)} vol points.
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
            <figcaption>
              Implied volatility vs log-moneyness k = ln(K/F). Line: fitted surface at quoted strikes. Dots: market mid IV
              with bid–ask IV range; held-out strikes shown separately.
            </figcaption>
            <ResponsiveContainer width="100%" height={340}>
              <ComposedChart margin={{ top: 10, right: 20, bottom: 30, left: 10 }}>
                <CartesianGrid stroke={pal.grid} />
                <XAxis type="number" dataKey="k" domain={["auto", "auto"]} stroke={pal.ink2} tickFormatter={(v: number) => sig(v, 2)} label={{ value: "k = ln(K/F)", position: "bottom", fill: pal.ink2 }} />
                <YAxis type="number" domain={["auto", "auto"]} stroke={pal.ink2} tickFormatter={(v: number) => pct(v, 3)} width={70} />
                <Tooltip
                  content={({ payload }) => {
                    const p = payload?.[0]?.payload as ReturnType<typeof point> | undefined;
                    if (!p || p.strike === undefined) return null;
                    return (
                      <div className="tooltip">
                        <strong>{p.type} K = {p.strike}</strong>
                        <div>bid/ask {sig(p.bid, 6)} / {sig(p.ask, 6)}; model {sig(p.model, 6)}</div>
                        <div>mid IV {pct(p.iv, 4)}; model IV {pct(p.model_iv, 4)}</div>
                        <div>{p.inside ? "model inside bid/ask" : "model outside bid/ask"}</div>
                      </div>
                    );
                  }}
                />
                <Legend verticalAlign="top" />
                <Line data={curve} dataKey="model_iv" name="fitted SSVI" stroke={pal.series[0]} strokeWidth={2} dot={false} isAnimationActive={false} />
                <Scatter data={inSample} dataKey="iv" name="quotes used in fit" fill={pal.series[1]} isAnimationActive={false}>
                  <ErrorBar dataKey="err" direction="y" width={4} stroke={pal.series[1]} strokeWidth={1.5} isAnimationActive={false} />
                </Scatter>
                <Scatter data={heldOut} dataKey="iv" name="held-out quotes" fill={pal.series[2]} isAnimationActive={false}>
                  <ErrorBar dataKey="err" direction="y" width={4} stroke={pal.series[2]} strokeWidth={1.5} isAnimationActive={false} />
                </Scatter>
              </ComposedChart>
            </ResponsiveContainer>
          </figure>
          <SurfaceCharts fitId={artifact.fit_id} />
          <details>
            <summary>Forwards and exclusions</summary>
            <table>
              <thead>
                <tr><th>Expiry</th><th className="num">T</th><th>Method</th><th className="num">Forward</th><th className="num">Discount</th><th className="num">Parity pairs</th></tr>
              </thead>
              <tbody>
                {Object.entries(artifact.forwards).map(([e, f]) => (
                  <tr key={e}><td>{e.slice(0, 10)}</td><td className="num">{sig(f.T, 4)}</td><td>{f.method.replace(/_/g, " ")}</td><td className="num">{sig(f.F, 8)}</td><td className="num">{sig(f.D, 6)}</td><td className="num">{f.parity_pairs}</td></tr>
                ))}
              </tbody>
            </table>
            <ExclusionList exclusions={artifact.exclusions} />
          </details>
        </div>
      )}
      {snaps && snaps.length > 0 && <HestonCalibrationPanel snapshotId={snapshotId} laterId={laterId} />}
    </section>
  );
}

/** Short provenance label: which generator produced a synthetic snapshot, else its source. */
function sourceLabel(s: SnapshotSummary): string {
  const m = /^synthetic: (\w+) generator/.exec(s.source);
  if (m) return `synthetic ${m[1]}`;
  return (s.synthetic ? "synthetic · " : "") + s.source.slice(0, 32);
}

function StatsRow({ label, s }: { label: string; s?: FitStats }) {
  if (!s) return null;
  return (
    <tr>
      <th scope="row">{label}</th>
      <td className="num">{s.n}</td>
      <td className="num">{sig(s.price_rmse, 3)}</td>
      <td className="num">{sig(s.max_abs_price_error, 3)}</td>
      <td className="num">{sig(s.weighted_residual_rms, 3)}</td>
      <td className="num">{s.bid_ask_containment === undefined ? "—" : pct(s.bid_ask_containment, 3)}</td>
    </tr>
  );
}

function ExclusionList({ exclusions }: { exclusions: Record<string, number> }) {
  return (
    <p className="small">
      Excluded before fitting (counts per reason):{" "}
      {Object.entries(exclusions)
        .map(([k, n]) => `${k.replace(/_/g, " ")} ${n}`)
        .join(" · ") || "none"}
    </p>
  );
}
