import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, type SurfaceViewsResponse } from "../api/client";
import { pct, sig } from "../lib/decimal";
import { rampColor, usePalette } from "../lib/theme";
import { useAction } from "../lib/useAction";
import { ErrorBox, Field, Loading } from "./common";

/** Implied-vol heat map, ATM term structure and risk-neutral density of a fitted surface. */
export function SurfaceCharts({ fitId }: { fitId: string }) {
  const pal = usePalette();
  const [views, setViews] = useState<SurfaceViewsResponse | null>(null);
  const [slice, setSlice] = useState(0);
  const { busy, error, run } = useAction();

  useEffect(() => {
    let live = true;
    void run("views", () => api.surfaceViews(fitId)).then((v) => {
      if (live) {
        setViews(v ?? null);
        setSlice(v ? v.densities.length - 1 : 0);
      }
    });
    return () => {
      live = false;
    };
  }, [fitId, run]);

  if (busy) return <Loading label="Evaluating the surface…" />;
  if (error) return <ErrorBox error={error} />;
  if (!views) return null;

  const term = views.term_structure.map((p) => ({
    T: p.T,
    interpolated: p.region === "interpolated" ? p.atm_vol : null,
    extrapolated: p.region === "extrapolated" ? p.atm_vol : null,
  }));
  const d = views.densities[slice];
  const density = views.density_k.map((k, i) => ({ k, p: d.density[i] }));

  return (
    <div className="viz">
      <h3>Surface views</h3>
      <p className="note">{views.notes}</p>
      <Heatmap views={views} />
      <div className="small-multiples">
        <figure className="chart">
          <figcaption>
            <strong>ATM volatility term structure</strong> <span className="muted">(√(θ_t / t))</span>
          </figcaption>
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={term} margin={{ top: 6, right: 12, bottom: 24, left: 0 }}>
              <CartesianGrid stroke={pal.grid} />
              <XAxis type="number" dataKey="T" domain={["dataMin", "dataMax"]} stroke={pal.ink2} tickFormatter={(v: number) => sig(v, 2)} label={{ value: "maturity (years)", position: "bottom", fill: pal.ink2 }} />
              <YAxis stroke={pal.ink2} tickFormatter={(v: number) => pct(v, 3)} width={56} domain={["auto", "auto"]} />
              <Tooltip formatter={(v) => (v === null ? null : pct(Number(v), 4))} labelFormatter={(l) => `T = ${sig(Number(l), 4)} y`} />
              <Legend verticalAlign="top" itemSorter={null} />
              {views.fitted_expiries.map((t) => <ReferenceLine key={t} x={t} stroke={pal.grid} />)}
              <Line dataKey="interpolated" name="between fitted expiries" stroke={pal.series[0]} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false} />
              <Line dataKey="extrapolated" name="extrapolated" stroke={pal.series[1]} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false} />
            </LineChart>
          </ResponsiveContainer>
        </figure>
        <figure className="chart">
          <figcaption>
            <strong>Risk-neutral density of k = ln(K/F)</strong>{" "}
            <span className="muted">
              mass {sig(d.mass_on_grid, 5)}, E[F_T/F] {sig(d.mean_forward_ratio_on_grid, 5)}, min {sig(d.min_density, 3)}
            </span>
          </figcaption>
          <Field label="Maturity">
            {(id) => (
              <select id={id} value={slice} onChange={(e) => setSlice(Number(e.target.value))}>
                {views.densities.map((x, i) => <option key={i} value={i}>T = {sig(x.T, 4)} years</option>)}
              </select>
            )}
          </Field>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={density} margin={{ top: 6, right: 12, bottom: 24, left: 0 }}>
              <CartesianGrid stroke={pal.grid} />
              <XAxis type="number" dataKey="k" domain={["dataMin", "dataMax"]} stroke={pal.ink2} tickFormatter={(v: number) => sig(v, 2)} label={{ value: "k = ln(K/F)", position: "bottom", fill: pal.ink2 }} />
              <YAxis stroke={pal.ink2} tickFormatter={(v: number) => sig(v, 2)} width={40} />
              <Tooltip formatter={(v) => sig(Number(v), 5)} labelFormatter={(l) => `k = ${sig(Number(l), 4)}`} />
              <ReferenceLine x={0} stroke={pal.ink2} />
              <Line dataKey="p" name="density" stroke={pal.series[0]} strokeWidth={2} dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
          <p className="small muted">A density that dipped below zero would mean butterfly arbitrage; mass and E[F_T/F] near 1 check normalisation and the forward.</p>
        </figure>
      </div>
    </div>
  );
}

