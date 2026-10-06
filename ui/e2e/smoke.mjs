// End-to-end smoke test against a running server using the locally installed Chrome.
// Usage: node e2e/smoke.mjs http://127.0.0.1:8765 <screenshot-dir>
import { chromium } from "playwright-core";

const base = process.argv[2] ?? "http://127.0.0.1:8000";
const out = process.argv[3] ?? "e2e-screens";
const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
// Chrome logs intentional 4xx domain errors as console errors; those are expected here.
page.on("console", (m) => m.type() === "error" && !/status of 4\d\d/.test(m.text()) && errors.push(m.text()));
const check = (cond, msg) => { if (!cond) throw new Error(`FAILED: ${msg}`); console.log(`ok - ${msg}`); };
// Select the first option whose text matches (snapshot order among equal timestamps is by id hash).
const pick = async (label, re) => {
  const sel = page.getByLabel(label);
  await sel.locator("option", { hasText: re }).first().waitFor({ state: "attached" });
  const texts = await sel.locator("option").allTextContents();
  const i = texts.findIndex((t) => re.test(t));
  if (i < 0) throw new Error(`FAILED: no option matching ${re} in ${label}`);
  await sel.selectOption({ index: i });
};

await page.goto(base);
await page.getByText("Closed-form BSM").first().waitFor();
await page.getByRole("button", { name: "Price", exact: true }).click();
await page.getByText("Price per underlying unit").waitFor();
check((await page.locator(".hero__value").first().innerText()).startsWith("4.759422393"), "analytic price 4.759422393 shown");
await page.getByText("Value and risk profiles").waitFor();
await page.getByText(/Option value per unit against spot/).waitFor();
check((await page.locator(".small-multiples figure").count()) === 4, "four Greek profile charts rendered");
await page.waitForTimeout(300);
await page.screenshot({ path: `${out}/01-price.png`, fullPage: true });

// Monte Carlo with uncertainty
await page.getByRole("radio", { name: /Monte Carlo/ }).check();
await page.getByRole("button", { name: "Price", exact: true }).click();
await page.getByText(/standard error ·/).waitFor();
check(await page.getByText("not supported").first().isVisible(), "MC gamma reported as not supported");
await page.screenshot({ path: `${out}/02-price-mc.png`, fullPage: true });

// Failure: tree with too few steps and low vol
await page.getByLabel(/Volatility \(model\)/).fill("1");
await page.getByRole("radio", { name: /CRR binomial tree/ }).check();
await page.getByRole("textbox", { name: "Steps" }).fill("10");
await page.getByRole("button", { name: "Price", exact: true }).click();
await page.getByText("Tree setup rejected").waitFor();
check(await page.getByText(/Minimum valid tree steps/).isVisible(), "invalid tree probability explained with minimum steps");
await page.screenshot({ path: `${out}/03-tree-rejected.png`, fullPage: true });
await page.getByLabel(/Volatility \(model\)/).fill("20");

// Compare
await page.getByRole("tab", { name: "Compare engines" }).click();
await page.getByRole("button", { name: "Compare engines" }).click();
await page.getByText(/standard errors;/).waitFor();
await page.screenshot({ path: `${out}/04-compare.png`, fullPage: true });

// Convergence
await page.getByRole("tab", { name: "Convergence" }).click();
await page.getByRole("button", { name: "Run tree study" }).click();
await page.getByText(/\|CRR − closed form\|/).waitFor();
await page.getByRole("button", { name: "Run Monte Carlo study" }).click();
await page.getByText(/Estimate with 95% confidence band/).waitFor();
await page.waitForTimeout(300);
await page.screenshot({ path: `${out}/05-convergence.png`, fullPage: true });

