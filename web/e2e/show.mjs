// R5/R6 check: a mock show plays end-to-end with every event visible.
// Usage: node e2e/show.mjs [baseUrl] [outDir] [ticker] [mode] [horizon] [--reduced]
import { chromium } from "playwright-core";

const [base = "http://localhost:5173", outDir = ".", ticker = "NVDA", mode = "swing", horizon = "1M"] = process.argv.slice(2);
const reduced = process.argv.includes("--reduced");
const tag = `${ticker.replace(/[^A-Z]/gi, "")}_${mode}${reduced ? "_reduced" : ""}`;
const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 }, reducedMotion: reduced ? "reduce" : "no-preference" });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
const log = (...a) => console.log("•", ...a);

// count SSE events in the page
await page.addInitScript(() => {
  window.__events = [];
  const ES = window.EventSource;
  window.EventSource = class extends ES {
    constructor(...a) {
      super(...a);
      const types = ["run_started", "stage", "agent_started", "tool_call", "tool_result", "search", "news_brief", "agent_done",
        "agent_failed", "aggregate", "debate_turn", "judge", "quant", "cost_update", "run_done", "error"];
      types.forEach((t) => this.addEventListener(t, (e) => window.__events.push(JSON.parse(e.data))));
    }
  };
});

await page.goto(base + "/", { waitUntil: "networkidle" });
await page.evaluate(([t, m, h]) => sessionStorage.setItem("oracle.arena", JSON.stringify({ ticker: t, mode: m, horizon: h, runId: null })), [ticker, mode, horizon]);
await page.reload({ waitUntil: "networkidle" });
await page.getByRole("button", { name: /START THE SHOW/ }).click();

const seen = { thinking: false, phone: false, bubbleOk: false, bubbleSearch: false, scoreChip: false, provisional: false };
const t0 = Date.now();
let shotMid = false;
let shotDebate = false;
while (Date.now() - t0 < 150_000) {
  const st = await page.evaluate(() => {
    const txt = document.body.innerText;
    return {
      thinking: txt.includes("thinking…"),
      phone: txt.includes("on the phone…"),
      bubbleOk: [...document.querySelectorAll("[title]")].some((e) => /get_\w+\(/.test(e.getAttribute("title") ?? "") && e.textContent.includes("✓")),
      bubbleSearch: [...document.querySelectorAll("[title]")].some((e) => (e.textContent ?? "").includes("🔎")),
      scoreChip: /conf 0\.\d\d/.test(txt),
      provisional: txt.includes("live S_raw"),
      debate: document.querySelectorAll("[aria-label='Bull vs Bear debate'] li").length,
      verdict: !!document.querySelector("[data-testid=verdict]"),
      done: (window.__events ?? []).some((e) => e.type === "run_done"),
      revealed: !!document.querySelector("[data-testid=forecast]"),
    };
  });
  for (const k of Object.keys(seen)) seen[k] ||= st[k];
  if (!shotMid && st.phone && st.scoreChip) {
    await page.screenshot({ path: `${outDir}/show_${tag}_1_analysts.png` });
    shotMid = true;
  }
  if (!shotDebate && st.debate >= 2) {
    await page.screenshot({ path: `${outDir}/show_${tag}_2_debate.png`, fullPage: true });
    shotDebate = true;
  }
  if (st.done && (st.revealed || st.verdict)) break;
  await page.waitForTimeout(250);
}
await page.waitForTimeout(reduced ? 500 : 3500); // let the reveal + fan chart finish
const events = await page.evaluate(() => window.__events);
const types = [...new Set(events.map((e) => e.type))];
const tape = await page.locator("[aria-label^='Ticker tape'] div.num > div").count();
log(`events: ${events.length} (${types.join(", ")}) in ${((Date.now() - t0) / 1000).toFixed(0)}s`);
log(`tape lines: ${tape}`);
log(`seen: ${JSON.stringify(seen)}`);
const rail = await page.locator("[aria-label='Show progress'] li").allInnerTexts();
log(`rail: ${rail.map((r) => r.replace(/\s+/g, " ").trim()).join(" | ")}`);
log(`debate turns rendered: ${await page.locator("[aria-label='Bull vs Bear debate'] li").count()}`);
await page.screenshot({ path: `${outDir}/show_${tag}_3_end.png`, fullPage: true });

// drawer on the first finished podium
const pod = page.getByRole("button", { name: /open details/ }).first();
if (await pod.count()) {
  await pod.click();
  const dlg = page.getByRole("dialog");
  await dlg.waitFor();
  const t = await dlg.innerText();
  log(`drawer: ${t.split("\n")[0]} · has trace: ${/tool trace \(\d+\)/i.test(t)} · key points: ${/key points/i.test(t)}`);
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${outDir}/show_${tag}_4_drawer.png` });
  await page.keyboard.press("Escape");
}
console.log(JSON.stringify({ ok: events.some((e) => e.type === "run_done"), errors }));
await browser.close();
