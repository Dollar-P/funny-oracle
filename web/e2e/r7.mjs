// R7 check: mock duel → head-to-head; Hall of Fame shows DB metrics; share card PNG downloads.
// Usage: node e2e/r7.mjs [baseUrl] [outDir] [replayId]
import { chromium } from "playwright-core";
import fs from "node:fs";

const [base = "http://localhost:5173", outDir = ".", rid = "10"] = process.argv.slice(2);
const browser = await chromium.launch({ channel: "chrome", headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
const log = (...a) => console.log("•", ...a);

// --- Hall of Fame (real DB) ---
await page.goto(`${base}/hall-of-fame`, { waitUntil: "networkidle" });
await page.getByText("AI vs 🪙 Coin Flip").waitFor({ timeout: 60000 });
await page.waitForTimeout(2000);
const coin = (await page.locator("[aria-labelledby=coin-title]").innerText()).replace(/\s+/g, " ");
log(`coin flip: ${coin.slice(0, 160)}`);
log(`model rows: ${await page.locator("[aria-labelledby=models-title] tbody tr").count()} · history rows: ${await page.locator("[aria-labelledby=hist-title] tbody tr").count()} · pending rows: ${await page.locator("[aria-labelledby=pending-title] li").count()}`);
log(`auto-scalp: ${(await page.locator("[aria-labelledby=as-title]").innerText()).split("\n").slice(0, 3).join(" | ")}`);
await page.selectOption("#f-mode", "scalp");
await page.waitForTimeout(600);
log(`history filtered to scalp: ${await page.locator("[aria-labelledby=hist-title] tbody tr").count()} rows`);
await page.selectOption("#f-mode", "");
await page.screenshot({ path: `${outDir}/hall_of_fame.png`, fullPage: true });

// --- Share card ---
await page.goto(`${base}/p/${rid}`, { waitUntil: "networkidle" });
await page.getByRole("button", { name: /Share card/ }).click();
await page.getByRole("dialog", { name: "Share card" }).locator("img").waitFor({ timeout: 20000 });
await page.screenshot({ path: `${outDir}/share_preview.png` });
const [dl] = await Promise.all([page.waitForEvent("download"), page.getByTestId("share-download").click()]);
const png = `${outDir}/share_card_${rid}.png`;
await dl.saveAs(png);
const buf = fs.readFileSync(png);
const w = buf.readUInt32BE(16);
const h = buf.readUInt32BE(20);
log(`share card: ${dl.suggestedFilename()} ${w}×${h} ${(buf.length / 1024).toFixed(0)} KB`);
await page.keyboard.press("Escape");

// --- Duel (mock) ---
await page.goto(`${base}/duel?ticker=GC%3DF&mode=swing&horizon=1M`, { waitUntil: "networkidle" });
await page.evaluate(() => sessionStorage.removeItem("oracle.duel"));
const groups = page.getByRole("radiogroup");
log(`duel pickers: A=${await groups.nth(2).getByRole("radio").count()} B=${await groups.nth(3).getByRole("radio").count()}`);
await page.getByRole("button", { name: /START THE DUEL/ }).click();
await page.getByRole("region", { name: "Model A" }).waitFor();
await page.waitForTimeout(6000);
await page.screenshot({ path: `${outDir}/duel_live.png`, fullPage: true });
await page.getByTestId("head-to-head").waitFor({ timeout: 180000 });
await page.waitForTimeout(1500);
const h2h = (await page.getByTestId("head-to-head").innerText()).split("\n");
log(`head-to-head banner: ${h2h[0]} · rows: ${h2h.slice(1, 4).join(" / ")}`);
await page.screenshot({ path: `${outDir}/duel_done.png`, fullPage: true });
console.log(JSON.stringify({ ok: true, errors }));
await browser.close();
