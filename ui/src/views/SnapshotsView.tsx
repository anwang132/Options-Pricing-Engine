import { useCallback, useEffect, useState } from "react";
import { api, type QuoteRow, type SnapshotManifest, type SnapshotSummary } from "../api/client";
import { Empty, ErrorBox, Loading } from "../components/common";
import { useAction } from "../lib/useAction";

const REASON_TEXT: Record<string, string> = {
  zero_bid: "Zero bid (no usable lower price)",
  wide_spread: "Spread wider than 50% of mid",
  crossed_quote: "Bid above ask",
  missing_bid_or_ask: "Bid or ask missing",
  nonfinite_value: "Non-finite number (NaN/Inf)",
  negative_price: "Negative price",
  stale_quote: "Quote older than 15 minutes at snapshot time",
  underlying_time_mismatch: "Quote and spot observed > 60 s apart",
  unparseable_field: "Field could not be parsed",
  duplicate_conflicting_quote: "Duplicate contract with conflicting prices",
  duplicate_identical_quote: "Identical duplicate (first kept)",
  expired_contract: "Expired at snapshot time",
  unsupported_deliverable: "Adjusted / non-standard deliverable",
  unsupported_settlement_lag: "Settlement lag not supported",
  invalid_strike: "Non-positive strike",
  quote_time_unknown: "Quote time unknown (kept, freshness unknown)",
  missing_size: "Bid/ask size missing (kept)",
};

