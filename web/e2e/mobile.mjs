// Phone-width check: no horizontal page scroll on key screens. Usage: node e2e/mobile.mjs [baseUrl] [outDir] [id]
import { chromium } from "playwright-core";
const [base = "http://localhost:5173", outDir = ".", id = "10"] = process.argv.slice(2);
const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
for (const [name, path] of [["arena", "/"], ["replay", `/p/${id}`], ["duel", "/duel"], ["hall", "/hall-of-fame"]]) {
  await page.goto(base + path, { waitUntil: "networkidle" });
  await page.waitForTimeout(2500);
  const r = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
    wide: [...document.querySelectorAll("body *")].filter((e) => e.getBoundingClientRect().right > document.documentElement.clientWidth + 1 && !e.closest("[class*=overflow]") && getComputedStyle(e).position !== "fixed").slice(0, 3).map((e) => e.tagName + "." + String(e.className).slice(0, 40)) }));
  console.log(`• ${name}: scrollWidth ${r.sw} / ${r.cw}${r.sw > r.cw ? " ✗ overflow " + JSON.stringify(r.wide) : " ✓"}`);
  await page.screenshot({ path: `${outDir}/mobile_${name}.png`, fullPage: false });
}
console.log(JSON.stringify({ errors }));
await browser.close();