// Implied vol: bid/ask then impossible quote then low vega
await page.getByRole("tab", { name: "Implied volatility" }).click();
await page.getByRole("button", { name: "Invert" }).click();
await page.getByRole("heading", { name: /bid: 4.7/ }).waitFor();
await page.screenshot({ path: `${out}/06-iv.png`, fullPage: true });
await page.getByRole("button", { name: "Example: impossible quote" }).click();
await page.getByRole("button", { name: "Invert" }).click();
await page.getByText("below lower bound").waitFor();
check(await page.getByText("No implied volatility").isVisible(), "impossible quote yields no IV");
await page.screenshot({ path: `${out}/07-iv-impossible.png`, fullPage: true });
await page.getByLabel(/^Strike/).fill("60");
await page.getByLabel(/^Expiry/).fill("2026-10-28T20:00");
await page.getByLabel(/Single quote/).fill("0.01");
await page.getByRole("button", { name: "Invert" }).click();
await page.getByText("unstable low vega").waitFor();
await page.screenshot({ path: `${out}/08-iv-low-vega.png`, fullPage: true });

// American -> engine filtering, early-exercise boundary, capability matrix
await page.getByRole("tab", { name: "Price & Greeks" }).click();
await page.getByLabel(/^Strike/).fill("40");
await page.getByLabel(/^Expiry/).fill("2027-03-30T08:00");
await page.getByLabel("Option type").selectOption("put");
await page.getByLabel("Exercise style").selectOption("american");
check(await page.getByRole("radio", { name: /Closed-form BSM/ }).isDisabled(), "closed form disabled for American");
await page.getByRole("button", { name: "Price", exact: true }).click();
await page.getByText("Early-exercise boundary").waitFor({ timeout: 30000 });
check(await page.getByText(/exercising immediately is optimal at or below the boundary/).isVisible(), "put exercise boundary explained");
await page.waitForTimeout(300);
await page.screenshot({ path: `${out}/09b-american-boundary.png`, fullPage: true });
await page.getByRole("radio", { name: /Longstaff–Schwartz MC/ }).check();
await page.getByRole("button", { name: "Price", exact: true }).click();
await page.getByText(/standard error ·/).waitFor({ timeout: 30000 });
check(await page.getByText(/regression Greeks are biased/).first().isVisible(), "LSM Greeks reported as not supported with reason");
await page.screenshot({ path: `${out}/09d-american-lsm.png`, fullPage: true });
await page.getByLabel(/Andersen–Broadie upper bound/).check();
await page.getByRole("textbox", { name: "Outer paths" }).fill("300");
await page.getByRole("textbox", { name: "Inner paths" }).fill("50");
await page.getByRole("button", { name: "Price", exact: true }).click();
await page.getByText(/Bermudan price bracket/).waitFor({ timeout: 60000 });
check(await page.getByText(/Bermudan price bracket/).isVisible(), "LSM dual upper bound brackets the price");
await page.getByLabel("Option type").selectOption("call");

// Heston: model selector, engine routing, implied-vol smile
await page.getByLabel("Exercise style").selectOption("european");
await page.getByLabel(/^Strike/).fill("42");
await page.getByLabel(/^Model/).selectOption("heston");
check(await page.getByRole("radio", { name: /Heston \(Fourier\)/ }).isChecked(), "Heston engine selected for the Heston model");
check(await page.getByRole("radio", { name: /Closed-form BSM/ }).isDisabled(), "BSM engines disabled for the Heston model");
await page.getByRole("button", { name: "Price", exact: true }).click();
await page.getByText("Implied-volatility smile").waitFor({ timeout: 30000 });
check(await page.getByText(/Feller condition/).isVisible(), "Feller condition reported");
check((await page.locator(".small-multiples figure").count()) === 3, "vega chart omitted for Heston (not supported)");
await page.waitForTimeout(300);
await page.screenshot({ path: `${out}/09c-heston-smile.png`, fullPage: true });
await page.getByLabel(/^Model/).selectOption("black_scholes");
await page.getByLabel(/^Strike/).fill("40");

