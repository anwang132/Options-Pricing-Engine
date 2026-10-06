import { fellerSatisfied, type FieldErrors, type HestonForm, type InstrumentForm } from "../lib/instrument";
import { Field } from "./common";

interface Props {
  form: InstrumentForm;
  errors: FieldErrors;
  onChange: (next: InstrumentForm) => void;
  showVolatility?: boolean;
}

export function InstrumentPanel({ form, errors, onChange, showVolatility = true }: Props) {
  const set = <K extends keyof InstrumentForm>(k: K, v: InstrumentForm[K]) => onChange({ ...form, [k]: v });
  const text = (k: keyof InstrumentForm, label: string, unit?: string, hint?: string) => (
    <Field label={label} unit={unit} hint={hint} error={errors[k]}>
      {(id, d) => (
        <input
          id={id}
          aria-describedby={d}
          aria-invalid={!!errors[k]}
          inputMode="decimal"
          value={form[k] as string}
          onChange={(e) => set(k, e.target.value as never)}
        />
      )}
    </Field>
  );
  return (
    <section className="panel" aria-labelledby="inputs-h">
      <h2 id="inputs-h">Contract &amp; market inputs</h2>
      <p className="note">
        Defaults are <strong>synthetic</strong> example inputs (Hull's textbook case). No live market data is used.
      </p>
      <fieldset>
        <legend>Contract</legend>
        <div className="grid">
          <Field label="Option type">
            {(id) => (
              <select id={id} value={form.optionType} onChange={(e) => set("optionType", e.target.value as "call" | "put")}>
                <option value="call">Call</option>
                <option value="put">Put</option>
              </select>
            )}
          </Field>
          <Field label="Exercise style">
            {(id) => (
              <select id={id} value={form.exercise} onChange={(e) => set("exercise", e.target.value as "european" | "american")}>
                <option value="european">European</option>
                <option value="american">American</option>
              </select>
            )}
          </Field>
          {text("strike", "Strike", `${form.currency} per unit`)}
          {text("multiplier", "Multiplier", "units per contract")}
          <Field label="Expiry" unit="UTC" error={errors.expiry} hint="Exercise/expiration timestamp">
            {(id, d) => (
              <input id={id} aria-describedby={d} type="datetime-local" value={form.expiry} onChange={(e) => set("expiry", e.target.value)} />
            )}
          </Field>
          <Field label="Underlying id">
            {(id) => <input id={id} value={form.underlying} onChange={(e) => set("underlying", e.target.value)} />}
          </Field>
        </div>
      </fieldset>
      <fieldset>
        <legend>Market &amp; valuation</legend>
        <div className="grid">
          <Field label="Valuation time" unit="UTC" error={errors.asOf} hint="Explicit as-of; the engine never reads the clock">
            {(id, d) => (
              <input id={id} aria-describedby={d} type="datetime-local" value={form.asOf} onChange={(e) => set("asOf", e.target.value)} />
            )}
          </Field>
          {text("spot", "Spot", `${form.currency} per unit`)}
          {text("ratePct", "Risk-free rate", "% per year, continuous", "5 means 0.05; negative allowed")}
          {text("yieldPct", "Dividend yield", "% per year, continuous")}
        </div>
      </fieldset>
      {showVolatility && <ModelInputs form={form} errors={errors} onChange={onChange} />}
      <fieldset>
        <legend>Dividends</legend>
        <details className="dividends">
          <summary>Cash dividends ({form.dividends.length}) — escrowed model</summary>
          <p className="note">
            Known cash amounts per unit. Priced with the escrowed-dividend model (spot net of their present value).
            Ex-dates before the valuation time or after expiry are ignored.
          </p>
          {form.dividends.map((d, i) => (
            <div className="row" key={i}>
              <Field label={`Ex-date ${i + 1}`} unit="UTC">
                {(id) => (
                  <input
                    id={id}
                    type="datetime-local"
                    value={d.exDate}
                    onChange={(e) => set("dividends", form.dividends.map((x, j) => (j === i ? { ...x, exDate: e.target.value } : x)))}
                  />
                )}
              </Field>
              <Field label="Amount" unit={`${form.currency} per unit`}>
                {(id) => (
                  <input
                    id={id}
                    inputMode="decimal"
                    value={d.amount}
                    onChange={(e) => set("dividends", form.dividends.map((x, j) => (j === i ? { ...x, amount: e.target.value } : x)))}
                  />
                )}
              </Field>
              <button type="button" className="link" onClick={() => set("dividends", form.dividends.filter((_, j) => j !== i))}>
                Remove
              </button>
            </div>
          ))}
          <button type="button" className="secondary" onClick={() => set("dividends", [...form.dividends, { exDate: "", amount: "" }])}>
            Add dividend
          </button>
          {errors.general && <p className="error-text" role="alert">{errors.general}</p>}
        </details>
      </fieldset>
    </section>
  );
}

function ModelInputs({ form, errors, onChange }: Omit<Props, "showVolatility">) {
  const setH = (k: keyof HestonForm, v: string) => onChange({ ...form, heston: { ...form.heston, [k]: v } });
  const h = (k: keyof HestonForm, label: string, unit?: string, hint?: string) => (
    <Field label={label} unit={unit} hint={hint} error={errors[k]}>
      {(id, d) => (
        <input id={id} aria-describedby={d} aria-invalid={!!errors[k]} inputMode="decimal" value={form.heston[k]} onChange={(e) => setH(k, e.target.value)} />
      )}
    </Field>
  );
  const feller = fellerSatisfied(form.heston);
  return (
    <fieldset>
      <legend>Model</legend>
      <div className="grid">
        <Field label="Model" hint={form.model === "heston" ? "Stochastic variance; European exercise only" : "Flat volatility (GBM)"}>
          {(id, d) => (
            <select id={id} aria-describedby={d} value={form.model} onChange={(e) => onChange({ ...form, model: e.target.value as InstrumentForm["model"] })}>
              <option value="black_scholes">Black-Scholes</option>
              <option value="heston">Heston stochastic volatility</option>
            </select>
          )}
        </Field>
        {form.model === "black_scholes" ? (
          <Field label="Volatility (model)" unit="% per year" hint="Model parameter σ; 20 means 0.20" error={errors.volPct}>
            {(id, d) => (
              <input id={id} aria-describedby={d} aria-invalid={!!errors.volPct} inputMode="decimal" value={form.volPct} onChange={(e) => onChange({ ...form, volPct: e.target.value })} />
            )}
          </Field>
        ) : (
          <>
            {h("spotVolPct", "Initial vol √v₀", "% per year", "Today's instantaneous volatility")}
            {h("longVolPct", "Long-run vol √θ", "% per year", "Variance reverts towards θ")}
            {h("kappa", "Mean reversion κ", "per year", "Speed of reversion; half-life ln2/κ")}
            {h("volOfVol", "Vol of vol σ", "per √year", "0 gives Black-Scholes with time-averaged variance")}
            {h("rho", "Correlation ρ", "spot vs variance", "Negative ρ gives the equity downside skew")}
          </>
        )}
      </div>
      {form.model === "heston" && feller !== null && (
        <p className="note">
          Feller condition 2κθ ≥ σ² is {feller ? "satisfied: variance stays strictly positive." : "violated: variance can touch zero. This is allowed and priced correctly; it is reported, not rejected."}
        </p>
      )}
    </fieldset>
  );
}
