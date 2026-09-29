import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const ENGINES = [
  { engine_id: "bsm_analytic", version: "1.0.0", description: "Closed-form", config_type: "AnalyticConfig",
    capabilities: { exercise_styles: ["european"], dividend_treatments: ["continuous_yield", "escrowed_cash"], model_families: ["black_scholes"], greeks: { delta: "analytic" }, stochastic: false, batching: true, diagnostics: [], zero_volatility: true } },
  { engine_id: "mc_terminal_gbm", version: "1.0.0", description: "MC", config_type: "MonteCarloConfig",
    capabilities: { exercise_styles: ["european"], dividend_treatments: ["continuous_yield"], model_families: ["black_scholes"], greeks: { delta: "pathwise" }, stochastic: true, batching: false, diagnostics: [], zero_volatility: false } },
  { engine_id: "crr_tree", version: "1.0.0", description: "Tree", config_type: "CRRConfig",
    capabilities: { exercise_styles: ["european", "american"], dividend_treatments: ["continuous_yield"], model_families: ["black_scholes"], greeks: { delta: "tree_nodes" }, stochastic: false, batching: false, diagnostics: [], zero_volatility: false } },
];

function mockFetch(handler: (url: string, body: unknown) => { status: number; body: unknown }) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const r = handler(url, init?.body ? JSON.parse(init.body as string) : undefined);
    return new Response(JSON.stringify(r.body), { status: r.status });
  }));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("disables engines that do not support American exercise", async () => {
    mockFetch(() => ({ status: 200, body: ENGINES }));
    render(<App />);
    await screen.findByText("Closed-form BSM");
    await userEvent.selectOptions(screen.getByLabelText("Exercise style"), "american");
    expect(screen.getByRole("radio", { name: /Closed-form BSM/ })).toBeDisabled();
    expect(screen.getByRole("radio", { name: /CRR binomial tree/ })).toBeEnabled();
    expect(screen.getByRole("radio", { name: /CRR binomial tree/ })).toBeChecked();
  });

  it("sends percent inputs as exact decimal strings and shows structured errors", async () => {
    let sent: any = null;
    mockFetch((url, body) => {
      if (url.endsWith("/engines")) return { status: 200, body: ENGINES };
      sent = body;
      return { status: 422, body: { error: { code: "expired_contract", message: "valuation time is after expiry", details: {} } } };
    });
    render(<App />);
    await screen.findByText("Closed-form BSM");
    await userEvent.click(screen.getByRole("button", { name: "Price" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Contract has expired"));
    expect(sent.market.rate).toBe("0.10");
    expect(sent.model.volatility).toBe(0.2);
    expect(sent.contract.expiry).toBe("2027-03-30T08:00:00Z");
  });

  it("blocks submission on invalid input", async () => {
    mockFetch(() => ({ status: 200, body: ENGINES }));
    render(<App />);
    await screen.findByText("Closed-form BSM");
    const strike = screen.getByLabelText(/^Strike/);
    await userEvent.clear(strike);
    await userEvent.type(strike, "-5");
    expect(screen.getByText("Strike must be positive")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Price" })).toBeDisabled();
  });
});