// Hedging experiment: GBM world (Black-Scholes model), then Heston world
await page.getByRole("tab", { name: "Hedging" }).click();
await page.getByRole("button", { name: "Run hedging experiment" }).click();
await page.getByText(/Standard deviation of the hedged P&L/).waitFor({ timeout: 60000 });
const slopeText = await page.getByText(/Standard deviation of the hedged P&L/).innerText();
const slope = Number(/slope (-?\d+(?:\.\d+)?)/.exec(slopeText)?.[1]);
check(slope < -0.4 && slope > -0.6, `GBM hedging error falls like N^-1/2 (slope ${slope})`);
await page.waitForTimeout(300);
await page.screenshot({ path: `${out}/09e-hedging-gbm.png`, fullPage: true });
await page.getByLabel(/^Model/).selectOption("heston");
await page.getByLabel(/Rebalancing counts/).fill("8,16");
await page.getByLabel(/^Paths/).fill("1000");
await page.getByRole("button", { name: "Run hedging experiment" }).click();
await page.getByRole("cell", { name: "Minimum-variance delta" }).first().waitFor({ timeout: 60000 });
check(await page.getByText(/Heston world \(Andersen QE paths\)/).isVisible(), "Heston-world hedging ran all strategies");
await page.screenshot({ path: `${out}/09f-hedging-heston.png`, fullPage: true });
await page.getByLabel(/^Model/).selectOption("black_scholes");
await page.getByRole("tab", { name: "Price & Greeks" }).click();

await page.getByRole("tab", { name: "Capabilities" }).click();
await page.screenshot({ path: `${out}/09-capabilities.png`, fullPage: true });

// Release B: snapshots -> quality report
await page.getByRole("tab", { name: "Market data" }).click();
await page.getByRole("button", { name: "Load bundled synthetic snapshots" }).click();
await page.getByRole("heading", { name: /Quality report/ }).waitFor();
check(await page.getByText("Bid above ask").first().isVisible(), "crossed quote shown with its reason");
check(await page.getByText("Quote time unknown (kept, freshness unknown)").first().isVisible(), "unknown quote time flagged");
await page.screenshot({ path: `${out}/11-snapshots.png`, fullPage: true });

// Surface fit with later-snapshot evaluation
await page.getByRole("tab", { name: "Surface" }).click();
await pick("Snapshot to fit", /09-28T20:00 · synthetic SSVI/);
await pick("Later snapshot (optional)", /09-29T20:00 · synthetic SSVI/);
await page.getByRole("button", { name: "Fit surface" }).click();
await page.getByText("Parametric guarantee (conditional)").waitFor();
check(await page.getByText(/no violations detected on the tested grid/).isVisible(), "sampled arbitrage statement shown");
check(await page.getByText(/Later snapshot \(\+1 d, no refit\)/).isVisible(), "later snapshot evaluated without refit");
await page.getByRole("img", { name: "Implied volatility heat map" }).waitFor();
await page.locator("svg.heatmap-svg rect.cell").nth(200).hover();
check(await page.getByText(/implied vol \d/).isVisible(), "heat map hover shows the cell value");
check(await page.getByText(/Risk-neutral density of k/).isVisible(), "risk-neutral density chart rendered");
await page.waitForTimeout(300);
await page.screenshot({ path: `${out}/12-surface.png`, fullPage: true });

// Heston calibration on the Heston-generated pair
await pick("Snapshot to fit", /09-28T20:00 · synthetic Heston/);
await pick("Later snapshot (optional)", /09-29T20:00 · synthetic Heston/);
await page.getByRole("button", { name: "Calibrate Heston" }).click();
await page.getByText(/Later, only v₀ refitted/).waitFor({ timeout: 60000 });
check(await page.getByText(/Synthetic data generated by Heston/).isVisible(), "Heston calibration recovers the generating model");
await page.waitForTimeout(300);
await page.screenshot({ path: `${out}/12b-heston-calibration.png`, fullPage: true });