function Heatmap({ views }: { views: SurfaceViewsResponse }) {
  const pal = usePalette();
  const [hover, setHover] = useState<{ i: number; j: number } | null>(null);
  const { k_grid: ks, t_grid: ts, implied_vol: iv } = views;
  const flat = iv.flat();
  const [lo, hi] = [Math.min(...flat), Math.max(...flat)];
  const W = 640;
  const H = 300;
  const pad = { l: 56, r: 12, t: 8, b: 36 };
  const cw = (W - pad.l - pad.r) / ks.length;
  const ch = (H - pad.t - pad.b) / ts.length;
  const fitted = useMemo(() => new Set(views.fitted_expiries), [views.fitted_expiries]);
  const kTicks = ks.filter((_, j) => j % 10 === 0);
  return (
    <figure className="chart">
      <figcaption>
        <strong>Implied volatility</strong> by log-moneyness (columns) and maturity (rows, shortest at the top). Rows marked ▸ are fitted
        expiries; the others are interpolated.
      </figcaption>
      <svg className="heatmap-svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Implied volatility heat map" onMouseLeave={() => setHover(null)}>
        {iv.map((row, i) =>
          row.map((v, j) => (
            <rect
              key={`${i}-${j}`}
              className="cell"
              x={pad.l + j * cw}
              y={pad.t + i * ch}
              width={cw + 0.5}
              height={ch + 0.5}
              fill={rampColor(pal, (v - lo) / (hi - lo || 1))}
              onMouseEnter={() => setHover({ i, j })}
            />
          )),
        )}
        {ts.map((t, i) =>
          fitted.has(t) ? (
            <text key={t} x={pad.l - 4} y={pad.t + (i + 0.7) * ch} fontSize="10" textAnchor="end" fill={pal.ink2}>
              ▸ {sig(t, 2)}y
            </text>
          ) : null,
        )}
        {kTicks.map((k) => {
          const j = ks.indexOf(k);
          return (
            <text key={k} x={pad.l + (j + 0.5) * cw} y={H - pad.b + 14} fontSize="10" textAnchor="middle" fill={pal.ink2}>
              {sig(k, 2)}
            </text>
          );
        })}
        <text x={pad.l + (W - pad.l - pad.r) / 2} y={H - 4} fontSize="11" textAnchor="middle" fill={pal.ink2}>
          k = ln(K/F)
        </text>
      </svg>
      <div className="color-legend">
        <span>{pct(lo, 3)}</span>
        <span className="color-legend__bar" style={{ background: `linear-gradient(to right, ${pal.ramp.join(",")})` }} aria-hidden="true" />
        <span>{pct(hi, 3)}</span>
        <span className="small" aria-live="polite">
          {hover ? `k = ${sig(ks[hover.j], 3)}, T = ${sig(ts[hover.i], 3)} y: implied vol ${pct(iv[hover.i][hover.j], 4)}` : "Hover a cell for its value"}
        </span>
      </div>
      <details>
        <summary>Heat map data table</summary>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>T \ k</th>
                {ks.filter((_, j) => j % 4 === 0).map((k) => <th key={k} className="num">{sig(k, 2)}</th>)}
              </tr>
            </thead>
            <tbody>
              {iv.map((row, i) => (
                <tr key={i}>
                  <th scope="row">{sig(ts[i], 3)}</th>
                  {row.filter((_, j) => j % 4 === 0).map((v, j) => <td key={j} className="num">{pct(v, 3)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}
