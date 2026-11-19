"""
eval_local_llm.py — ข้อสอบวัด "ความฉลาด" ของโมเดลสำหรับงาน Funny Oracle (ตรวจคะแนนอัตโนมัติ)

ใช้ได้กับ endpoint แบบ OpenAI-compatible ทุกตัว (LM Studio ฯลฯ) ใช้แค่ httpx

    .venv\\Scripts\\python scripts\\eval_local_llm.py --model prism-ml/bonsai-27b
    .venv\\Scripts\\python scripts\\eval_local_llm.py --model prism-ml/bonsai-27b --modes off      # เฉพาะไม่คิด
    .venv\\Scripts\\python scripts\\eval_local_llm.py --only long_context,hallucination_pe

หมวดข้อสอบ:
  calc        คำนวณจากตัวเลข indicator / fundamentals / สูตรในโปรเจกต์
  compare     เทียบตัวเลข (จุดที่โหมดไม่คิดเคยพลาด)
  halluc      ข้อมูลไม่มีให้ ต้องตอบ null ไม่แต่งเอง
  reasoning   ตรรกะ: ความสอดคล้องของ report, วันเวลา, จัดประเภทข่าว
  long_ctx    หาข้อมูลสำคัญที่ฝังอยู่ใน prompt ยาว (มีตัวหลอก)
  instruct    ทำตามกฎรูปแบบหลายข้อพร้อมกัน

ผลลัพธ์บันทึกเป็น eval_<model>_<เวลา>.json ไว้เทียบกับโมเดลอื่น (เช่น Gemini) ภายหลัง
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import httpx

# Local OpenAI-compatible server (LM Studio etc.): LOCAL_BASE_URL from the environment or .env,
# or --base-url on the command line. Never hard-code a LAN address here (it ends up on GitHub).
try:  # optional: read .env like the rest of the project (the script itself only needs httpx)
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass
DEFAULT_BASE = os.getenv("LOCAL_BASE_URL", "http://localhost:1234/v1")
TIMEOUT = httpx.Timeout(600.0, connect=10.0)
THINK_RE = re.compile(r"<think>.*?</think>", re.S)

SYSTEM = (
    "You are a careful quantitative market analyst. Use ONLY the data given in the prompt. "
    "If the data does not contain what is asked, answer null — never guess or invent numbers. "
    "Reply with JSON only."
)

# ------------------------------------------------------------------ data
IND = {
    "ticker": "BTC-USD", "interval": "1h", "as_of": "2026-11-19T16:00:00Z",
    "last": 91250.0, "rsi14": 71.8, "macd": {"macd": 412.5, "signal": 380.1, "hist": 32.4},
    "sma20": 89800.0, "sma50": 87400.0, "sma200": 84100.0,
    "bollinger": {"upper": 91600.0, "mid": 89800.0, "lower": 88000.0},
    "atr14": 820.0, "volume_z": 1.9, "support": [89500, 88000], "resistance": [91600, 93000],
}
IND_JSON = json.dumps(IND)

FUND = {"ticker": "ACME", "price": 120.0, "eps_ttm": 4.8, "eps_growth_pct": 20.0,
        "revenue_growth_pct": 14.0, "debt_to_equity": 0.6}

REPORTS = [  # (agent, weight, score, confidence) — swing · stock
    ("business", 0.25, 0.4, 0.8), ("technical", 0.15, -0.2, 0.6), ("valuation", 0.25, 0.1, 0.5),
    ("macro", 0.15, -0.3, 0.7), ("psychology", 0.20, 0.6, 0.9),
]
S_RAW = sum(w * s * c for _, w, s, c in REPORTS) / sum(w * c for _, w, _, c in REPORTS)


def long_context_prompt(n_items: int) -> str:
    rnd = random.Random(42)
    cos = ["Apple", "Microsoft", "Tesla", "Amazon", "Meta", "AMD", "Intel", "Netflix", "Oracle",
           "Shopify", "Coinbase", "Palantir", "Broadcom", "Salesforce", "Uber"]
    events = ["a product launch", "an analyst upgrade", "an analyst downgrade", "earnings",
              "a supply-chain update", "a lawsuit ruling", "a CEO interview", "index rebalancing",
              "a partnership announcement", "weak guidance", "a buyback announcement"]
    items = []
    for i in range(n_items):
        d = f"2026-11-{rnd.randint(10, 19):02d}"
        items.append(f"[{d}] {rnd.choice(cos)} shares {rnd.choice(['rose', 'fell'])} "
                     f"{rnd.uniform(0.2, 6):.1f}% after {rnd.choice(events)}.")
    distractors = [
        "[2026-11-12] Analysts had widely expected the Federal Reserve to cut rates by 25 basis points.",
        "[2026-11-15] One Fed official said a 75 basis point move 'should not be ruled out'.",
        "[2026-11-17] Futures markets priced a 60% chance of a 25bp cut at the next meeting.",
    ]
    needle = ("[2026-11-18] The Federal Reserve announced an unscheduled 50 basis point rate cut, "
              "citing tightening credit conditions.")
    for j, dline in enumerate(distractors):
        items.insert(int(len(items) * (0.15 + 0.25 * j)), dline)
    items.insert(int(len(items) * 0.62), needle)
    return ("News brief (most recent last is NOT guaranteed; read everything):\n" + "\n".join(items)
            + "\n\nQuestion: What rate change did the Federal Reserve actually ANNOUNCE, in basis "
              "points? Use a negative number for a cut and a positive number for a hike. "
              "Expectations and opinions are not announcements.")


# ------------------------------------------------------------------ helpers
def schema_answer(t: dict) -> dict:
    return {"type": "object", "properties": {"answer": t},
            "required": ["answer"], "additionalProperties": False}


NUM = {"type": "number"}
NUM_OR_NULL = {"type": ["number", "null"]}
BOOL = {"type": "boolean"}
STR = {"type": "string"}


def g_num(expected: float, tol: float) -> Callable[[Any], tuple[bool, str]]:
    def grade(a):
        try:
            v = float(a)
        except (TypeError, ValueError):
            return False, f"ไม่ใช่ตัวเลข: {a!r}"
        return abs(v - expected) <= tol, f"ได้ {v:g} · เฉลย {expected:g} ±{tol:g}"
    return grade


def g_eq(expected: Any) -> Callable[[Any], tuple[bool, str]]:
    def grade(a):
        norm = (lambda x: x.strip().lower()) if isinstance(expected, str) else (lambda x: x)
        try:
            ok = norm(a) == norm(expected)
        except AttributeError:
            ok = False
        return ok, f"ได้ {a!r} · เฉลย {expected!r}"
    return grade


def g_null(a) -> tuple[bool, str]:
    return a is None, f"ได้ {a!r} · เฉลย null (ข้อมูลไม่มีให้)"


@dataclass
class Q:
    id: str
    cat: str
    prompt: str
    schema: dict
    grade: Callable[[Any], tuple[bool, str]]
    whole_obj: bool = False  # True = grader รับ object ทั้งก้อน ไม่ใช่แค่ "answer"
    note: str = ""


def g_instruct(obj) -> tuple[bool, str]:
    issues = []
    kp = obj.get("key_points", [])
    if len(kp) != 4:
        issues.append(f"key_points={len(kp)} ข้อ (ต้อง 4)")
    if not all(isinstance(k, str) and k[:1] in ("▲", "▼") for k in kp):
        issues.append("บางข้อไม่ขึ้นต้นด้วย ▲/▼")
    words = len(str(obj.get("reasoning", "")).split())
    if words > 40:
        issues.append(f"reasoning {words} คำ (เกิน 40)")
    s = obj.get("score")
    if not isinstance(s, (int, float)) or not -1 <= s <= 1:
        issues.append(f"score={s!r} นอกช่วง")
    if obj.get("agent") != "technical":
        issues.append(f"agent={obj.get('agent')!r}")
    return not issues, "; ".join(issues) or "ทำตามกฎครบ"


INSTRUCT_SCHEMA = {
    "type": "object",
    "properties": {
        "agent": {"type": "string"},
        "score": {"type": "number", "description": "decimal -1.0..1.0"},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "reasoning": {"type": "string"},
    },
    "required": ["agent", "score", "key_points", "reasoning"],
    "additionalProperties": False,
}


def build_questions(long_items: int) -> list[Q]:
    pe = FUND["price"] / FUND["eps_ttm"]
    return [
        # ---- compare
        Q("count_above_sma", "compare",
          f"Data: {IND_JSON}\nHow many of SMA20, SMA50 and SMA200 is the last price ABOVE? (integer)",
          schema_answer({"type": "integer"}), g_eq(3)),
        Q("nearest_resistance", "compare",
          f"Data: {IND_JSON}\nWhat is the nearest resistance level ABOVE the last price?",
          schema_answer(NUM), g_num(91600, 0.5)),
        Q("rsi_zone", "compare",
          f"Data: {IND_JSON}\nIs RSI14 in overbought territory (above 70)?",
          schema_answer(BOOL), g_eq(True)),
        # ---- calc
        Q("pct_from_sma200", "calc",
          f"Data: {IND_JSON}\nHow far is the last price above SMA200, in percent? "
          "(e.g. 5.0 means 5%). Round to 1 decimal.",
          schema_answer(NUM), g_num((IND["last"] / IND["sma200"] - 1) * 100, 0.15)),
        Q("atr_pct", "calc",
          f"Data: {IND_JSON}\nATR14 as a percentage of the last price? Round to 2 decimals.",
          schema_answer(NUM), g_num(IND["atr14"] / IND["last"] * 100, 0.05)),
        Q("bollinger_pctb", "calc",
          f"Data: {IND_JSON}\nCompute Bollinger %B = (last - lower) / (upper - lower). Round to 2 decimals.",
          schema_answer(NUM),
          g_num((IND["last"] - 88000) / (91600 - 88000), 0.02)),
        Q("pe_peg", "calc",
          f"Fundamentals: {json.dumps(FUND)}\nCompute PEG = (price / eps_ttm) / eps_growth_pct. "
          "Round to 2 decimals.",
          schema_answer(NUM), g_num(pe / FUND["eps_growth_pct"], 0.02)),
        Q("compound", "calc",
          "A stock at $100 rises 10% on Monday, then falls 10% on Tuesday. What is the price now?",
          schema_answer(NUM), g_num(99.0, 0.01)),
        Q("s_raw", "calc",
          "Agent reports (agent, weight, score, confidence): "
          + json.dumps(REPORTS)
          + "\nCompute S_raw = sum(weight*score*confidence) / sum(weight*confidence). Round to 3 decimals.",
          schema_answer(NUM), g_num(S_RAW, 0.005), note=f"เฉลย {S_RAW:.4f}"),
        # ---- hallucination
        Q("hallucination_pe", "halluc",
          f"Data: {IND_JSON}\nWhat is BTC's P/E ratio according to this data?",
          schema_answer(NUM_OR_NULL), g_null),
        Q("hallucination_earnings", "halluc",
          f"Fundamentals: {json.dumps(FUND)}\nWhat is ACME's next earnings date according to the data? "
          "Answer as YYYY-MM-DD or null.",
          schema_answer({"type": ["string", "null"]}), g_null),
        # ---- reasoning
        Q("report_consistency", "reasoning",
          "Analyst report: " + json.dumps({
              "agent": "technical", "score": 0.7, "confidence": 0.8,
              "key_points": ["Price broke below SMA200 on high volume",
                             "RSI fell to 28 with bearish divergence",
                             "MACD crossed below its signal line"]})
          + "\nIs the score consistent with the key points? (true/false)",
          schema_answer(BOOL), g_eq(False)),
        Q("horizon_date", "reasoning",
          "Now is Friday 2026-11-20 14:00 UTC. Mode Swing, horizon 1W (one calendar week). "
          "What is the target date? Answer YYYY-MM-DD.",
          schema_answer(STR), g_eq("2026-11-27")),
        Q("market_close", "reasoning",
          "US stock market regular session closes at 21:00 UTC. Now is 20:30 UTC. "
          "A scalp prediction for NVDA has a 1h horizon. Does the target time fall AFTER the close?",
          schema_answer(BOOL), g_eq(True)),
        Q("headline_sentiment", "reasoning",
          "Classify each headline's likely short-term impact on NVDA stock as bullish, bearish or neutral, "
          "in order:\n1. NVIDIA beats estimates and raises full-year guidance\n"
          "2. US expands AI chip export restrictions to China\n"
          "3. NVIDIA to present at an industry conference next week",
          schema_answer({"type": "array", "items": {"type": "string",
                         "enum": ["bullish", "bearish", "neutral"]}}),
          g_eq(["bullish", "bearish", "neutral"])),
        # ---- long context
        Q("long_context", "long_ctx", long_context_prompt(long_items),
          schema_answer(NUM), g_num(-50, 0.1), note=f"{long_items} ข่าว + ตัวหลอก 3 อัน"),
        # ---- instruction following
        Q("instruct_rules", "instruct",
          f"Data: {IND_JSON}\nWrite a technical AgentReport for BTC-USD 1h with these rules:\n"
          "- agent must be exactly \"technical\"\n- score is a decimal between -1 and 1\n"
          "- EXACTLY 4 key_points, each starting with ▲ (bullish) or ▼ (bearish)\n"
          "- reasoning at most 40 words",
          INSTRUCT_SCHEMA, g_instruct, whole_obj=True),
    ]


# ------------------------------------------------------------------ runner
@dataclass
class Result:
    qid: str
    cat: str
    mode: str
    ok: bool
    detail: str
    secs: float
    out_tokens: int | None = None
    reasoning_chars: int = 0
    raw: str = ""
    reasoning: str = ""


async def ask(client, base, model, q: Q, mode: str, max_tok: dict) -> Result:
    payload = {
        "model": model, "temperature": 0.1,
        "max_tokens": max_tok[mode],
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": q.prompt}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": q.id, "strict": True, "schema": q.schema}},
    }
    if mode == "off":
        payload["reasoning_effort"] = "none"
    t0 = time.perf_counter()
    try:
        r = await client.post(f"{base}/chat/completions", json=payload)
        dt = time.perf_counter() - t0
        if r.status_code != 200:
            return Result(q.id, q.cat, mode, False, f"HTTP {r.status_code}: {r.text[:200]}", dt)
        resp = r.json()
    except Exception as e:
        return Result(q.id, q.cat, mode, False, f"error {e!r}", time.perf_counter() - t0)

    ch = resp["choices"][0]
    msg = ch["message"]
    content = msg.get("content") or ""
    reasoning = (msg.get("reasoning_content") or msg.get("reasoning") or "") \
        + "".join(THINK_RE.findall(content))
    raw = THINK_RE.sub("", content).strip()
    out_tok = (resp.get("usage") or {}).get("completion_tokens")
    base_res = dict(qid=q.id, cat=q.cat, mode=mode, secs=dt, out_tokens=out_tok,
                    reasoning_chars=len(reasoning), raw=raw, reasoning=reasoning)
    if ch.get("finish_reason") == "length" and not raw:
        return Result(ok=False, detail="โดนตัด (max_tokens หมดระหว่างคิด)", **base_res)
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return Result(ok=False, detail=f"parse JSON ไม่ได้: {raw[:120]!r}", **base_res)
    try:
        ok, detail = q.grade(obj if q.whole_obj else obj.get("answer"))
    except Exception as e:
        ok, detail = False, f"grader error {e!r}"
    return Result(ok=ok, detail=detail, **base_res)


def print_report(model: str, results: list[Result], modes: list[str]) -> None:
    print(f"\n{'=' * 78}\nผลสอบ — {model}\n{'=' * 78}")
    by_q: dict[str, dict[str, Result]] = {}
    for r in results:
        by_q.setdefault(r.qid, {})[r.mode] = r
    head = f"  {'ข้อ':24s}{'หมวด':10s}" + "".join(f"{('think ' + m):>16s}" for m in modes)
    print(head)
    for qid, per in by_q.items():
        cat = next(iter(per.values())).cat
        cells = "".join(
            f"{('✅' if per[m].ok else '❌') + f' {per[m].secs:5.1f}s':>16s}" if m in per else f"{'-':>16s}"
            for m in modes)
        print(f"  {qid:24s}{cat:10s}{cells}")
    print()
    for m in modes:
        rs = [r for r in results if r.mode == m]
        if not rs:
            continue
        score = sum(r.ok for r in rs)
        secs = sum(r.secs for r in rs)
        cats = {}
        for r in rs:
            c = cats.setdefault(r.cat, [0, 0])
            c[0] += r.ok
            c[1] += 1
        cat_s = " · ".join(f"{k} {v[0]}/{v[1]}" for k, v in cats.items())
        print(f"  think {m:3s}: {score}/{len(rs)} ({score / len(rs):.0%}) · รวม {secs:.0f}s · {cat_s}")
    print("\n  ข้อที่ผิด:")
    wrong = [r for r in results if not r.ok]
    for r in wrong:
        print(f"   ❌ [{r.mode}] {r.qid}: {r.detail}")
    if not wrong:
        print("   (ไม่มี 🎉)")


def _clip(text: str, n: int) -> str:
    text = text.strip()
    if n <= 0 or len(text) <= n:
        return text
    half = n // 2
    return f"{text[:half]}\n… (ตัด {len(text) - n} ตัวอักษร) …\n{text[-half:]}"


def show_io(r: Result, n: int) -> None:
    """แสดง thinking + คำตอบของโมเดล (ย่อหน้าให้อ่านง่าย)"""
    ind = "\n      "
    if r.reasoning:
        print(f"    🧠 thinking ({r.reasoning_chars} ตัวอักษร):{ind}"
              + _clip(r.reasoning, n).replace("\n", ind))
    print(f"    💬 output:{ind}" + _clip(r.raw or '(ว่าง)', n).replace("\n", ind))
    print()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    ap.add_argument("--model", required=True)
    ap.add_argument("--modes", default="off,on", help="off,on | off | on")
    ap.add_argument("--only", default="", help="รันเฉพาะข้อ (คั่นด้วย ,)")
    ap.add_argument("--long-items", type=int, default=250,
                    help="จำนวนข่าวใน long_context (~17 token/ข่าว; 250 ≈ 4K token, 500 ≈ 8K)")
    ap.add_argument("--show", choices=["all", "wrong", "none"], default="all",
                    help="แสดง thinking + output: ทุกข้อ / เฉพาะข้อที่ผิด / ไม่แสดง")
    ap.add_argument("--show-chars", type=int, default=1200,
                    help="ความยาวสูงสุดที่แสดงต่อช่อง (0 = แสดงเต็ม)")
    ap.add_argument("--max-tokens-on", type=int, default=4000)
    ap.add_argument("--max-tokens-off", type=int, default=800)
    a = ap.parse_args()

    base = a.base_url.rstrip("/")
    modes = [m.strip() for m in a.modes.split(",") if m.strip()]
    max_tok = {"on": a.max_tokens_on, "off": a.max_tokens_off}
    qs = build_questions(a.long_items)
    if a.only:
        keep = {x.strip() for x in a.only.split(",")}
        qs = [q for q in qs if q.id in keep or q.cat in keep]

    print(f"▶ {a.model} · {len(qs)} ข้อ × modes {modes} (ต้องเปิด Thinking ใน LM Studio ไว้)")
    results: list[Result] = []
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for mode in modes:
            print(f"\n--- think {mode} ---")
            for q in qs:
                r = await ask(client, base, a.model, q, mode, max_tok)
                results.append(r)
                extra = f" · {q.note}" if q.note else ""
                print(f"  {'✅' if r.ok else '❌'} {q.id:24s} {r.secs:6.1f}s  {r.detail}{extra}")
                if a.show != "none" and (a.show == "all" or not r.ok):
                    show_io(r, a.show_chars)

    print_report(a.model, results, modes)
    out = f"eval_{re.sub(r'[^A-Za-z0-9]+', '-', a.model)}_{datetime.now():%Y%m%d-%H%M}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"model": a.model, "base_url": base, "at": datetime.now().isoformat(),
                   "results": [r.__dict__ for r in results]}, f, ensure_ascii=False, indent=2)
    print(f"\n  บันทึกผลไว้ที่ {out}")


if __name__ == "__main__":
    asyncio.run(main())
