import { lazy, Suspense, useEffect, useState } from "react";
import { api, type EngineInfo, type PriceRequest, type PriceResponse } from "../api/client";
import { EngineSettings, DEFAULT_CONFIGS, type EngineConfig } from "../components/EngineSettings";
import { Empty, ErrorBox, Loading, downloadJson } from "../components/common";
import { PriceResult } from "../components/PriceResult";
import { useAction } from "../lib/useAction";
import { instrumentPayload, modelPayload, supportedEngines, type InstrumentForm } from "../lib/instrument";

// Charts load on demand so the pricing form does not wait for the chart library.
const ProfileCharts = lazy(() => import("../components/ProfileCharts").then((m) => ({ default: m.ProfileCharts })));

interface Props {
  form: InstrumentForm;
  engines: EngineInfo[];
  valid: boolean;
}

export function PriceView({ form, engines, valid }: Props) {
  const supported = supportedEngines(engines, form);
  const [config, setConfig] = useState<EngineConfig>(DEFAULT_CONFIGS.bsm_analytic);
  const [result, setResult] = useState<PriceResponse | null>(null);
  const { busy, error, run: act } = useAction();

  // If the chosen engine stops being supported (e.g. American selected), fall back.
  useEffect(() => {
    if (supported.length && !supported.some((e) => e.engine_id === config.engine)) {
      setConfig(DEFAULT_CONFIGS[supported[0].engine_id]);
    }
  }, [supported, config.engine]);

  const body = (): PriceRequest => ({
    schema_version: "1",
    ...instrumentPayload(form),
    model: modelPayload(form),
    engine: config,
  });

  // Profiles are drawn for the request that produced the displayed result.
  const [profileBody, setProfileBody] = useState<PriceRequest | null>(null);
  const run = async () => {
    const request = body();
    const out = await act("price", () => api.price(request));
    setResult(out ?? null);
    setProfileBody(out ? request : null);
  };

  const manifest = async () => {
    const m = await act("manifest", () => api.manifest(body()));
    if (m) downloadJson(`run-manifest-${m.result.request_hash.slice(0, 12)}.json`, m);
  };

  return (
    <section className="panel" aria-labelledby="price-h">
      <h2 id="price-h">Price and Greeks</h2>
      <EngineSettings engines={engines} supported={supported} config={config} onChange={setConfig} />
      <div className="actions">
        <button type="button" onClick={run} disabled={!valid || busy !== null || !supported.length}>
          {busy === "price" ? "Pricing…" : "Price"}
        </button>
        <button type="button" className="secondary" onClick={manifest} disabled={!valid || busy !== null}>
          Download run manifest
        </button>
        {!valid && <span className="error-text">Fix the highlighted inputs first.</span>}
      </div>
      <p className="note">
        The run manifest contains the exact request, result and environment (package, NumPy/SciPy versions, git
        state) and can be replayed with <code>options-engine replay manifest.json</code>.
      </p>
      {busy === "price" && <Loading label="Computing…" />}
      <ErrorBox error={error} />
      {result && <PriceResult result={result} />}
      {result && profileBody && (
        <Suspense fallback={<Loading label="Loading charts…" />}>
          <ProfileCharts body={profileBody} american={profileBody.contract.exercise_style === "american"} />
        </Suspense>
      )}
      {!result && !error && !busy && <Empty>Run a calculation to see the price, Greeks, assumptions and diagnostics.</Empty>}
    </section>
  );
}
