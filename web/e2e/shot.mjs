// Usage: node e2e/shot.mjs <url> <out.png> [width] [height] [waitMs] [--reduced]
import { chromium } from "playwright-core";
const [url, out, w = "1440", h = "900", wait = "1500"] = process.argv.slice(2);
const reduced = process.argv.includes("--reduced");
const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: +w, height: +h }, reducedMotion: reduced ? "reduce" : "no-preference" });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
await page.goto(url, { waitUntil: "networkidle" });
await page.waitForTimeout(+wait);
await page.screenshot({ path: out, fullPage: true });
console.log(JSON.stringify({ out, errors }));
await browser.close();
