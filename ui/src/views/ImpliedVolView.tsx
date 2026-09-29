import { useState } from "react";
import { api, type ImpliedVolResponse, type Schemas } from "../api/client";
import { Empty, ErrorBox, Field, Loading, StatusBadge } from "../components/common";
import { isDecimalText, pct, sig } from "../lib/decimal";
import { instrumentPayload, type InstrumentForm } from "../lib/instrument";
import { useAction } from "../lib/useAction";

type IVOut = Schemas["IVOut"];

function BoundsBar({ r }: { r: IVOut }) {
  // Position the quote between the model-free bounds (visual aid only).
  const lo = r.lower_bound;
  const hi = r.upper_bound;
  const span = hi - lo || 1;
  const pos = Math.min(Math.max((r.quote - lo) / span, -0.05), 1.05);
  return (
    <div className="bounds" aria-hidden="true">
      <div className="bounds__track" />
      <div className="bounds__mark" style={{ left: `${pos * 100}%` }} />
      <div className="bounds__labels">
        <span>lower {sig(lo, 6)}</span>
        <span>upper {sig(hi, 6)}</span>
      </div>
    </div>
  );
}

function IVCard({ r }: { r: IVOut }) {
  return (
    <article className="card" aria-label={`${r.label} inversion`}>
      <header>
        <h3>
          {r.label}: {sig(r.quote, 8)}
        </h3>
        <StatusBadge status={r.status} />
      </header>
      <div className="hero__value hero__value--small">{r.implied_vol === null ? "No implied volatility" : pct(r.implied_vol, 8)}</div>
      <p>{r.explanation}</p>
      <BoundsBar r={r} />
      <dl className="kv">
        <div><dt>Model-free bounds</dt><dd>[{sig(r.lower_bound, 8)}, {sig(r.upper_bound, 8)})</dd></div>
        {r.iv_interval && (
          <div>
            <dt>IV range from quote ± {sig(r.price_resolution, 2)}</dt>
            <dd>
              [{r.iv_interval[0] === null ? "not identifiable" : pct(r.iv_interval[0], 5)}, {r.iv_interval[1] === null ? "unbounded" : pct(r.iv_interval[1], 5)}]
            </dd>
          </div>
        )}
        {r.vega !== null && <div><dt>Vega at solution</dt><dd>{sig(r.vega, 5)} per 1.00 vol ({sig(r.vega / 100, 5)} per vol point)</dd></div>}
        {r.vol_uncertainty !== null && <div><dt>Vol uncertainty from quote resolution</dt><dd>{sig(r.vol_uncertainty * 100, 3)} vol points (stability threshold 1)</dd></div>}
        {r.price_residual !== null && <div><dt>Price residual</dt><dd>{sig(r.price_residual, 3)}</dd></div>}
        {r.iterations !== null && <div><dt>Brent iterations / bracket</dt><dd>{r.iterations} / [{r.bracket?.map((b) => sig(b, 3)).join(", ")}]</dd></div>}
      </dl>
      {r.notes.length > 0 && <ul className="small">{r.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
    </article>
  );
}

export function ImpliedVolView({ form, valid }: { form: InstrumentForm; valid: boolean }) {
  const [quotes, setQuotes] = useState({ quote: "", bid: "4.70", ask: "4.80", resolution: "" });
  const [res, setRes] = useState<ImpliedVolResponse | null>(null);
  const { busy: busyLabel, error, run: act } = useAction();
  const busy = busyLabel !== null;
  const bad = (s: string) => s !== "" && !isDecimalText(s);
  const anyBad = bad(quotes.quote) || bad(quotes.bid) || bad(quotes.ask) || bad(quotes.resolution);
  const empty = !quotes.quote && !quotes.bid && !quotes.ask;

  const run = async () => {
    const { contract, valuation, market } = instrumentPayload(form);
    const out = await act("iv", () =>
      api.impliedVol({
        schema_version: "1",
        contract,
        valuation,
        market,
        quote: quotes.quote || null,
        bid: quotes.bid || null,
        ask: quotes.ask || null,
        price_resolution: quotes.resolution ? Number(quotes.resolution) : null,
      }),
    );
    setRes(out ?? null);
  };

  return (
    <section className="panel" aria-labelledby="iv-h">
      <h2 id="iv-h">Implied volatility</h2>
      <p className="note">
        European Black-Scholes inversion by bracketed Brent search. Quotes are checked against model-free bounds first;
        each quote is inverted separately. The quote's decimal places set its resolution (e.g. 4.70 → ±0.005) unless
        overridden; that resolution drives the stability check. The volatility input above is not used here.
      </p>
      <div className="grid">
        {(["quote", "bid", "ask"] as const).map((k) => (
          <Field key={k} label={k === "quote" ? "Single quote (optional)" : k[0].toUpperCase() + k.slice(1)} unit={`${form.currency} per unit`} error={bad(quotes[k]) ? "Must be a number" : undefined}>
            {(id, d) => <input id={id} aria-describedby={d} inputMode="decimal" value={quotes[k]} onChange={(e) => setQuotes({ ...quotes, [k]: e.target.value })} />}
          </Field>
        ))}
        <Field label="Resolution override" unit="price units" hint="Blank = half the last decimal place" error={bad(quotes.resolution) ? "Must be a number" : undefined}>
          {(id, d) => <input id={id} aria-describedby={d} inputMode="decimal" value={quotes.resolution} onChange={(e) => setQuotes({ ...quotes, resolution: e.target.value })} />}
        </Field>
      </div>
      <div className="actions">
        <button type="button" onClick={run} disabled={!valid || busy || anyBad || empty}>
          {busy ? "Inverting…" : "Invert"}
        </button>
        <button type="button" className="secondary" onClick={() => setQuotes({ quote: "3.50", bid: "", ask: "", resolution: "" })}>
          Example: impossible quote
        </button>
      </div>
      {form.exercise !== "european" && (
        <p className="callout callout--warn">American quotes are not European Black-Scholes observations; the server will reject this inversion.</p>
      )}
      {busy && <Loading label="Inverting…" />}
      <ErrorBox error={error} />
      {!res && !busy && !error && <Empty>Enter a quote or a bid/ask pair and invert.</Empty>}
      {res && (
        <>
          <p className="small muted">
            T = {sig(res.time_to_expiry, 8)} years · forward {sig(res.forward, 8)} · discount factor {sig(res.discount_factor, 8)} · {res.assumptions.bounds as string}
          </p>
          <div className="cards">
            {res.results.map((r) => (
              <IVCard key={r.label} r={r} />
            ))}
          </div>
        </>
      )}
    </section>
  );
}
