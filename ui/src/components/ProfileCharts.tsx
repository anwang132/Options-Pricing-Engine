import { useEffect, useState } from "react";
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
import { api, type ExerciseBoundaryResponse, type PriceRequest, type ProfileResponse } from "../api/client";
import { sig } from "../lib/decimal";
import { usePalette, type Palette } from "../lib/theme";
import { useAction } from "../lib/useAction";
import { ErrorBox, Loading } from "./common";

const GREEK_TITLES: Record<string, string> = {
  delta: "Delta",
  gamma: "Gamma",
  vega: "Vega",
  theta: "Theta",
};

function horizonLabel(days: number, elapsed: number): string {
  if (elapsed === 0) return `today (${sig(days, 3)} d left)`;
  return `${sig(days, 3)} days left`;
}

/** Value and Greek profiles against spot; American contracts also get the exercise boundary. */
export function ProfileCharts({ body, american }: { body: PriceRequest; american: boolean }) {
  const pal = usePalette();
  const [profile, setProfile] = useState<ProfileResponse | null>(null);
  const [boundary, setBoundary] = useState<ExerciseBoundaryResponse | null>(null);
  const { busy, error, run } = useAction();
  const key = JSON.stringify(body);

  useEffect(() => {
    let live = true;
    void (async () => {
      const p = await run("profile", () => api.profile(body));
      const b = american ? await run("boundary", () => api.exerciseBoundary(body)) : undefined;
      if (live) {
        setProfile(p ?? null);
        setBoundary(b ?? null);
      }
    })();
    return () => {
      live = false;
    };
    // Recompute only when the request itself changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, american]);

  if (busy && !profile) return <Loading label="Computing profiles…" />;
  if (error && !profile) return <ErrorBox error={error} />;
  if (!profile) return null;

  const rows = profile.spots.map((s, i) => {
    const row: Record<string, number | null> = { spot: s, payoff: profile.payoff[i] };
    profile.horizons.forEach((h, j) => (row[`h${j}`] = h.values[i]));
    for (const [g, vals] of Object.entries(profile.greeks)) row[g] = vals[i];
    return row;
  });

  return (
    <div className="viz">
      <h3>Value and risk profiles</h3>
      <p className="note">
        Every point is priced by the engine ({profile.engine}). {profile.assumptions}
      </p>
      <figure className="chart">
        <figcaption>Option value per unit against spot, today and as expiry approaches; the grey line is the payoff at expiry.</figcaption>
        <ResponsiveContainer width="100%" height={320}>
          <LineChart data={rows} margin={{ top: 10, right: 20, bottom: 30, left: 10 }}>
            <CartesianGrid stroke={pal.grid} />
            <XAxis type="number" dataKey="spot" domain={["dataMin", "dataMax"]} stroke={pal.ink2} tickFormatter={(v: number) => sig(v, 3)} label={{ value: "spot", position: "bottom", fill: pal.ink2 }} />
            <YAxis stroke={pal.ink2} tickFormatter={(v: number) => sig(v, 3)} width={60} />
            <Tooltip formatter={(v) => sig(Number(v), 6)} labelFormatter={(l) => `spot ${sig(Number(l), 6)}`} />
            <Legend verticalAlign="top" itemSorter={null} />
            {/* Labels sit on opposite sides so they cannot collide when spot is near the strike. */}
            <ReferenceLine x={profile.strike} stroke={pal.ink2} label={{ value: "strike", fill: pal.ink2, position: profile.strike <= profile.current_spot ? "insideTopRight" : "insideTopLeft", dy: 14 }} />
            <ReferenceLine x={profile.current_spot} stroke={pal.series[0]} label={{ value: "spot now", fill: pal.ink2, position: profile.strike <= profile.current_spot ? "insideTopLeft" : "insideTopRight" }} />
            {profile.horizons.map((h, j) => (
              <Line key={j} dataKey={`h${j}`} name={horizonLabel(h.remaining_days, h.elapsed_fraction)} stroke={pal.series[j]} strokeWidth={2} dot={false} isAnimationActive={false} />
            ))}
            <Line dataKey="payoff" name="payoff at expiry" stroke={pal.ink2} strokeWidth={1.5} dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </figure>
      <div className="small-multiples">
        {Object.keys(profile.greeks).map((g) => (
          <GreekChart key={g} rows={rows} greek={g} unit={profile.greek_units[g]} strike={profile.strike} spot={profile.current_spot} pal={pal} />
        ))}
      </div>
      {american && boundary && <BoundaryChart b={boundary} pal={pal} />}
      {american && busy === "boundary" && <Loading label="Extracting the exercise boundary…" />}
      <details>
        <summary>Profile data table</summary>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th className="num">spot</th>
                {profile.horizons.map((h, j) => <th key={j} className="num">{horizonLabel(h.remaining_days, h.elapsed_fraction)}</th>)}
                <th className="num">payoff</th>
                {Object.keys(profile.greeks).map((g) => <th key={g} className="num">{GREEK_TITLES[g] ?? g}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td className="num">{sig(r.spot, 6)}</td>
                  {profile.horizons.map((_, j) => <td key={j} className="num">{sig(r[`h${j}`], 6)}</td>)}
                  <td className="num">{sig(r.payoff, 6)}</td>
                  {Object.keys(profile.greeks).map((g) => <td key={g} className="num">{sig(r[g], 5)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

function GreekChart(props: { rows: Record<string, number | null>[]; greek: string; unit: string; strike: number; spot: number; pal: Palette }) {
  const { rows, greek, unit, strike, spot, pal } = props;
  return (
    <figure className="chart">
      <figcaption>
        <strong>{GREEK_TITLES[greek] ?? greek}</strong> <span className="muted">({unit})</span>
      </figcaption>
      <ResponsiveContainer width="100%" height={180}>
        <LineChart data={rows} margin={{ top: 6, right: 12, bottom: 6, left: 0 }}>
          <CartesianGrid stroke={pal.grid} />
          <XAxis type="number" dataKey="spot" domain={["dataMin", "dataMax"]} stroke={pal.ink2} tickFormatter={(v: number) => sig(v, 3)} />
          <YAxis stroke={pal.ink2} tickFormatter={(v: number) => sig(v, 2)} width={64} />
          <Tooltip formatter={(v) => (v === null ? "undefined here" : sig(Number(v), 6))} labelFormatter={(l) => `spot ${sig(Number(l), 6)}`} />
          <ReferenceLine x={strike} stroke={pal.ink2} />
          <ReferenceLine x={spot} stroke={pal.series[0]} />
          <Line dataKey={greek} name={GREEK_TITLES[greek] ?? greek} stroke={pal.series[0]} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false} />
        </LineChart>
      </ResponsiveContainer>
    </figure>
  );
}

function BoundaryChart({ b, pal }: { b: ExerciseBoundaryResponse; pal: Palette }) {
  const put = b.option_type === "put";
  const pts = b.points.map((p) => ({
    days: p.days_from_now,
    boundary: p.boundary_spot,
    // Shade the exercise region between the boundary and the axis edge on the exercise side.
    region: p.boundary_spot === null ? null : put ? [0, p.boundary_spot] : [p.boundary_spot, b.strike * 3],
  }));
  const known = b.points.map((p) => p.boundary_spot).filter((v): v is number => v !== null);
  const lo = Math.min(b.current_spot, ...known, b.strike) * 0.95;
  const hi = Math.max(b.current_spot, ...known, b.strike) * 1.05;
  return (
    <figure className="chart">
      <figcaption>
        <strong>Early-exercise boundary</strong> — exercising immediately is optimal {b.exercise_region} (shaded).{" "}
        {b.no_node_exercised
          ? "No tree node is in the exercise region: early exercise is never optimal for this contract under these inputs."
          : b.exercise_optimal_now
            ? "At today's spot, immediate exercise is optimal."
            : "At today's spot, holding is optimal."}
      </figcaption>
      {!b.no_node_exercised && (
        <ResponsiveContainer width="100%" height={300}>
          <ComposedChart data={pts} margin={{ top: 10, right: 20, bottom: 30, left: 10 }}>
            <CartesianGrid stroke={pal.grid} />
            <XAxis type="number" dataKey="days" domain={["dataMin", "dataMax"]} stroke={pal.ink2} tickFormatter={(v: number) => sig(v, 3)} label={{ value: "days from now", position: "bottom", fill: pal.ink2 }} />
            <YAxis type="number" domain={[lo, hi]} allowDataOverflow stroke={pal.ink2} tickFormatter={(v: number) => sig(v, 3)} width={60} />
            <Tooltip formatter={(v, n) => (n === "exercise boundary" ? sig(Number(v), 6) : null)} labelFormatter={(l) => `${sig(Number(l), 4)} days from now`} />
            <Legend verticalAlign="top" itemSorter={null} />
            <Area dataKey="region" name="exercise region" fill={pal.series[1]} fillOpacity={0.12} stroke="none" isAnimationActive={false} connectNulls={false} />
            <Line type="stepAfter" dataKey="boundary" name="exercise boundary" stroke={pal.series[1]} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false} />
            <ReferenceLine y={b.strike} stroke={pal.ink2} label={{ value: "strike", fill: pal.ink2, position: "insideTopLeft" }} />
            <ReferenceLine y={b.current_spot} stroke={pal.series[0]} label={{ value: "spot now", fill: pal.ink2, position: "insideBottomLeft" }} />
          </ComposedChart>
        </ResponsiveContainer>
      )}
      <p className="small muted">{b.resolution_note} {b.gap_note}</p>
    </figure>
  );
}
