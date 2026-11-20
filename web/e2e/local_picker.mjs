// Local models in the pickers: free/local badge, Quick/Deep toggle, Scalp refusal, Duel.
// Usage: node e2e/local_picker.mjs [baseUrl] [outDir]
import { chromium } from "playwright-core";

const [base = "http://localhost:5173", outDir = "."] = process.argv.slice(2);
const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
const log = (...a) => console.log("•", ...a);

await page.goto(base + "/", { waitUntil: "networkidle" });
await page.evaluate(() => sessionStorage.clear());
await page.reload({ waitUntil: "networkidle" });
const group = page.getByRole("radiogroup", { name: /Oracle brain/ });
const local = group.getByRole("radio", { name: /bonsai-27b \(local\)/ });
await local.waitFor({ timeout: 15000 });
log(`cards: ${(await group.getByRole("radio").allInnerTexts()).map((t) => t.split("\n")[0]).join(" | ")}`);
await local.click();
const toggle = page.getByTestId("think-preset");
await toggle.waitFor();
log(`selected local card: "${(await local.innerText()).replace(/\s+/g, " ")}" · preset toggle visible`);
await page.getByRole("radio", { name: "🧠 Deep" }).click();
log(`deep hint: ${(await toggle.innerText()).split("\n").pop()}`);
await page.getByRole("radio", { name: "⚡ Quick" }).click();
const start = page.getByRole("button", { name: /START THE SHOW/ });
log(`swing start enabled: ${await start.isEnabled()}`);
await page.screenshot({ path: `${outDir}/local_arena_swing.png`, fullPage: true });

await page.getByRole("radio", { name: "⚡ Scalp" }).click();
await page.waitForTimeout(300);
log(`scalp: start enabled=${await start.isEnabled()} · reason="${await page.locator("#start-reason").innerText()}"`);
log(`scalp: local card aria-disabled=${await local.getAttribute("aria-disabled")}`);
await page.screenshot({ path: `${outDir}/local_arena_scalp.png`, fullPage: true });

// a mock show with the local model (replays a stored show; shows the label in the header)
await page.getByRole("radio", { name: "🐢 Swing" }).click();
await start.click();
await page.getByText(/bonsai-27b \(local\)/).first().waitFor({ timeout: 15000 });
log("mock show header shows the local model label");

await page.goto(base + "/duel?ticker=NVDA&mode=swing&horizon=1M", { waitUntil: "networkidle" });
await page.evaluate(() => sessionStorage.removeItem("oracle.duel"));
await page.getByRole("radiogroup", { name: "Model A" }).getByRole("radio", { name: /bonsai-27b \(local\)/ }).click();
await page.getByRole("radiogroup", { name: "Model B" }).getByRole("radio", { name: /3\.5 Flash-Lite/ }).click();
const dstart = page.getByRole("button", { name: /START THE DUEL/ });
log(`duel: toggle=${await page.getByTestId("think-preset").count()} · start="${(await dstart.innerText()).replace(/\s+/g, " ")}" enabled=${await dstart.isEnabled()}`);
await page.screenshot({ path: `${outDir}/local_duel_setup.png`, fullPage: true });
console.log(JSON.stringify({ ok: true, errors }));
await browser.close();
