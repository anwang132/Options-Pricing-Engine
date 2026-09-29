import type { EngineInfo, Schemas } from "../api/client";
import { ENGINE_LABELS } from "../lib/instrument";
import { Field } from "./common";

export type EngineConfig = Schemas["AnalyticEngineIn"] | Schemas["CRREngineIn"] | Schemas["MonteCarloEngineIn"];

export const DEFAULT_CONFIGS: Record<string, EngineConfig> = {
  bsm_analytic: { engine: "bsm_analytic" },
  crr_tree: { engine: "crr_tree", steps: 1000, allow_step_refinement: false, odd_even_diagnostic: true },
  mc_terminal_gbm: {
    engine: "mc_terminal_gbm",
    paths: 200000,
    seed: 20260928,
    antithetic: true,
    control_variate: "none",
  },
};

interface Props {
  engines: EngineInfo[];
  supported: EngineInfo[];
  config: EngineConfig;
  onChange: (c: EngineConfig) => void;
}

export function EngineSettings({ engines, supported, config, onChange }: Props) {
  const supportedIds = new Set(supported.map((e) => e.engine_id));
  const int = (v: string) => (v === "" ? 0 : Math.trunc(Number(v)));
  return (
    <fieldset>
      <legend>Engine</legend>
      <div className="engine-choices" role="radiogroup" aria-label="Pricing engine">
        {engines.map((e) => {
          const ok = supportedIds.has(e.engine_id);
          return (
            <label key={e.engine_id} className={`choice${ok ? "" : " choice--disabled"}`}>
              <input
                type="radio"
                name="engine"
                value={e.engine_id}
                checked={config.engine === e.engine_id}
                disabled={!ok}
                onChange={() => onChange(DEFAULT_CONFIGS[e.engine_id])}
              />
              <span>
                <strong>{ENGINE_LABELS[e.engine_id] ?? e.engine_id}</strong>
                <small>
                  {ok
                    ? e.description
                    : `Not available: supports ${e.capabilities.exercise_styles.join("/")} exercise` +
                      (e.capabilities.zero_volatility ? "" : ", volatility > 0")}
                </small>
              </span>
            </label>
          );
        })}
      </div>
      {config.engine === "crr_tree" && (
        <div className="grid">
          <Field label="Steps" hint="Work limit 20,000; time grows with steps²">
            {(id, d) => (
              <input id={id} aria-describedby={d} inputMode="numeric" value={config.steps ?? ""} onChange={(e) => onChange({ ...config, steps: int(e.target.value) })} />
            )}
          </Field>
          <label className="check">
            <input
              type="checkbox"
              checked={!!config.allow_step_refinement}
              onChange={(e) => onChange({ ...config, allow_step_refinement: e.target.checked })}
            />
            Allow step refinement if the up-probability is invalid (reported, never clipped)
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={config.odd_even_diagnostic !== false}
              onChange={(e) => onChange({ ...config, odd_even_diagnostic: e.target.checked })}
            />
            Odd/even diagnostic (also prices N+1 steps)
          </label>
        </div>
      )}
      {config.engine === "mc_terminal_gbm" && (
        <div className="grid">
          <Field label="Payoff evaluations (paths)" hint="Even with antithetics; limit 20,000,000">
            {(id, d) => (
              <input id={id} aria-describedby={d} inputMode="numeric" value={config.paths ?? ""} onChange={(e) => onChange({ ...config, paths: int(e.target.value) })} />
            )}
          </Field>
          <Field label="Seed" hint="PCG64 via SeedSequence(seed)">
            {(id, d) => (
              <input id={id} aria-describedby={d} inputMode="numeric" value={config.seed ?? ""} onChange={(e) => onChange({ ...config, seed: int(e.target.value) })} />
            )}
          </Field>
          <label className="check">
            <input type="checkbox" checked={config.antithetic !== false} onChange={(e) => onChange({ ...config, antithetic: e.target.checked })} />
            Antithetic pairs (SE from pair averages)
          </label>
          <Field label="Control variate">
            {(id) => (
              <select
                id={id}
                value={config.control_variate ?? "none"}
                onChange={(e) => onChange({ ...config, control_variate: e.target.value as "none" | "terminal_underlying" })}
              >
                <option value="none">None</option>
                <option value="terminal_underlying">Discounted terminal underlying (β from independent pilot)</option>
              </select>
            )}
          </Field>
        </div>
      )}
    </fieldset>
  );
}
