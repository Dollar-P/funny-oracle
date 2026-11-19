"""
test_lmstudio.py — ทดสอบว่า local LLM บน LM Studio ใช้กับ Funny Oracle ได้แค่ไหน

ใช้แค่ httpx (มีใน requirements.txt อยู่แล้ว)

    .venv\\Scripts\\python scripts\\test_lmstudio.py
    .venv\\Scripts\\python scripts\\test_lmstudio.py --model prism-ml/bonsai-27b
    .venv\\Scripts\\python scripts\\test_lmstudio.py --base-url http://<lm-studio-host>:1234/v1 --skip-concurrency

ทดสอบ 5 อย่าง (ตรงกับสิ่งที่ agent ในโปรเจกต์ต้องใช้):
  1. models       — เชื่อมต่อได้ไหม มีโมเดลอะไรโหลดอยู่
  2. chat         — ตอบได้ไหม ความเร็วกี่ token/s
  3. json_schema  — ส่งออก AgentReport เป็น JSON ตาม schema ได้ไหม
  4. tool_call    — เรียก tool (get_indicators) แล้วอ่านผลกลับมาสรุปได้ไหม  ← สำคัญที่สุด
  5. concurrency  — รัน 3 request พร้อมกันได้เร็วกว่ารันทีละอันไหม (5 analysts รันแบบ parallel)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from pathlib import Path

import httpx

# Local OpenAI-compatible server (LM Studio etc.): LOCAL_BASE_URL from the environment or .env,
# or --base-url on the command line. Never hard-code a LAN address here (it ends up on GitHub).
try:  # optional: read .env like the rest of the project (the script itself only needs httpx)
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass
DEFAULT_BASE = os.getenv("LOCAL_BASE_URL", "http://localhost:1234/v1")
TIMEOUT = httpx.Timeout(300.0, connect=10.0)

AGENT_REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "agent": {"type": "string"},
        "score": {"type": "number", "minimum": -1, "maximum": 1,
                  "description": "Direction for the horizon as a decimal from -1.0 (strong down) "
                                 "to 1.0 (strong up). Example: 0.35. NOT a percentage."},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1,
                       "description": "Decimal from 0.0 to 1.0. Example: 0.6"},
        "key_points": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 5},
        "reasoning": {"type": "string"},
    },
    "required": ["agent", "score", "confidence", "key_points", "reasoning"],
    "additionalProperties": False,
}

GET_INDICATORS_TOOL = {
    "type": "function",
    "function": {
        "name": "get_indicators",
        "description": "Get technical indicators (RSI, MACD, SMA, Bollinger, ATR) for a ticker.",
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "e.g. BTC-USD, NVDA"},
                "interval": {"type": "string", "enum": ["15m", "1h", "1d"]},
            },
            "required": ["ticker", "interval"],
        },
    },
}

# ผลลัพธ์ปลอม (ไม่ต้องรัน MCP server จริง)
FAKE_INDICATORS = {
    "ticker": "BTC-USD", "interval": "1h", "as_of": "2026-11-19T16:00:00Z",
    "last": 91250.0, "rsi14": 71.8, "macd": {"macd": 412.5, "signal": 380.1, "hist": 32.4},
    "sma20": 89800.0, "sma50": 87400.0, "sma200": 84100.0,
    "bollinger": {"upper": 91600.0, "mid": 89800.0, "lower": 88000.0},
    "atr14": 820.0, "volume_z": 1.9, "support": [89500, 88000], "resistance": [91600, 93000],
}

THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def strip_think(text: str | None) -> str:
    """บางโมเดล (reasoning) ใส่ <think>...</think> มาก่อนคำตอบ"""
    text = THINK_RE.sub("", text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)  # ตัด code fence ถ้ามี
    return m.group(1).strip() if m else text


def header(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def result(ok: bool, msg: str) -> bool:
    print(f"  {'✅ PASS' if ok else '❌ FAIL'}  {msg}")
    return ok


MAX_TOKENS_OVERRIDE: int | None = None  # ตั้งจาก --max-tokens (reasoning model ต้องใช้เยอะ)
REASONING_EFFORT: str | None = None     # ตั้งจาก --reasoning-effort (low/medium/high)


def diag(resp: dict) -> None:
    """แสดง finish_reason + ส่วนที่โมเดล 'คิด' (reasoning_content) ถ้ามี"""
    ch = resp["choices"][0]
    msg = ch.get("message", {})
    reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
    print(f"  [diag] finish_reason={ch.get('finish_reason')} · content={len(msg.get('content') or '')} chars"
          f" · reasoning={len(reasoning)} chars")
    if reasoning:
        print(f"  [diag] reasoning (ท้าย): …{reasoning[-200:]!r}")
    if ch.get("finish_reason") == "length":
        print("  [diag] ⚠ โดนตัดเพราะ max_tokens หมด → ลอง --max-tokens 4000")


async def chat(client: httpx.AsyncClient, base: str, payload: dict) -> tuple[dict, float]:
    if MAX_TOKENS_OVERRIDE:
        payload = {**payload, "max_tokens": MAX_TOKENS_OVERRIDE}
    if REASONING_EFFORT:
        payload = {**payload, "reasoning_effort": REASONING_EFFORT}
    t0 = time.perf_counter()
    r = await client.post(f"{base}/chat/completions", json=payload)
    dt = time.perf_counter() - t0
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:500]}")
    resp = r.json()
    if not (resp["choices"][0]["message"].get("content") or "").strip() or \
            resp["choices"][0].get("finish_reason") == "length":
        diag(resp)
    return resp, dt


def tok_speed(resp: dict, dt: float) -> str:
    u = resp.get("usage") or {}
    out = u.get("completion_tokens")
    if not out:
        return f"{dt:.1f}s"
    return f"{dt:.1f}s · in {u.get('prompt_tokens')} / out {out} tok · ~{out / dt:.1f} tok/s"


# ---------------------------------------------------------------- tests
async def t_models(client, base, model) -> str | None:
    header("1) GET /models — เชื่อมต่อได้ไหม")
    try:
        r = await client.get(f"{base}/models")
        r.raise_for_status()
    except Exception as e:
        result(False, f"เชื่อมต่อไม่ได้: {e!r}")
        print("  → เช็ค: LM Studio > Developer > Server running, เปิด 'Serve on Local Network',"
              " Windows Firewall อนุญาต port 1234")
        return None
    ids = [m["id"] for m in r.json().get("data", [])]
    result(True, f"พบ {len(ids)} โมเดล: {ids}")
    if model and model not in ids:
        print(f"  ⚠ ไม่พบ '{model}' ในรายการ (LM Studio อาจ JIT-load ให้เองถ้าเปิดไว้)")
        return model
    return model or (ids[0] if ids else None)


async def t_chat(client, base, model) -> bool:
    header("2) Chat ธรรมดา — ตอบได้ไหม เร็วแค่ไหน")
    resp, dt = await chat(client, base, {
        "model": model, "temperature": 0.3, "max_tokens": 200,
        "messages": [{"role": "user", "content":
                      "In 2 sentences: what does RSI above 70 usually indicate?"}],
    })
    text = strip_think(resp["choices"][0]["message"].get("content"))
    print(f"  ตอบ: {text[:300]}")
    return result(bool(text), tok_speed(resp, dt))


async def t_json(client, base, model) -> bool:
    header("3) Structured JSON (response_format=json_schema) — AgentReport (+ retry 1 ครั้ง)")
    messages = [
        {"role": "system", "content":
            "You are a senior Technical analyst. Reply with JSON only.\n"
            "Rules: score is a DECIMAL between -1.0 and 1.0 (e.g. 0.35), never a percentage. "
            "confidence is a DECIMAL between 0.0 and 1.0. key_points: 3 to 5 items. "
            "Compare numbers carefully: price above an SMA means the SMA value is lower than price."},
        {"role": "user", "content": "Asset BTC-USD, scalp mode, horizon 1h. Data: "
         + json.dumps(FAKE_INDICATORS) + "\nGive your AgentReport (agent='technical')."},
    ]
    total = 0.0
    for attempt in (1, 2):
        resp, dt = await chat(client, base, {
            "model": model, "temperature": 0.2, "max_tokens": 600,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "agent_report", "strict": True, "schema": AGENT_REPORT_SCHEMA}},
            "messages": messages,
        })
        total += dt
        raw = strip_think(resp["choices"][0]["message"].get("content"))
        problems = []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
            problems.append("parse JSON ไม่ได้")
            print(f"  raw: {raw[:400]}")
        if data:
            if not -1 <= float(data.get("score", 99)) <= 1:
                problems.append(f"score={data.get('score')} นอกช่วง -1..1")
            if not 0 <= float(data.get("confidence", 99)) <= 1:
                problems.append(f"confidence={data.get('confidence')} นอกช่วง 0..1")
            if not 3 <= len(data.get("key_points", [])) <= 5:
                problems.append("key_points ไม่ใช่ 3–5 ข้อ")
            print(f"  [ครั้งที่ {attempt}] score={data.get('score')} conf={data.get('confidence')} · {dt:.1f}s")
        if not problems:
            print(f"  key_points={data.get('key_points')}")
            note = "ผ่านตั้งแต่ครั้งแรก" if attempt == 1 else "ผ่านหลัง retry"
            return result(True, f"{note} · รวม {total:.1f}s")
        print(f"  [ครั้งที่ {attempt}] ปัญหา: {'; '.join(problems)}")
        # retry: บอกโมเดลว่าผิดตรงไหน (แบบเดียวกับที่ llm.py ควรทำ)
        messages += [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": "Your JSON is invalid: " + "; ".join(problems)
             + ". score must be a decimal between -1.0 and 1.0 (divide by 100 if you used a"
               " percentage). Return the corrected JSON only."},
        ]
    return result(False, f"ยังผิดหลัง retry · รวม {total:.1f}s")


async def t_tools(client, base, model) -> bool:
    header("4) Tool calling — เรียก get_indicators แล้วสรุปผล (agent loop 2 รอบ)")
    messages = [
        {"role": "system", "content": "You are a Technical analyst. Always use tools to get real "
         "data before concluding. Never invent numbers."},
        {"role": "user", "content": "Analyse BTC-USD on the 1h chart for the next hour."},
    ]
    resp, dt = await chat(client, base, {
        "model": model, "temperature": 0.2, "max_tokens": 400,
        "messages": messages, "tools": [GET_INDICATORS_TOOL], "tool_choice": "auto",
    })
    msg = resp["choices"][0]["message"]
    calls = msg.get("tool_calls") or []
    if not calls:
        print(f"  ไม่ได้เรียก tool, ตอบว่า: {strip_think(msg.get('content'))[:300]}")
        return result(False, "โมเดลไม่เรียก tool (หรือเรียกในรูปแบบที่ LM Studio parse ไม่ได้)")
    c = calls[0]
    try:
        args = json.loads(c["function"]["arguments"] or "{}")
    except json.JSONDecodeError:
        return result(False, f"arguments ไม่ใช่ JSON: {c['function']['arguments']!r}")
    ok_args = c["function"]["name"] == "get_indicators" and args.get("ticker", "").upper() == "BTC-USD"
    result(ok_args, f"รอบ 1: {c['function']['name']}({args}) · {dt:.1f}s")

    messages.append(msg)
    messages.append({"role": "tool", "tool_call_id": c.get("id", "call_0"),
                     "content": json.dumps(FAKE_INDICATORS)})
    resp2, dt2 = await chat(client, base, {
        "model": model, "temperature": 0.2, "max_tokens": 400,
        "messages": messages, "tools": [GET_INDICATORS_TOOL],
    })
    final = strip_think(resp2["choices"][0]["message"].get("content"))
    print(f"  รอบ 2 สรุป: {final[:400]}")
    uses_data = any(s in final for s in ("71", "RSI", "rsi", "91"))
    return result(ok_args and uses_data,
                  f"อ่านผล tool แล้วอ้างตัวเลขจริง: {uses_data} · {dt2:.1f}s")


async def t_concurrency(client, base, model, n=3) -> bool:
    header(f"5) Concurrency — {n} request พร้อมกัน vs ทีละอัน")
    payload = {"model": model, "temperature": 0.3, "max_tokens": 120,
               "messages": [{"role": "user", "content": "List 3 risks for gold this month. Be brief."}]}
    t0 = time.perf_counter()
    await chat(client, base, payload)
    single = time.perf_counter() - t0
    t0 = time.perf_counter()
    await asyncio.gather(*(chat(client, base, payload) for _ in range(n)))
    par = time.perf_counter() - t0
    speedup = single * n / par
    print(f"  1 request: {single:.1f}s · {n} พร้อมกัน: {par:.1f}s · speedup ≈ {speedup:.1f}x")
    if speedup < 1.3:
        print("  → LM Studio น่าจะประมวลผลทีละ request: ตั้ง semaphore = 1 สำหรับ provider นี้"
              " หรือเปิด parallel/concurrent predictions ในการตั้งค่าโหลดโมเดล (ถ้าเวอร์ชันรองรับ)")
    return result(True, "ข้อมูลไว้ตั้งค่า semaphore (ไม่ถือว่า fail)")


async def t_think_toggle(client, base, model) -> bool:
    header("6) Thinking toggle ทีละ request — คุมได้จาก API ไหม")
    print("  (ต้องเปิด Thinking ใน LM Studio ไว้ก่อน แล้วดูว่าแต่ละวิธีปิดมันได้ไหม)")
    q = "Is a price of 91250 above or below an SMA200 of 84100? One sentence."
    variants = {
        "baseline (ไม่ส่งอะไร)": {},
        "reasoning_effort=\"off\"": {"extra": {"reasoning_effort": "off"}},
        "reasoning_effort=\"none\"": {"extra": {"reasoning_effort": "none"}},
        "reasoning_effort=\"minimal\"": {"extra": {"reasoning_effort": "minimal"}},
        "reasoning_effort=\"low\"": {"extra": {"reasoning_effort": "low"}},
        "reasoning={\"effort\":\"none\"}": {"extra": {"reasoning": {"effort": "none"}}},
    }
    rows = []
    for name, v in variants.items():
        payload = {"model": model, "temperature": 0.2, "max_tokens": 3000,
                   "messages": [{"role": "user", "content": q + v.get("suffix", "")}],
                   **v.get("extra", {})}
        try:
            resp, dt = await chat(client, base, payload)
        except Exception as e:
            rows.append((name, None, None, "HTTP error (ดูข้อความเต็มด้านล่าง)"))
            print(f"\n  ⚠ {name} → {e}")
            continue
        msg = resp["choices"][0]["message"]
        content = msg.get("content") or ""
        r_len = len(msg.get("reasoning_content") or msg.get("reasoning") or "") \
            + sum(len(m) for m in THINK_RE.findall(content))
        rows.append((name, dt, r_len, strip_think(content)[:60]))
    print()
    for name, dt, r_len, ans in rows:
        t = f"{dt:5.1f}s" if dt is not None else "  -  "
        print(f"  {name:45s} {t} · reasoning={r_len} chars · {ans!r}")
    base_len = rows[0][2] or 0
    works = [n for n, _, r, _ in rows[1:] if r is not None and base_len > 0 and r < base_len * 0.2]
    if base_len == 0:
        print("\n  ⚠ baseline ไม่มี reasoning — Thinking ใน LM Studio น่าจะยังปิดอยู่ เปิดก่อนแล้วรันใหม่")
        return result(False, "ทดสอบไม่ได้")
    return result(bool(works), f"วิธีที่ปิด thinking ได้: {works or 'ไม่มี'}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    ap.add_argument("--model", default=None, help="ไม่ใส่ = ใช้โมเดลแรกที่โหลดอยู่")
    ap.add_argument("--skip-concurrency", action="store_true")
    ap.add_argument("--only-think-toggle", action="store_true",
                    help="รันเฉพาะเทสข้อ 6 (thinking toggle)")
    ap.add_argument("--max-tokens", type=int, default=None,
                    help="บังคับ max_tokens ทุก request (reasoning model แนะนำ 3000–4000)")
    ap.add_argument("--reasoning-effort", default=None,
                    help="ส่ง reasoning_effort ตามที่โมเดลรองรับ เช่น on/off (bonsai) หรือ low/medium/high (gpt-oss)")
    a = ap.parse_args()
    global MAX_TOKENS_OVERRIDE, REASONING_EFFORT
    MAX_TOKENS_OVERRIDE = a.max_tokens
    REASONING_EFFORT = a.reasoning_effort
    base = a.base_url.rstrip("/")

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        model = await t_models(client, base, a.model)
        if not model:
            return
        print(f"\n▶ ทดสอบโมเดล: {model}")
        scores = {}
        tests = [("chat", t_chat), ("json_schema", t_json), ("tool_call", t_tools)]
        if not a.skip_concurrency:
            tests.append(("concurrency", t_concurrency))
        if a.only_think_toggle:
            tests = [("think_toggle", t_think_toggle)]
        for name, fn in tests:
            try:
                scores[name] = await fn(client, base, model)
            except Exception as e:
                scores[name] = result(False, f"error: {e!r}")

    header(f"สรุป — {model}")
    for k, v in scores.items():
        print(f"  {'✅' if v else '❌'} {k}")
    if scores.get("json_schema") and scores.get("tool_call"):
        print("\n  👍 ใช้เป็น analyst agent ได้ (tool + JSON ผ่าน)")
    elif scores.get("json_schema"):
        print("\n  🟡 ใช้ได้กับ agent ที่ไม่ต้องเรียก tool: Bull / Bear / Judge"
              " หรือป้อนข้อมูลให้ล่วงหน้า (pre-fetch) แทนการให้โมเดลเรียก tool เอง")
    else:
        print("\n  🔴 ยังไม่พร้อม — ลองโมเดลที่ใหญ่ขึ้น")


if __name__ == "__main__":
    asyncio.run(main())