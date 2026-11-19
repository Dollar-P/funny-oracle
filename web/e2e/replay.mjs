// R6 check: /p/:id renders the stored verdict + forecast; "Replay the show" streams it live again.
// Usage: node e2e/replay.mjs [baseUrl] [outDir] [id]
import { chromium } from "playwright-core";

const [base = "http://localhost:5173", outDir = ".", id = "10"] = process.argv.slice(2);
const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
const log = (...a) => console.log("•", ...a);

await page.goto(`${base}/p/${id}`, { waitUntil: "networkidle" });
await page.getByTestId("verdict").waitFor({ timeout: 10000 });
await page.getByTestId("forecast").waitFor();
await page.waitForTimeout(1500);
const headline = await page.getByTestId("verdict").locator("h3").innerText();
const canvases = await page.getByTestId("forecast").locator("canvas").count();
const prize = await page.getByTestId("forecast").innerText();
log(`/p/${id}: headline "${headline}"`);
log(`chart canvases: ${canvases} · prize cards: ${["Bear", "Base", "Bull", "P(up)"].every((k) => prize.includes(k))}`);
log(`podiums done: ${await page.getByRole("button", { name: /open details/ }).count()} · debate turns: ${await page.locator("[aria-label='Bull vs Bear debate'] li").count()}`);
await page.screenshot({ path: `${outDir}/replay_${id}_static.png`, fullPage: true });

await page.getByRole("button", { name: /Replay the show/ }).click();
await page.getByText(/mock replay of #/).waitFor({ timeout: 10000 });
log("replay started (mock badge visible)");
await page.getByTestId("verdict").waitFor({ timeout: 180000 });
await page.getByTestId("forecast").waitFor({ timeout: 10000 });
await page.waitForTimeout(2500);
log(`replay ended in verdict: "${await page.getByTestId("verdict").locator("h3").innerText()}"`);
await page.screenshot({ path: `${outDir}/replay_${id}_live_end.png`, fullPage: true });
console.log(JSON.stringify({ ok: true, errors }));
await browser.close();
