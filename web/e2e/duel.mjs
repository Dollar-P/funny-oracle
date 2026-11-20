// Duel check: pick models A/B, start, wait for the head-to-head.
// Usage: node e2e/duel.mjs [baseUrl] [outDir] [ticker] [mode] [horizon] [modelA] [modelB]
import { chromium } from "playwright-core";

const [base = "http://localhost:5173", outDir = ".", ticker = "NVDA", mode = "swing", horizon = "1M",
  ma = "gemini-3.5-flash-lite", mb = "gemini-3.8-flash"] = process.argv.slice(2);
const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
const log = (...a) => console.log("•", ...a);

const models = await (await fetch(`${base}/api/models`)).json();
const label = (id) => models.models.find((m) => m.id === id).label;
const esc = (t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); // labels like "bonsai-27b (local)" contain ( )
await page.goto(`${base}/duel?ticker=${encodeURIComponent(ticker)}&mode=${mode}&horizon=${horizon}`, { waitUntil: "networkidle" });
await page.evaluate(() => sessionStorage.removeItem("oracle.duel"));
await page.getByRole("radiogroup", { name: "Model A" }).getByRole("radio", { name: new RegExp(esc(label(ma))) }).click();
await page.getByRole("radiogroup", { name: "Model B" }).getByRole("radio", { name: new RegExp(esc(label(mb))) }).click();
const start = page.getByRole("button", { name: /START THE DUEL/ });
log(`start: ${(await start.innerText()).replace(/\s+/g, " ")}`);
const t0 = Date.now();
await start.click();
await page.getByRole("region", { name: "Model A" }).waitFor();
await page.waitForTimeout(15000);
await page.screenshot({ path: `${outDir}/duel_live.png`, fullPage: true });
await page.getByTestId("head-to-head").waitFor({ timeout: Number(process.env.DUEL_TIMEOUT_MS ?? 300000) });
await page.waitForTimeout(1500);
log(`done in ${((Date.now() - t0) / 1000).toFixed(0)}s`);
log((await page.getByTestId("head-to-head").innerText()).split("\n").slice(0, 12).join(" | "));
await page.screenshot({ path: `${outDir}/duel_done.png`, fullPage: true });
console.log(JSON.stringify({ ok: true, errors }));
await browser.close();