// Portfolio driven by the surface (sticky moneyness)
await page.getByRole("tab", { name: "Portfolio" }).click();
await page.getByLabel(/^Spot/).fill("4500");
await page.getByLabel(/Risk-free rate/).fill("4");
await page.getByLabel(/Dividend yield/).fill("1.5");
await page.getByLabel(/Underlying id/).fill("SYNTH-IDX");
for (const [i, strike] of [[1, "4500"], [2, "4700"], [3, "4100"]]) {
  await page.getByLabel(`Position ${i} strike`).fill(strike);
  await page.getByLabel(`Position ${i} volatility percent`).fill("");
}
await page.getByLabel("Surface fit").selectOption({ index: 1 });
await page.getByLabel("Surface dynamics").selectOption("sticky_moneyness");
await page.getByRole("button", { name: "Run scenarios" }).click();
await page.getByText("Portfolio value").waitFor();
check(await page.locator("table.heatmap td").count() >= 21, "P&L heat map rendered");
await page.waitForTimeout(300);
await page.screenshot({ path: `${out}/13-portfolio.png`, fullPage: true });

// Portfolio failure: mismatched underlying vs the fitted surface
await page.getByLabel(/Underlying id/).fill("OTHER");
await page.getByRole("button", { name: "Run scenarios" }).click();
await page.getByText(/fitted for 'SYNTH-IDX'/).waitFor();
check(true, "surface/underlying mismatch rejected with explanation");

// Paper trading: open account, trade, mark, void
await page.getByRole("tab", { name: "Paper trading" }).click();
await page.getByText("Paper trading only.").waitFor();
const panel = page.locator('section[aria-labelledby="inputs-h"]'); // the left contract panel
await panel.getByLabel(/^Underlying id/).fill("SYNTH");
await panel.getByLabel(/^Spot/).fill("42");
await panel.getByLabel(/^Strike/).fill("40");
await panel.getByLabel(/^Expiry/).fill("2027-03-30T08:00");
await panel.getByLabel("Option type").selectOption("call");
await panel.getByLabel("Exercise style").selectOption("european");
await page.getByRole("button", { name: "Open paper account" }).click();
await page.getByText("Record a trade", { exact: true }).waitFor();
await page.getByRole("button", { name: "Use contract from the left panel" }).click();
await page.getByLabel(/^Fill price/).fill("4.50");
await page.getByLabel(/^Contracts/).fill("2");
await page.getByRole("button", { name: "Record trade" }).click();
await page.getByText("Open positions and marks").waitFor();
const key = await page.locator("fieldset table.editable tbody th").first().innerText();
await page.getByLabel(`${key} market price`).fill("4.90");
await page.getByLabel(`${key} model volatility percent`).fill("20");
await page.getByRole("button", { name: "Mark positions" }).click();
await page.getByText(/P&L over time at each mark/).waitFor();
// cash 10000 - 2*100*4.50 - 0.65 = 9099.35; value 980 -> equity 10079.35; P&L 79.35
check((await page.locator(".hero__value").first().innerText()).includes("10,079.35"), "paper equity after mark = 10,079.35");
check((await page.locator(".hero__value").nth(1).innerText()).includes("79.35"), "total P&L 79.35 (80 unrealized - 0.65 fees)");
await page.getByText(/Audit log/).click();
check(await page.getByText(/hash chain intact/).isVisible(), "ledger hash chain intact");
await page.waitForTimeout(300);
await page.screenshot({ path: `${out}/14-paper.png`, fullPage: true });
await page.getByLabel("Reason for voiding a trade").fill("test entry");
await page.getByRole("button", { name: "Void" }).click();
await page.getByText(/\(1 void\)/).waitFor();
check(true, "trade voided with reason and kept in the audit log");

// Narrow viewport
await page.setViewportSize({ width: 390, height: 900 });
await page.getByRole("tab", { name: "Price & Greeks" }).click();
const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
check(!overflow, "no horizontal page scroll at 390px");
await page.screenshot({ path: `${out}/10-mobile.png`, fullPage: false });

check(errors.length === 0, `no console/page errors (${errors.join(" | ")})`);
await browser.close();
