// Regenerate the README images from a running server (synthetic inputs only).
// Usage: node e2e/screenshots.mjs http://127.0.0.1:8765 ../docs/images
// Use a fresh OPTIONS_ENGINE_DATA_DIR: the script loads the bundled synthetic snapshots.
import { chromium } from "playwright-core";

const base = process.argv[2] ?? "http://127.0.0.1:8000";
const out = process.argv[3] ?? "../docs/images";
const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1 });
const shot = async (locator, name) => {
  await page.mouse.move(0, 0); // no hover tooltips in the images
  await page.waitForTimeout(400); // let chart transitions settle
  await locator.screenshot({ path: `${out}/${name}.png` });
  console.log(`wrote ${out}/${name}.png`);
};
const figure = (caption) => page.locator("figure.chart").filter({ hasText: caption }).first();

await page.goto(base);
await page.getByText("Closed-form BSM").first().waitFor();

// Price & Greeks with profiles (Hull example)
await page.getByRole("button", { name: "Price", exact: true }).click();
await page.getByText(/Option value per unit against spot/).waitFor();
await page.waitForTimeout(400);
await page.screenshot({ path: `${out}/workbench.png` }); // first viewport: inputs, price, Greeks
console.log(`wrote ${out}/workbench.png`);

// Heston smile
await page.getByLabel(/^Model/).selectOption("heston");
await page.getByRole("button", { name: "Price", exact: true }).click();
await page.getByText("Implied-volatility smile").waitFor({ timeout: 30000 });
await shot(figure("Implied-volatility smile"), "heston-smile");
await page.getByLabel(/^Model/).selectOption("black_scholes");

// American put: early-exercise boundary
await page.getByLabel("Option type").selectOption("put");
await page.getByLabel("Exercise style").selectOption("american");
await page.getByRole("button", { name: "Price", exact: true }).click();
await page.getByText("Early-exercise boundary").waitFor({ timeout: 30000 });
await shot(figure("Early-exercise boundary"), "exercise-boundary");
await page.getByLabel("Option type").selectOption("call");
await page.getByLabel("Exercise style").selectOption("european");

// Hedging experiment in the GBM world, then the Heston world
await page.getByRole("tab", { name: "Hedging" }).click();
await page.getByRole("button", { name: "Run hedging experiment" }).click();
await page.getByText(/Standard deviation of the hedged P&L/).waitFor({ timeout: 60000 });
await shot(figure("Standard deviation of the hedged P&L"), "hedging-gbm");
await page.getByLabel(/^Model/).selectOption("heston");
await page.getByLabel(/^Paths/).fill("3000");
await page.getByRole("button", { name: "Run hedging experiment" }).click();
await page.getByText(/Heston world \(Andersen QE paths\)/).waitFor({ timeout: 120000 });
await shot(page.locator(".small-multiples").first(), "hedging-heston-histograms");
await page.getByLabel(/^Model/).selectOption("black_scholes");

// Monte Carlo convergence with confidence band
await page.getByRole("tab", { name: "Convergence" }).click();
await page.getByRole("button", { name: "Run Monte Carlo study" }).click();
await page.getByText(/Estimate with 95% confidence band/).waitFor();
await shot(figure("Estimate with 95% confidence band"), "mc-convergence");

// SSVI surface heat map
await page.getByRole("tab", { name: "Market data" }).click();
await page.getByRole("button", { name: "Load bundled synthetic snapshots" }).click();
await page.getByRole("heading", { name: /Quality report/ }).waitFor();
await page.getByRole("tab", { name: "Surface" }).click();
const pick = async (label, re) => {
  const sel = page.getByLabel(label);
  await sel.locator("option", { hasText: re }).first().waitFor({ state: "attached" });
  const texts = await sel.locator("option").allTextContents();
  await sel.selectOption({ index: texts.findIndex((t) => re.test(t)) });
};
await pick("Snapshot to fit", /09-28T20:00 · synthetic SSVI/);
await page.getByRole("button", { name: "Fit surface" }).click();
await page.getByRole("img", { name: "Implied volatility heat map" }).waitFor();
await shot(page.locator(".viz").first(), "surface");

// Heston calibration of the Heston-generated pair
await pick("Snapshot to fit", /09-28T20:00 · synthetic Heston/);
await pick("Later snapshot (optional)", /09-29T20:00 · synthetic Heston/);
await page.getByRole("button", { name: "Calibrate Heston" }).click();
await page.getByText(/Later, only v₀ refitted/).waitFor({ timeout: 60000 });
await shot(page.locator("section.viz").filter({ has: page.getByRole("heading", { name: /Heston calibration/ }) }), "heston-calibration");

await browser.close();
