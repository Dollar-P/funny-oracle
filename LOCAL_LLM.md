# Funny Oracle — Local LLM (LM Studio) Findings

> Companion to `ARCHITECTURE.md` / `REDESIGN.md`. Result of testing a free local provider with `scripts/test_lmstudio.py` (2026-11-20).
> Implementation status: see `PROGRESS.md` → Local LLM.

## Setup tested

| | |
|---|---|
| Server | LM Studio, OpenAI-compatible API at `http://<lm-studio-host>:1234/v1` (another machine on the same LAN; the real address lives only in your local `.env`) |
| GPU | RTX 4050 Notebook, 6 GB VRAM |
| Winner | `prism-ml/bonsai-27b` — 1-bit (binary) Qwen3.6-27B, ~3.9 GB, reasoning model |
| LM Studio settings | **Thinking ON** (the app turns it off per request — see below), full GPU offload, context 8K–16K (don't exceed VRAM), plugged in / performance mode |

## Results

| Test | gemma-3-1b-it | bonsai-27b (thinking ON) | bonsai-27b (thinking OFF) |
|---|---|---|---|
| Chat | ✅ 1.7 s | ✅ 35.8 s | ✅ 3.5 s (~11 tok/s) |
| JSON AgentReport | ❌ score=72.5 | ✅ 208 s (2,960 tok, mostly thinking) | ✅ **17 s, first try** (after prompt fix) |
| Tool call (round 1) | ❌ never calls tools, invents "TradingView" | ✅ 12 s | ✅ 3.3 s |
| Summarise tool result | – | ✅ 71 s | ✅ 28 s, cites real numbers |
| Concurrency (3 parallel) | 1.4× | 1.3× | – |

## Per-request thinking control ✅

Tested with LM Studio Thinking ON, same question:

| Request param | Time | Reasoning | Effect |
|---|---|---|---|
| (none) | 17.7 s | 617 chars | thinking on |
| **`reasoning_effort: "none"`** | **2.3 s** | **0** | **thinking off ✅** |
| `reasoning_effort: "minimal"` / `"low"` | 16–18 s | 540–627 | still on (model is on/off only) |
| `reasoning_effort: "off"` | HTTP 400 | – | invalid; allowed: none, minimal, low, medium, high, xhigh |
| `/no_think` suffix, `chat_template_kwargs.enable_thinking`, `reasoning: "off"`, `reasoning: {effort}` | – | unchanged | ignored |

→ Keep Thinking ON in LM Studio; the provider sends `reasoning_effort: "none"` to turn it off, and omits the field to keep it on.

### Thinking policy per step

| Step | Thinking | Why |
|---|---|---|
| Agent tool-selection turns (1..n-1) | off | simple decision, ~3 s |
| Agent final AgentReport turn | **on** | synthesising numbers from several tools |
| Bull / Bear | on | must rebut the other side |
| Judge | **on** | most important, called once |

Presets in the UI: **Quick** = all off (~8–10 min swing, for prompt iteration) · **Deep** = policy above (~20–30 min, overnight scoreboard runs). Estimates, not yet measured end-to-end.

## Lessons → requirements for the `openai_compat` provider in `core/llm.py`

1. **LM Studio's json_schema enforces structure, not numeric ranges.** `minimum/maximum` were ignored (score came back 72 / 72.5). Always validate with Pydantic (`Field(ge=-1, le=1)`).
2. **Prompt fix that worked:** schema `description` per field ("decimal from -1.0 to 1.0, e.g. 0.35, NOT a percentage") + the same rule in the system prompt. Keep the retry-with-feedback (send the invalid JSON back + list of problems) as safety net.
3. **Without thinking the model makes comparison slips** (once said price $91,250 was "below SMA200 $84,100"); worse with long prompts. Prompt rule "compare numbers carefully" + thinking on for synthesis steps (policy above).
4. **Reasoning models:** read `reasoning_content` separately from `content` (could show in war room); strip `<think>…</think>` if it appears in content.
5. **max_tokens:** thinking on ≥ 4000 (else answers truncate to empty); thinking off ~1000.
6. **Final agent turn must be JSON**, not free text — free-text summaries run long (hit 400-token cap at ~28 s), JSON turn is ~17 s.
7. **Concurrency ≈ 1.3×** → `LOCAL_CONCURRENCY=1` (maybe 2).
8. **Timeouts:** raise per-agent timeout for local to 180–300 s (45 s default kills agents).
9. **No Google Search grounding** → own MCP tools `web_search` (RSS → DuckDuckGo fallback; SearXNG/Tavily later) + `fetch_url` (trafilatura, ≤1,500 chars), compact ≤2 KB JSON with `as_of`, cached 30–60 min. News Researcher searches once and shares a NewsBrief with Business/Macro/Psychology. Hybrid option: Gemini flash-lite as the News Researcher only.
10. **Cost:** price 0 in `config.PRICES`; LM Studio returns `usage` so token logging still works; budget guard passes.

## Proposed `.env`

```env
LLM_PROVIDER=local            # gemini | claude | local
LOCAL_BASE_URL=http://<lm-studio-host>:1234/v1
LOCAL_MODEL=prism-ml/bonsai-27b
LOCAL_CONCURRENCY=1
LOCAL_TIMEOUT_S=240
LOCAL_MAX_TURNS=4
LOCAL_THINK_PRESET=deep       # quick | deep
LOCAL_MAX_TOKENS_THINK=4000
LOCAL_MAX_TOKENS_FAST=1000
```

## Where local fits

| Use | Verdict |
|---|---|
| Swing mode, all agents | ✅ |
| Scalp mode | ❌ too slow for a 15 min horizon — keep Gemini |
| Bull / Bear / Judge (no tools) | ✅ easiest win |
| Fallback when budget is out | ✅ |
| Overnight batch → bigger scoreboard sample | ✅ main benefit |
| Duel: "Local (free, slow) vs Gemini" | ✅ fun demo + model leaderboard |

Caveat: prompts tuned on Bonsai may behave differently on Gemini — keep results split per model.

## Not usable on this GPU
- `openai/gpt-oss-20b` — larger than 6 GB VRAM, would spill to RAM (slower than Bonsai).
- Ternary Bonsai 27B (~5.9 GB) — no room left for context.

## Implemented (branch `local-llm`)
`openai_compat` provider + `web_search` / `fetch_url` MCP tools, following lessons 1–10; details and tests in
`PROGRESS.md` → Local LLM. Measured end-to-end with bonsai-27b, **Quick** preset: a swing show took **6.6 min**
(397 s) in the Arena and 352 s as one side of a duel — faster than the 8–10 min estimate above. Deep is still an
estimate. One new finding: in tool turns with thinking off, the model tends to repeat identical tool calls until
the turn limit; the provider now ends the tool phase when a turn only repeats earlier calls.
