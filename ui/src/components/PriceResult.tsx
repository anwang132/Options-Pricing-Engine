import type { PriceResponse } from "../api/client";
import { sig } from "../lib/decimal";
import { StatusBadge } from "./common";

const GREEK_LABEL: Record<string, string> = {
  delta: "Delta",
  gamma: "Gamma",
  vega: "Vega",
  theta: "Theta",
  rho: "Rho",
  dividend_rho: "Dividend rho",
};

export function PriceResult({ result }: { result: PriceResponse }) {
  const u = result.uncertainty;
  return (
    <div className="result" aria-live="polite">
      <div className="hero">
        <div>
          <div className="hero__label">Price per underlying unit</div>
          <div className="hero__value">
            {sig(result.price, 10)} <span className="unit">{result.currency}</span>
          </div>
          {u && (
            <div className="hero__sub">
              ± {sig(u.standard_error, 3)} standard error · {Math.round(u.confidence_level * 100)}% CI [{sig(u.ci_low, 8)},{" "}
              {sig(u.ci_high, 8)}] from {u.independent_observations.toLocaleString()} independent observations
            </div>
          )}
          {typeof result.diagnostics.upper_bound === "number" && (
            <div className="hero__sub">
              Bermudan price bracket [{sig((result.diagnostics.bermudan_interval as number[])[0], 6)},{" "}
              {sig((result.diagnostics.bermudan_interval as number[])[1], 6)}]: this lower estimate and the Andersen–Broadie
              upper bound {sig(result.diagnostics.upper_bound, 6)} (duality gap {sig(result.diagnostics.duality_gap as number, 3)})
            </div>
          )}
        </div>
        <div>
          <div className="hero__label">One long contract</div>
          <div className="hero__value hero__value--small">
            {sig(result.contract_value, 10)} <span className="unit">{result.currency}</span>
          </div>
          <div className="hero__sub">price × multiplier {result.multiplier}</div>
        </div>
      </div>
      <p className="note">
        {result.engine_id} v{result.engine_version} · T = {sig(result.time_to_expiry, 8)} years (
        {String(result.assumptions.day_count)}) · {result.dividend_treatment.replace(/_/g, " ")} · values are unrounded
        float64 shown to 10 significant digits.
        {u && " Sampling uncertainty (standard error) is separate from model limitations."}
      </p>

      <h3>Greeks</h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Greek</th>
              <th scope="col" className="num">Value</th>
              <th scope="col">Display unit</th>
              <th scope="col">Status</th>
              <th scope="col">Method</th>
              <th scope="col" className="num">Std. error</th>
              <th scope="col">Raw unit / note</th>
            </tr>
          </thead>
          <tbody>
            {result.greeks.map((g) => (
              <tr key={g.name}>
                <th scope="row">{GREEK_LABEL[g.name] ?? g.name}</th>
                <td className="num">{g.display_value === null ? "—" : sig(g.display_value, 8)}</td>
                <td>{g.display_unit}</td>
                <td>
                  <StatusBadge status={g.status} />
                </td>
                <td>{g.method ?? "—"}</td>
                <td className="num">{g.display_standard_error == null ? "—" : sig(g.display_standard_error, 3)}</td>
                <td className="small">
                  raw {g.value === null ? "unavailable" : sig(g.value, 8)}: {g.unit}
                  {g.note && <div className="muted">{g.note}</div>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <details>
        <summary>Assumptions</summary>
        <dl className="kv">
          {Object.entries(result.assumptions).map(([k, v]) => (
            <div key={k}>
              <dt>{k.replace(/_/g, " ")}</dt>
              <dd>{String(v)}</dd>
            </div>
          ))}
        </dl>
      </details>
      <details>
        <summary>Diagnostics</summary>
        <dl className="kv">
          {Object.entries(result.diagnostics).map(([k, v]) => (
            <div key={k}>
              <dt>{k.replace(/_/g, " ")}</dt>
              <dd>{typeof v === "number" ? sig(v, 10) : v === null ? "—" : String(v)}</dd>
            </div>
          ))}
        </dl>
      </details>
      <p className="small muted">Request hash {result.request_hash}</p>
    </div>
  );
}