export function SnapshotsView({ onSnapshotsChanged }: { onSnapshotsChanged?: () => void }) {
  const [list, setList] = useState<SnapshotSummary[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [manifest, setManifest] = useState<SnapshotManifest | null>(null);
  const [quotes, setQuotes] = useState<QuoteRow[] | null>(null);
  const { busy, error, setError, run: act } = useAction();

  const refresh = useCallback(async () => {
    try {
      setList(await api.snapshots());
    } catch (e) {
      setError(e);
    }
  }, [setError]);
  useEffect(() => {
    void refresh();
  }, [refresh]);

  const open = async (id: string) => {
    setSelected(id);
    const out = await act("open", () => Promise.all([api.snapshot(id), api.snapshotQuotes(id)]));
    if (out) {
      setManifest(out[0]);
      setQuotes(out[1]);
    }
  };

  /** After a new snapshot is stored: refresh the list, notify other views, open it. */
  const afterIngest = async (snapshotId: string) => {
    await refresh();
    onSnapshotsChanged?.();
    await open(snapshotId);
  };

  const loadBundled = async () => {
    const out = await act("bundled", () => api.loadBundled());
    if (out) await afterIngest(out[0].snapshot_id);
  };

  const upload = async (file: File) => {
    const out = await act("upload", async () => api.uploadSnapshot(await file.text()));
    if (out) await afterIngest(out.manifest.snapshot_id);
  };

  const quarantined = quotes?.filter((q) => !q.accepted) ?? [];
  const flagged = quotes?.filter((q) => q.accepted && q.issues.length) ?? [];

  return (
    <section className="panel" aria-labelledby="snap-h">
      <h2 id="snap-h">Market snapshots</h2>
      <p className="note">
        Snapshots are immutable and content-addressed: the raw file, the normalised quotes and a manifest with hashes,
        parser version and quality policy are stored once. Every excluded quote keeps its reasons. The bundled files are{" "}
        <strong>synthetic</strong> (generated from a known SSVI surface with injected defects); no live data is used.
      </p>
      <div className="actions">
        <button type="button" onClick={loadBundled} disabled={busy !== null}>
          {busy === "bundled" ? "Loading…" : "Load bundled synthetic snapshots"}
        </button>
        <label className="secondary file-button">
          Upload snapshot file (options-snapshot/v1 JSON)
          <input
            type="file"
            accept="application/json,.json"
            onChange={(e) => e.target.files?.[0] && void upload(e.target.files[0])}
            disabled={busy !== null}
          />
        </label>
      </div>
      <ErrorBox error={error} />
      {list === null ? (
        <Loading label="Loading snapshots…" />
      ) : list.length === 0 ? (
        <Empty>No snapshots stored yet. Load the bundled synthetic snapshots or upload a file.</Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Snapshot</th>
                <th scope="col">As of (UTC)</th>
                <th scope="col">Underlying</th>
                <th scope="col">Source</th>
                <th scope="col" className="num">Accepted / total</th>
                <th scope="col" />
              </tr>
            </thead>
            <tbody>
              {list.map((s) => (
                <tr key={s.snapshot_id} className={s.snapshot_id === selected ? "selected" : undefined}>
                  <th scope="row"><code>{s.snapshot_id}</code></th>
                  <td>{s.as_of}</td>
                  <td>{s.underlying}</td>
                  <td>
                    {s.synthetic && <span className="badge badge--warn">synthetic</span>} {s.source}
                  </td>
                  <td className="num">
                    {s.accepted} / {s.total_quotes}
                  </td>
                  <td>
                    <button type="button" className="link" onClick={() => void open(s.snapshot_id)}>
                      Quality report
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {busy === "open" && <Loading label="Loading quality report…" />}
      {manifest && quotes && (
        <div className="result">
          <h3>Quality report — {manifest.snapshot_id}</h3>
          <dl className="kv">
            <div><dt>Spot</dt><dd>{manifest.underlying.spot} {manifest.underlying.currency}</dd></div>
            <div><dt>Spot observed at</dt><dd>{manifest.underlying.spot_observed_at ?? "unknown"}</dd></div>
            <div><dt>Carry inputs</dt><dd>{manifest.carry ? `r ${manifest.carry.rate}, q ${manifest.carry.dividend_yield} (${manifest.carry.source})` : "not supplied (never assumed)"}</dd></div>
            <div><dt>Accepted / quarantined</dt><dd>{manifest.quality.accepted} / {manifest.quality.quarantined} of {manifest.quality.total_quotes}</dd></div>
            <div><dt>Quote times known / unknown</dt><dd>{manifest.quality.freshness.quotes_with_known_time} / {manifest.quality.freshness.quotes_with_unknown_time}</dd></div>
            <div><dt>Raw file SHA-256</dt><dd><code className="small">{manifest.raw_sha256}</code></dd></div>
          </dl>
          <div className="two-col">
            <div>
              <h4>Quarantine reasons (counts per reason)</h4>
              <ReasonBars counts={manifest.quality.quarantine_reasons} />
            </div>
            <div>
              <h4>Flags (quote kept, caveat recorded)</h4>
              <ReasonBars counts={manifest.quality.flags_on_kept_or_quarantined} />
            </div>
          </div>
          <details open>
            <summary>Quarantined quotes ({quarantined.length})</summary>
            <QuoteTable rows={quarantined} />
          </details>
          <details>
            <summary>Accepted quotes with flags ({flagged.length})</summary>
            <QuoteTable rows={flagged} />
          </details>
        </div>
      )}
    </section>
  );
}

function ReasonBars({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  if (!entries.length) return <Empty>None.</Empty>;
  const max = Math.max(...entries.map(([, n]) => n));
  return (
    <ul className="bars">
      {entries.map(([k, n]) => (
        <li key={k}>
          <span className="bars__label" title={k}>{REASON_TEXT[k] ?? k}</span>
          <span className="bars__track" aria-hidden="true">
            <span className="bars__fill" style={{ width: `${(n / max) * 100}%` }} />
          </span>
          <span className="bars__value">{n}</span>
        </li>
      ))}
    </ul>
  );
}

function QuoteTable({ rows }: { rows: QuoteRow[] }) {
  if (!rows.length) return <Empty>None.</Empty>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col" className="num">Row</th>
            <th scope="col">Contract</th>
            <th scope="col">Type</th>
            <th scope="col" className="num">Strike</th>
            <th scope="col">Expiry</th>
            <th scope="col">Exercise</th>
            <th scope="col" className="num">Bid</th>
            <th scope="col" className="num">Ask</th>
            <th scope="col">Quote time</th>
            <th scope="col">Reasons</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((q) => (
            <tr key={q.row}>
              <td className="num">{q.row}</td>
              <td className="small">{q.contract_id ?? "—"}</td>
              <td>{q.option_type ?? "—"}</td>
              <td className="num">{q.strike ?? "—"}</td>
              <td className="small">{q.expiry?.slice(0, 10) ?? "—"}</td>
              <td>{q.exercise_style ?? "unknown"}</td>
              <td className="num">{q.bid ?? "—"}</td>
              <td className="num">{q.ask ?? "—"}</td>
              <td className="small">{q.quote_time ?? "unknown"}</td>
              <td className="small">{q.issues.map((i) => REASON_TEXT[i] ?? i).join("; ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
