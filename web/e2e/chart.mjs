// Forecast-chart consistency check: /p/<id> (anchored), its "Replay the show", and an Arena mock show.
// Usage: node e2e/chart.mjs [baseUrl] [outDir] [id] [prefix] [ticker] [mode] [horizon]
import { chromium } from "playwright-core";

const [base = "http://localhost:5173", outDir = ".", id = "4", prefix = "after", ticker = "BTC-USD", mode = "scalp", horizon = "15m"] =
  process.argv.slice(2);
const browser = await chromium.launch({ channel: "chrome", headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, timezoneId: "Asia/Bangkok" });
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
const log = (...a) => console.log("•", ...a);

async function chartInfo(label) {
  const f = page.getByTestId("forecast").first();
  await f.waitFor({ timeout: 240000 });
  await page.waitForTimeout(2500); // fan animation
  await f.scrollIntoViewIfNeeded();
  const axis = await f.evaluate((el) => {
    // lightweight-charts renders axis labels on canvas; read the time-axis via its hidden table cells is not possible,
    // so we report the data-* attributes the component exposes for testing.
    const fig = el.querySelector("figure");
    return fig ? { ...fig.dataset } : {};
  });
  const prize = (await f.innerText()).match(/P\(up\)\s*vs now ([\d,.]+)/)?.[1];
  log(`${label}: chart ${JSON.stringify(axis)} · prize "now" ${prize}`);
  const result = await page.locator("[role=status]").filter({ hasText: /Direction|Mock show/ }).allInnerTexts();
  if (result.length) log(`${label}: result card(s): ${result.map((r) => r.replace(/\s+/g, " ").slice(0, 160)).join(" || ")}`);
  await f.screenshot({ path: `${outDir}/chart_${prefix}_${label}.png` });
}

// 1) static replay page
await page.goto(`${base}/p/${id}`, { waitUntil: "networkidle" });
await chartInfo(`p${id}`);
const hdr = (await page.locator("text=/Prediction #/").first().innerText()).replace(/\s+/g, " ");
log(`p${id} header: ${hdr}`);

// 2) "Replay the show"
await page.getByRole("button", { name: /Replay the show/ }).click();
await page.getByText(/mock replay of #/).waitFor({ timeout: 10000 });
await chartInfo(`p${id}_replayshow`);

// 3) Arena mock show
await page.goto(base + "/", { waitUntil: "networkidle" });
await page.evaluate(([t, m, h]) => sessionStorage.setItem("oracle.arena", JSON.stringify({ ticker: t, mode: m, horizon: h, runId: null })), [ticker, mode, horizon]);
await page.reload({ waitUntil: "networkidle" });
await page.getByRole("button", { name: /START THE SHOW/ }).click();
await chartInfo(`arena_${ticker.replace(/[^A-Z]/g, "")}_${mode}`);
const orig = page.getByRole("button", { name: /original chart/ });
if (await orig.count()) {
  await orig.click();
  const oc = page.getByTestId("original-chart");
  await oc.waitFor();
  await page.waitForTimeout(1500);
  log(`original card: ${JSON.stringify(await oc.locator("figure").evaluate((e) => ({ ...e.dataset })))}`);
  await page.locator('[aria-label^="Original prediction"]').screenshot({ path: `${outDir}/chart_${prefix}_arena_original_card.png` });
}
const live = await page.evaluate(async (t) => (await (await fetch(`/api/history/${encodeURIComponent(t)}?interval=1m&limit=1`)).json()).candles.at(-1)?.close, ticker);
log(`live last 1m close ${live}`);
const tz = await page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone);
log(`browser tz ${tz}`);
console.log(JSON.stringify({ ok: true, errors }));
await browser.close();
