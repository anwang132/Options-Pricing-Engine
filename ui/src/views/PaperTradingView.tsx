import { useCallback, useEffect, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import {
  api,
  type LedgerEvent,
  type MarkResult,
  type PaperAccountRow,
  type PaperHistoryPoint,
  type PaperSummary,
} from "../api/client";
import { Empty, ErrorBox, Field, Loading, StatusBadge } from "../components/common";
import { isDecimalText, percentToDecimal, sig } from "../lib/decimal";
import type { InstrumentForm } from "../lib/instrument";
import { usePalette } from "../lib/theme";
import { useAction } from "../lib/useAction";

/** Current UTC time as a datetime-local value (the fields are labelled UTC). */
const nowUtc = () => new Date().toISOString().slice(0, 16);
const utc = (local: string) => `${local}:00Z`;
const money = (v: string | number | null | undefined, ccy = "") =>
  v === null || v === undefined ? "—" : `${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}${ccy ? ` ${ccy}` : ""}`;

interface TradeDraft {
  underlying: string;
  type: "call" | "put";
  exercise: "european" | "american";
  strike: string;
  expiry: string;
  multiplier: string;
  side: "buy" | "sell";
  quantity: string;
  price: string;
  fees: string;
  timestamp: string;
  note: string;
}

export function PaperTradingView({ form }: { form: InstrumentForm }) {
  const pal = usePalette();
  const { busy, error, setError, run } = useAction();
  const [accounts, setAccounts] = useState<PaperAccountRow[] | null>(null);
  const [accountId, setAccountId] = useState("");
  const [summary, setSummary] = useState<PaperSummary | null>(null);
  const [history, setHistory] = useState<PaperHistoryPoint[]>([]);
  const [events, setEvents] = useState<LedgerEvent[]>([]);
  const [lastMark, setLastMark] = useState<MarkResult | null>(null);
  const [newAcct, setNewAcct] = useState({ name: "My paper account", cash: "10000" });

  const load = useCallback(
    async (id: string) => {
      const out = await run("load", () => Promise.all([api.paperSummary(id), api.paperHistory(id), api.paperEvents(id)]));
      if (out) {
        setSummary(out[0]);
        setHistory(out[1]);
        setEvents(out[2]);
        setLastMark(out[0].last_mark ?? null);
      }
    },
    [run],
  );

  useEffect(() => {
    api.paperAccounts().then(
      (a) => {
        setAccounts(a);
        if (a.length) {
          setAccountId(a[a.length - 1].account_id);
          void load(a[a.length - 1].account_id);
        }
      },
      (e) => setError(e),
    );
  }, [load, setError]);

  const openAccount = async () => {
    // Same minute precision as the trade-time field, so a trade entered right away is not
    // "before the account was opened".
    const s = await run("open", () => api.paperOpen(newAcct.name, newAcct.cash.trim(), utc(nowUtc())));
    if (!s) return;
    setAccounts((a) => [...(a ?? []), { account_id: s.account_id, name: s.name, currency: s.currency }]);
    setAccountId(s.account_id);
    await load(s.account_id);
  };

  const after = async (s: PaperSummary | undefined) => {
    if (s) await load(s.account_id);
  };

  return (
    <section className="panel" aria-labelledby="paper-h">
      <h2 id="paper-h">Paper trading</h2>
      <p className="callout callout--warn">
        <strong>Paper trading only.</strong> No real money moves and nothing here is investment advice. Record the trades you make
        (or would make), then mark them to market prices and to the model to see how your positions and the model hold up over time.
      </p>
      <ErrorBox error={error} />
      <fieldset>
        <legend>Account</legend>
        {accounts === null ? (
          <Loading label="Loading accounts…" />
        ) : (
          <div className="grid">
            {accounts.length > 0 && (
              <Field label="Account">
                {(id) => (
                  <select id={id} value={accountId} onChange={(e) => { setAccountId(e.target.value); void load(e.target.value); }}>
                    {accounts.map((a) => <option key={a.account_id} value={a.account_id}>{a.name} · {a.account_id}</option>)}
                  </select>
                )}
              </Field>
            )}
            <Field label="New account name">{(id) => <input id={id} value={newAcct.name} onChange={(e) => setNewAcct({ ...newAcct, name: e.target.value })} />}</Field>
            <Field label="Starting cash" unit="USD" error={isDecimalText(newAcct.cash) ? undefined : "Must be a number"}>
              {(id) => <input id={id} inputMode="decimal" value={newAcct.cash} onChange={(e) => setNewAcct({ ...newAcct, cash: e.target.value })} />}
            </Field>
            <div>
              <button type="button" className="secondary" onClick={openAccount} disabled={busy !== null || !isDecimalText(newAcct.cash) || !newAcct.name.trim()}>
                Open paper account
              </button>
            </div>
          </div>
        )}
      </fieldset>
      {busy === "load" && !summary && <Loading label="Loading ledger…" />}
      {accounts?.length === 0 && !summary && <Empty>Open a paper account to start recording trades.</Empty>}
      {summary && (
        <>
          <Summary s={summary} mark={lastMark} />
          <TradeForm accountId={summary.account_id} form={form} onDone={after} run={run} busy={busy} />
          <MarkPanel s={summary} form={form} run={run} busy={busy} onChanged={() => load(summary.account_id)} />
          {history.length > 0 && (
            <figure className="chart">
              <figcaption>P&amp;L over time at each mark ({summary.currency}). Total = net realized + unrealized.</figcaption>
              <ResponsiveContainer width="100%" height={260}>
                <LineChart data={history.map((h) => ({ t: h.as_of.slice(0, 16).replace("T", " "), total: Number(h.total_pnl), realized: Number(h.net_realized), unrealized: Number(h.unrealized) }))} margin={{ top: 10, right: 20, bottom: 20, left: 10 }}>
                  <CartesianGrid stroke={pal.grid} />
                  <XAxis dataKey="t" stroke={pal.ink2} />
                  <YAxis stroke={pal.ink2} tickFormatter={(v: number) => sig(v, 3)} width={70} />
                  <Tooltip formatter={(v) => money(Number(v))} />
                  <Legend verticalAlign="top" itemSorter={null} />
                  <ReferenceLine y={0} stroke={pal.ink2} />
                  <Line dataKey="total" name="total P&L" stroke={pal.series[0]} strokeWidth={2} dot={{ r: 4, fill: pal.series[0], stroke: pal.surface, strokeWidth: 2 }} isAnimationActive={false} />
                  <Line dataKey="realized" name="net realized" stroke={pal.series[1]} strokeWidth={2} dot={{ r: 4, fill: pal.series[1], stroke: pal.surface, strokeWidth: 2 }} isAnimationActive={false} />
                  <Line dataKey="unrealized" name="unrealized" stroke={pal.series[2]} strokeWidth={2} dot={{ r: 4, fill: pal.series[2], stroke: pal.surface, strokeWidth: 2 }} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </figure>
          )}
          <EventLog s={summary} events={events} run={run} onDone={after} />
        </>
      )}
    </section>
  );
}

type Run = ReturnType<typeof useAction>["run"];

function Summary({ s, mark }: { s: PaperSummary; mark: MarkResult | null }) {
  return (
    <div className="result">
      <div className="hero">
        <div>
          <div className="hero__label">Equity at last mark</div>
          <div className="hero__value">{mark ? money(mark.equity, s.currency) : "not marked yet"}</div>
          <div className="hero__sub">{mark ? `marked ${mark.as_of.slice(0, 16).replace("T", " ")} UTC` : "Mark the positions below to value them"}</div>
        </div>
        <div>
          <div className="hero__label">Total P&amp;L at last mark</div>
          <div className="hero__value hero__value--small">{mark ? money(mark.total_pnl, s.currency) : "—"}</div>
        </div>
      </div>
      <dl className="kv">
        <div><dt>Cash (now)</dt><dd>{money(s.cash, s.currency)}</dd></div>
        <div><dt>Starting cash</dt><dd>{money(s.starting_cash, s.currency)}</dd></div>
        <div><dt>Net realized P&amp;L</dt><dd>{money(s.net_realized, s.currency)}</dd></div>
        <div><dt>Fees paid</dt><dd>{money(s.fees, s.currency)}</dd></div>
        <div><dt>Unrealized at last mark</dt><dd>{mark ? money(mark.unrealized, s.currency) : "—"}</dd></div>
        <div><dt>Trades recorded</dt><dd>{s.trades}{s.voided_trades.length ? ` (${s.voided_trades.length} void)` : ""}</dd></div>
        <div><dt>Ledger integrity</dt><dd><StatusBadge status={s.integrity.intact ? "ok" : "failed"} /> {s.integrity.events} events, hash-chained</dd></div>
      </dl>
    </div>
  );
}

function TradeForm(props: { accountId: string; form: InstrumentForm; onDone: (s: PaperSummary | undefined) => Promise<void>; run: Run; busy: string | null }) {
  const { accountId, form, onDone, run, busy } = props;
  const fromPanel = (): Partial<TradeDraft> => ({
    underlying: form.underlying, type: form.optionType, exercise: form.exercise, strike: form.strike, expiry: form.expiry, multiplier: form.multiplier,
  });
  const [t, setT] = useState<TradeDraft>({
    underlying: form.underlying, type: form.optionType, exercise: form.exercise, strike: form.strike, expiry: form.expiry, multiplier: form.multiplier,
    side: "buy", quantity: "1", price: "", fees: "0.65", timestamp: nowUtc(), note: "",
  });
  const set = (patch: Partial<TradeDraft>) => setT({ ...t, ...patch });
  const nums = [t.strike, t.multiplier, t.quantity, t.price, t.fees].every(isDecimalText);
  const submit = async () =>
    onDone(
      await run("trade", () =>
        api.paperTrade(accountId, {
          timestamp: utc(t.timestamp),
          side: t.side,
          quantity: t.quantity,
          price: t.price,
          fees: t.fees,
          note: t.note,
          contract: { underlying: t.underlying, currency: "USD", strike: t.strike, option_type: t.type, exercise_style: t.exercise, expiry: utc(t.expiry), multiplier: t.multiplier },
        }),
      ),
    );
  return (
    <fieldset>
      <legend>Record a trade</legend>
      <div className="grid">
        <Field label="Side">{(id) => <select id={id} value={t.side} onChange={(e) => set({ side: e.target.value as "buy" | "sell" })}><option value="buy">Buy</option><option value="sell">Sell</option></select>}</Field>
        <Field label="Contracts" hint="Whole number">{(id, d) => <input id={id} aria-describedby={d} inputMode="numeric" value={t.quantity} onChange={(e) => set({ quantity: e.target.value })} />}</Field>
        <Field label="Fill price" unit="per unit">{(id) => <input id={id} inputMode="decimal" value={t.price} onChange={(e) => set({ price: e.target.value })} />}</Field>
        <Field label="Fees" unit="USD total">{(id) => <input id={id} inputMode="decimal" value={t.fees} onChange={(e) => set({ fees: e.target.value })} />}</Field>
        <Field label="Trade time" unit="UTC">{(id) => <input id={id} type="datetime-local" value={t.timestamp} onChange={(e) => set({ timestamp: e.target.value })} />}</Field>
        <Field label="Underlying">{(id) => <input id={id} value={t.underlying} onChange={(e) => set({ underlying: e.target.value })} />}</Field>
        <Field label="Type">{(id) => <select id={id} value={t.type} onChange={(e) => set({ type: e.target.value as "call" | "put" })}><option value="call">Call</option><option value="put">Put</option></select>}</Field>
        <Field label="Exercise">{(id) => <select id={id} value={t.exercise} onChange={(e) => set({ exercise: e.target.value as "european" | "american" })}><option value="european">European</option><option value="american">American</option></select>}</Field>
        <Field label="Strike">{(id) => <input id={id} inputMode="decimal" value={t.strike} onChange={(e) => set({ strike: e.target.value })} />}</Field>
        <Field label="Contract expiry" unit="UTC">{(id) => <input id={id} type="datetime-local" value={t.expiry} onChange={(e) => set({ expiry: e.target.value })} />}</Field>
        <Field label="Multiplier">{(id) => <input id={id} inputMode="decimal" value={t.multiplier} onChange={(e) => set({ multiplier: e.target.value })} />}</Field>
        <Field label="Note">{(id) => <input id={id} value={t.note} maxLength={200} onChange={(e) => set({ note: e.target.value })} />}</Field>
      </div>
      <div className="actions">
        <button type="button" onClick={submit} disabled={busy !== null || !nums || !t.price}>{busy === "trade" ? "Recording…" : "Record trade"}</button>
        <button type="button" className="secondary" onClick={() => set(fromPanel())}>Use contract from the left panel</button>
        <button type="button" className="link" onClick={() => set({ timestamp: nowUtc() })}>Set time to now</button>
      </div>
    </fieldset>
  );
}

function MarkPanel(props: { s: PaperSummary; form: InstrumentForm; run: Run; busy: string | null; onChanged: () => Promise<void> }) {
  const { s, form, run, busy, onChanged } = props;
  const underlyings = [...new Set(s.positions.map((p) => String(p.contract.underlying)))];
  const [markets, setMarkets] = useState<Record<string, { spot: string; rate: string; yld: string }>>({});
  const [inputs, setInputs] = useState<Record<string, { price: string; vol: string }>>({});
  const [asOf, setAsOf] = useState(nowUtc());
  const [settle, setSettle] = useState<Record<string, string>>({});
  const mkt = (u: string) => markets[u] ?? { spot: u === form.underlying ? form.spot : "", rate: form.ratePct, yld: form.yieldPct };
  const inp = (k: string) => inputs[k] ?? { price: "", vol: "" };
  if (!s.positions.length) return <Empty>No open positions. Record a trade to start.</Empty>;
  const ready =
    underlyings.every((u) => isDecimalText(mkt(u).spot) && isDecimalText(mkt(u).rate) && isDecimalText(mkt(u).yld)) &&
    s.positions.every((p) => isDecimalText(inp(p.key).price) || isDecimalText(inp(p.key).vol));
  const markNow = async () => {
    const m = await run("mark", () =>
      api.paperMark(s.account_id, {
        as_of: utc(asOf),
        markets: Object.fromEntries(underlyings.map((u) => [u, { spot: mkt(u).spot, rate: percentToDecimal(mkt(u).rate), dividend_yield: percentToDecimal(mkt(u).yld) }])),
        inputs: Object.fromEntries(
          s.positions.map((p) => [p.key, {
            observed_price: isDecimalText(inp(p.key).price) ? inp(p.key).price : null,
            volatility: isDecimalText(inp(p.key).vol) ? Number(percentToDecimal(inp(p.key).vol)) : null,
          }]),
        ),
      }),
    );
    if (m) await onChanged();
  };
  const lastRows = Object.fromEntries((s.last_mark?.positions ?? []).map((r) => [r.key, r]));
  return (
    <fieldset>
      <legend>Open positions and marks</legend>
      <div className="grid">
        {underlyings.map((u) => (
          <div key={u} className="market-row">
            <strong>{u}</strong>
            <Field label="Spot">{(id) => <input id={id} inputMode="decimal" value={mkt(u).spot} onChange={(e) => setMarkets({ ...markets, [u]: { ...mkt(u), spot: e.target.value } })} />}</Field>
            <Field label="Rate" unit="%">{(id) => <input id={id} inputMode="decimal" value={mkt(u).rate} onChange={(e) => setMarkets({ ...markets, [u]: { ...mkt(u), rate: e.target.value } })} />}</Field>
            <Field label="Div. yield" unit="%">{(id) => <input id={id} inputMode="decimal" value={mkt(u).yld} onChange={(e) => setMarkets({ ...markets, [u]: { ...mkt(u), yld: e.target.value } })} />}</Field>
          </div>
        ))}
        <Field label="Mark time" unit="UTC">{(id) => <input id={id} type="datetime-local" value={asOf} onChange={(e) => setAsOf(e.target.value)} />}</Field>
      </div>
      <div className="table-wrap">
        <table className="editable">
          <thead>
            <tr>
              <th>Position</th><th className="num">Qty</th><th className="num">Avg price</th><th>Market price</th><th>Model vol %</th>
              <th className="num">Last value</th><th className="num">Unrealized</th><th className="num">Model − market</th><th>Expiry settlement</th>
            </tr>
          </thead>
          <tbody>
            {s.positions.map((p) => {
              const last = lastRows[p.key];
              const expiry = String(p.contract.expiry);
              const expired = new Date(expiry) <= new Date();
              return (
                <tr key={p.key}>
                  <th scope="row" className="small">{p.key}</th>
                  <td className="num">{p.quantity}</td>
                  <td className="num">{sig(Number(p.average_price), 6)}</td>
                  <td><input aria-label={`${p.key} market price`} inputMode="decimal" placeholder="observed" value={inp(p.key).price} onChange={(e) => setInputs({ ...inputs, [p.key]: { ...inp(p.key), price: e.target.value } })} /></td>
                  <td><input aria-label={`${p.key} model volatility percent`} inputMode="decimal" placeholder="e.g. 20" value={inp(p.key).vol} onChange={(e) => setInputs({ ...inputs, [p.key]: { ...inp(p.key), vol: e.target.value } })} /></td>
                  <td className="num">{last ? money(last.value) : "—"}</td>
                  <td className="num">{last ? money(last.unrealized) : "—"}</td>
                  <td className="num">{last?.model_minus_market == null ? "—" : sig(last.model_minus_market, 4)}</td>
                  <td>
                    {expired ? (
                      <span className="settle">
                        <input aria-label={`${p.key} settlement price`} inputMode="decimal" placeholder="underlying at expiry" value={settle[p.key] ?? ""} onChange={(e) => setSettle({ ...settle, [p.key]: e.target.value })} />
                        <button type="button" className="link" disabled={!isDecimalText(settle[p.key] ?? "")} onClick={async () => {
                          const out = await run("settle", () => api.paperSettle(s.account_id, String(p.contract.underlying), expiry, settle[p.key], `${new Date().toISOString().slice(0, 19)}Z`));
                          if (out) await onChanged();
                        }}>Settle</button>
                      </span>
                    ) : "open"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="note">Each position needs a market price, a model volatility, or both. The market price is used for value when given; the model price is shown alongside so you can see where the model disagrees with the market.</p>
      <div className="actions">
        <button type="button" onClick={markNow} disabled={busy !== null || !ready}>{busy === "mark" ? "Marking…" : "Mark positions"}</button>
        <button type="button" className="link" onClick={() => setAsOf(nowUtc())}>Set mark time to now</button>
      </div>
    </fieldset>
  );
}

function EventLog(props: { s: PaperSummary; events: LedgerEvent[]; run: Run; onDone: (s: PaperSummary | undefined) => Promise<void> }) {
  const { s, events, run, onDone } = props;
  const [reason, setReason] = useState("");
  const describe = (e: LedgerEvent): string => {
    const p = e.payload as Record<string, unknown>;
    if (e.type === "trade") {
      const c = p.contract as Record<string, unknown>;
      return `${p.trade_id}: ${p.side} ${p.quantity} × ${c.underlying} ${c.option_type} K=${c.strike} @ ${p.price} (fees ${p.fees})${p.note ? ` — ${p.note}` : ""}`;
    }
    if (e.type === "void") return `void ${p.trade_id}: ${p.reason}`;
    if (e.type === "settlement") return `settled ${p.underlying} expiring ${String(p.expiry).slice(0, 10)} at ${p.settlement_price}`;
    if (e.type === "mark") return `mark: equity ${money((p.result as Record<string, string>).equity)}`;
    return `opened with ${money(p.starting_cash as string)} ${p.currency}`;
  };
  return (
    <details>
      <summary>Audit log ({events.length} events, {s.integrity.intact ? "hash chain intact" : `CHAIN BROKEN at event ${s.integrity.first_bad_seq}`})</summary>
      <Field label="Reason for voiding a trade" hint="Voids exclude a trade from positions and cash; the original entry stays in the log">
        {(id, d) => <input id={id} aria-describedby={d} value={reason} onChange={(e) => setReason(e.target.value)} />}
      </Field>
      <div className="table-wrap">
        <table>
          <thead><tr><th className="num">#</th><th>Effective time (UTC)</th><th>Event</th><th>Hash</th><th /></tr></thead>
          <tbody>
            {events.map((e) => {
              const tradeId = e.type === "trade" ? String((e.payload as Record<string, unknown>).trade_id) : null;
              const voided = tradeId !== null && s.voided_trades.includes(tradeId);
              return (
                <tr key={e.seq} className={voided ? "muted" : undefined}>
                  <td className="num">{e.seq}</td>
                  <td className="small">{e.timestamp.slice(0, 19).replace("T", " ")}</td>
                  <td className="small">{describe(e)}{voided ? " (void)" : ""}</td>
                  <td><code className="small">{e.hash.slice(0, 10)}…</code></td>
                  <td>
                    {tradeId && !voided && (
                      <button type="button" className="link" disabled={!reason.trim()} onClick={async () => onDone(await run("void", () => api.paperVoid(s.account_id, tradeId, reason, `${new Date().toISOString().slice(0, 19)}Z`)))}>
                        Void
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </details>
  );
}
