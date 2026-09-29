import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { api, type EngineInfo } from "./api/client";
import { ErrorBox, Loading } from "./components/common";
import { InstrumentPanel } from "./components/InstrumentPanel";
import { DEFAULT_FORM, validate, type InstrumentForm } from "./lib/instrument";
import { EnginesView } from "./views/EnginesView";
import { ImpliedVolView } from "./views/ImpliedVolView";
import { PriceView } from "./views/PriceView";
import { SnapshotsView } from "./views/SnapshotsView";

// Chart-heavy views load on demand so the first paint does not wait for the chart library.
const CompareView = lazy(() => import("./views/CompareView").then((m) => ({ default: m.CompareView })));
const ConvergenceView = lazy(() => import("./views/ConvergenceView").then((m) => ({ default: m.ConvergenceView })));
const SurfaceView = lazy(() => import("./views/SurfaceView").then((m) => ({ default: m.SurfaceView })));
const PortfolioView = lazy(() => import("./views/PortfolioView").then((m) => ({ default: m.PortfolioView })));
const PaperTradingView = lazy(() => import("./views/PaperTradingView").then((m) => ({ default: m.PaperTradingView })));

const TABS = [
  { id: "price", label: "Price & Greeks" },
  { id: "compare", label: "Compare engines" },
  { id: "convergence", label: "Convergence" },
  { id: "iv", label: "Implied volatility" },
  { id: "snapshots", label: "Market data" },
  { id: "surface", label: "Surface" },
  { id: "portfolio", label: "Portfolio" },
  { id: "paper", label: "Paper trading" },
  { id: "engines", label: "Capabilities" },
] as const;
type Tab = (typeof TABS)[number]["id"];

export default function App() {
  const [form, setForm] = useState<InstrumentForm>(DEFAULT_FORM);
  const [tab, setTab] = useState<Tab>("price");
  const [engines, setEngines] = useState<EngineInfo[] | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [dataVersion, setDataVersion] = useState(0); // bumps when snapshots or fits change
  const bump = () => setDataVersion((v) => v + 1);
  const errors = useMemo(() => validate(form), [form]);
  const valid = Object.keys(errors).length === 0;

  useEffect(() => {
    api.engines().then(setEngines, setLoadError);
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <h1>Options pricing &amp; validation workbench</h1>
        <p>Black-Scholes-Merton pricing, validation, snapshots, SSVI surfaces and scenarios · offline, synthetic inputs</p>
      </header>
      <main className="layout">
        {tab !== "snapshots" && tab !== "surface" ? (
          <InstrumentPanel form={form} errors={errors} onChange={setForm} showVolatility={tab !== "iv"} />
        ) : (
          <aside className="panel note">
            Snapshot and surface views work on stored market snapshots; the contract and market inputs panel returns on
            the pricing and portfolio tabs.
          </aside>
        )}
        <div className="workspace">
          <nav className="tabs" role="tablist" aria-label="Workbench views">
            {TABS.map((t) => (
              <button
                key={t.id}
                role="tab"
                id={`tab-${t.id}`}
                aria-selected={tab === t.id}
                aria-controls={`panel-${t.id}`}
                className={tab === t.id ? "tab tab--active" : "tab"}
                onClick={() => setTab(t.id)}
              >
                {t.label}
              </button>
            ))}
          </nav>
          <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
            {loadError ? (
              <ErrorBox error={loadError} />
            ) : !engines ? (
              <Loading label="Loading engine capabilities…" />
            ) : (
              <Suspense fallback={<Loading label="Loading view…" />}>
                {tab === "price" && <PriceView form={form} engines={engines} valid={valid} />}
                {tab === "compare" && <CompareView form={form} valid={valid} />}
                {tab === "convergence" && <ConvergenceView form={form} valid={valid} />}
                {tab === "iv" && <ImpliedVolView form={form} valid={valid} />}
                {tab === "snapshots" && <SnapshotsView onSnapshotsChanged={bump} />}
                {tab === "surface" && <SurfaceView version={dataVersion} onFitsChanged={bump} />}
                {tab === "portfolio" && <PortfolioView form={form} version={dataVersion} />}
                {tab === "paper" && <PaperTradingView form={form} />}
                {tab === "engines" && <EnginesView engines={engines} />}
              </Suspense>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
