import type { EngineInfo } from "../api/client";
import { ENGINE_LABELS } from "../lib/instrument";

export function EnginesView({ engines }: { engines: EngineInfo[] }) {
  const greeks = ["delta", "gamma", "vega", "theta", "rho", "dividend_rho"];
  return (
    <section className="panel" aria-labelledby="eng-h">
      <h2 id="eng-h">Engine capability matrix</h2>
      <p className="note">
        Served by the API from each engine's declaration. Unsupported combinations are rejected before any computation;
        Greeks an engine cannot estimate are reported as <em>not supported</em>, never as zero.
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Engine</th>
              <th scope="col">Exercise</th>
              <th scope="col">Dividends</th>
              <th scope="col">σ = 0 / at expiry</th>
              <th scope="col">Stochastic</th>
              {greeks.map((g) => (
                <th scope="col" key={g}>{g.replace("_", " ")}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {engines.map((e) => (
              <tr key={e.engine_id}>
                <th scope="row">
                  {ENGINE_LABELS[e.engine_id] ?? e.engine_id}
                  <div className="small muted">
                    {e.engine_id} v{e.version}
                  </div>
                </th>
                <td>{e.capabilities.exercise_styles.join(", ")}</td>
                <td>{e.capabilities.dividend_treatments.map((d) => d.replace("_", " ")).join(", ")}</td>
                <td>{e.capabilities.zero_volatility ? "yes" : "no"}</td>
                <td>{e.capabilities.stochastic ? "yes (reports SE)" : "no"}</td>
                {greeks.map((g) => (
                  <td key={g} className="small">{e.capabilities.greeks[g]?.replace(/_/g, " ") ?? "not supported"}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
