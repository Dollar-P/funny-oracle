// Desk e2e on the demo dataset (scripts/pro_demo.py). Serve a COPY of the demo DB — this script approves/rejects.
//   node e2e/pro.mjs http://127.0.0.1:8011 ../docs/pro
// Checks: every page renders at 1440 px and 390 px, approve/reject work, the Lab shows CIs and
// insufficient-data badges, no horizontal scroll at 390 px, 0 console errors. Screenshots → <outDir>.
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";

const [base = "http://127.0.0.1:8011", out = "../docs/pro"] = process.argv.slice(2);
mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ channel: "chrome", headless: true });
const errors = [];
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok: !!ok, detail });
  if (!ok) console.error(`✗ ${name} ${detail}`);
};

async function open(width, height = 900) {
  const ctx = await browser.newContext({ viewport: { width, height }, reducedMotion: "reduce", timezoneId: "Asia/Bangkok", locale: "en-GB" });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => errors.push(`[${width}] pageerror ${e}`));
  page.on("console", (m) => m.type() === "error" && errors.push(`[${width}] console ${m.text()}`));
  return { ctx, page };
}

async function noHScroll(page) {
  return page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
}

async function api(path, init) {
  const r = await fetch(`${base}/api/pro${path}`, init);
  return r.json();
}

const newSignals = (await api("/signals?status=new")).signals;
check("demo has signals awaiting approval", newSignals.length >= 2, `${newSignals.length}`);
const anySignal = (await api("/signals?status=closed")).signals[0] ?? (await api("/signals")).signals[0];

const PAGES = [
  ["overview", "/pro", "Equity vs buy"],
  ["signals", "/pro/signals", "Signal blotter"],
  ["signal-detail", `/pro/signals/${anySignal.id}`, "Risk calculation"],
  ["positions", "/pro/positions", "Open positions"],
  ["journal", "/pro/journal", "Journal"],
  ["lab", "/pro/lab", "Strategy comparison"],
  ["risk", "/pro/risk", "Limit usage"],
  ["settings", "/pro/settings", "Risk & cost parameters"],
];

for (const width of [1440, 390]) {
  const { ctx, page } = await open(width);
  for (const [name, path, text] of PAGES) {
    await page.goto(base + path, { waitUntil: "networkidle" });
    try {
      await page.getByText(text, { exact: false }).first().waitFor({ timeout: 10_000 });
      check(`${name}@${width} renders`, true);
    } catch {
      check(`${name}@${width} renders`, false, `missing "${text}"`);
    }
    await page.waitForTimeout(name === "lab" || name === "overview" || name === "signal-detail" ? 900 : 300);
    if (width === 390) {
      const ok = await noHScroll(page);
      if (!ok) {
        const wide = await page.evaluate(() => [...document.querySelectorAll("main *")].filter((e) => e.getBoundingClientRect().right > window.innerWidth + 1 && !e.closest(".overflow-x-auto, .overflow-auto")).slice(0, 3).map((e) => `${e.tagName}.${e.className}`.slice(0, 80)));
        check(`${name}@390 no horizontal scroll`, false, JSON.stringify(wide));
      } else check(`${name}@390 no horizontal scroll`, true);
    }
    await page.screenshot({ path: `${out}/${name}_${width}.png`, fullPage: name !== "signals" });
  }
  await ctx.close();
}

// Lab: CIs and insufficient-data badges
{
  const { ctx, page } = await open(1440);
  await page.goto(`${base}/pro/lab`, { waitUntil: "networkidle" });
  const body = await page.locator("main").innerText();
  check("lab shows insufficient-data badges", /insufficient data/.test(body));
  check("lab shows confidence intervals", /\[[−-]?\d+\.\d+, [−-]?\d+\.\d+\]/.test(body));
  check("lab shows n", /n=\d+/.test(body));
  check("lab shows contamination labels", /contaminat/i.test(body));
  await ctx.close();
}

// Approve / reject in the blotter
{
  const { ctx, page } = await open(1440);
  await page.goto(`${base}/pro/signals?status=new`, { waitUntil: "networkidle" });
  const [a, b] = newSignals;
  await page.locator(`tr[data-signal="${a.id}"]`).getByRole("button", { name: "Approve" }).click();
  await page.waitForTimeout(800);
  const sa = (await api(`/signals/${a.id}`)).signal.status;
  check("approve works", sa === "approved", sa);
  await page.goto(`${base}/pro/signals?status=new`, { waitUntil: "networkidle" });
  await page.locator(`tr[data-signal="${b.id}"]`).getByRole("button", { name: "Reject" }).click();
  await page.waitForTimeout(800);
  const sb = (await api(`/signals/${b.id}`)).signal;
  check("reject works", sb.status === "rejected", sb.status);
  await page.goto(`${base}/pro/signals`, { waitUntil: "networkidle" });
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${out}/signals_after_approve_1440.png`, fullPage: false });
  await page.goto(`${base}/pro/positions`, { waitUntil: "networkidle" });
  await page.waitForTimeout(500);
  check("approved signal shows as a pending entry", /entry pending/.test(await page.locator("main").innerText()));
  await page.screenshot({ path: `${out}/positions_after_approve_1440.png`, fullPage: true });
  // mode switch round trip
  await page.getByRole("link", { name: "Arena" }).first().click();
  await page.waitForTimeout(600);
  check("mode switch → Arena", (await page.url()).replace(base, "") === "/");
  await page.getByRole("link", { name: "Desk" }).first().click();
  await page.waitForTimeout(600);
  check("mode switch → Desk", (await page.url()).replace(base, "").startsWith("/pro"));
  await ctx.close();
}

check("0 console errors", errors.length === 0, errors.slice(0, 5).join(" | "));
await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(JSON.stringify({ passed: results.length - failed.length, failed: failed.length, failures: failed, errors }, null, 1));
process.exit(failed.length ? 1 : 0);
