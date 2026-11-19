// R4 check: select everything in the Arena setup and press Start (mock) → a run begins.
// Usage: node e2e/setup.mjs [baseUrl] [outDir]
import { chromium } from "playwright-core";

const base = process.argv[2] ?? "http://localhost:5173";
const outDir = process.argv[3] ?? ".";
const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
const step = (s) => console.log("•", s);

await page.goto(base + "/", { waitUntil: "networkidle" });
await page.evaluate(() => sessionStorage.clear());
await page.reload({ waitUntil: "networkidle" });

step("ticker search: type 'eth', arrow down + Enter");
const box = page.getByRole("combobox");
await box.fill("eth");
await page.getByRole("option").first().waitFor();
const firstOpt = await page.getByRole("option").first().innerText();
await box.press("Enter");
const onStage = await page.locator("text=On stage:").innerText();
step(`  first option: ${firstOpt.replace(/\s+/g, " ")} → ${onStage}`);

step("preset chip GOLD, then BTC");
await page.getByRole("button", { name: /^GOLD/ }).click();
await page.getByRole("button", { name: /^BTC/ }).click();
await page.waitForTimeout(800); // sparklines
step("mode scalp, horizon 1h");
await page.getByRole("radio", { name: "⚡ Scalp" }).click();
await page.getByRole("radio", { name: "1h" }).click();
step("model: pick the second card, open advanced judge");
const cards = page.getByRole("radiogroup", { name: /Oracle brain/ }).getByRole("radio");
const n = await cards.count();
await cards.nth(Math.min(1, n - 1)).click();
await page.getByRole("button", { name: /Advanced/ }).click();
await page.screenshot({ path: `${outDir}/r4_setup.png`, fullPage: true });

const startBtn = page.getByRole("button", { name: /START THE SHOW/ });
step(`start button: "${(await startBtn.innerText()).replace(/\s+/g, " ")}" enabled=${await startBtn.isEnabled()}`);
await startBtn.click();
await page.getByRole("button", { name: /Leave show/ }).waitFor({ timeout: 10000 });
await page.waitForFunction(() => document.querySelector("[aria-label='Show progress']"), null, { timeout: 15000 });
const heading = (await page.locator("h2.font-display").first().innerText()).replace(/\s+/g, " ");
step(`run began: ${heading}`);
await page.screenshot({ path: `${outDir}/r4_started.png`, fullPage: true });
console.log(JSON.stringify({ ok: true, errors }));
await browser.close();
