"""Funny Oracle — Streamlit UI (§4.9).    streamlit run app.py"""
from __future__ import annotations

import asyncio
import json
import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
from core import cost, scoreboard
from core.asset import classify, normalize
from core.autoscalp import AutoScalpState, tick
from core.cost import BudgetExceeded, BudgetGuard
from core.schemas import Prediction
from mcp_server import data_sources as ds

st.set_page_config(page_title="Funny Oracle", page_icon="🔮", layout="wide")

# --- palette (dataviz reference palette; light/dark steps) -----------------------
LIGHT = {"series": "#2a78d6", "up": "#2a78d6", "down": "#e34948", "neutral": "#c3c2b7", "grid": "#e1e0d9",
         "muted": "#898781", "ink": "#0b0b0b", "band": "rgba(42,120,214,0.16)"}
DARK = {"series": "#3987e5", "up": "#3987e5", "down": "#e66767", "neutral": "#383835", "grid": "#2c2c2a",
        "muted": "#898781", "ink": "#f0efec", "band": "rgba(57,135,229,0.22)"}


def palette() -> dict:
    try:
        return DARK if st.context.theme.type == "dark" else LIGHT
    except Exception:
        return LIGHT


def base_layout(fig: go.Figure, height: int = 360, **kw) -> go.Figure:
    c = palette()
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=28, b=8), paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)", font=dict(family='system-ui, -apple-system, "Segoe UI", sans-serif',
                                                color=c["ink"], size=12),
        hoverlabel=dict(font_size=12), legend=dict(orientation="h", y=1.08, x=0, font=dict(color=c["muted"])), **kw)
    fig.update_xaxes(showgrid=False, linecolor=c["neutral"], tickfont=dict(color=c["muted"]))
    fig.update_yaxes(gridcolor=c["grid"], gridwidth=1, zeroline=False, tickfont=dict(color=c["muted"]))
    return fig


PRESETS = {"NVDA": "NVDA", "AAPL": "AAPL", "BTC": "BTC-USD", "ETH": "ETH-USD", "GOLD": "GC=F"}

# --- state -----------------------------------------------------------------------
ss = st.session_state
ss.setdefault("ticker", "NVDA")
ss.setdefault("pred", None)
ss.setdefault("auto", AutoScalpState())
ss.setdefault("auto_on", False)
ss.setdefault("mode", "swing")


def sync_sidebar(p: Prediction | None) -> None:
    """Point the sidebar widgets at a replayed prediction. Must run before the widgets are created."""
    if p is None:
        return
    ss.ticker, ss.mode, ss.horizon = p.ticker, p.mode, p.horizon
    ss.preset = None
    ss["_last_preset"] = None


if "_replay_sync" in ss:  # set by the Load button on the previous run
    sync_sidebar(ss.pop("_replay_sync"))
if ss.pred is None and st.query_params.get("replay", "").isdigit():  # demo link: ?replay=<prediction id>
    ss.pred = scoreboard.load(int(st.query_params["replay"]), "arena")
    sync_sidebar(ss.pred)


def run_async(coro):
    return asyncio.run(coro)


# --- sidebar ------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## 🔮 Funny Oracle")
    preset = st.pills("Presets", list(PRESETS), key="preset", label_visibility="collapsed")
    if preset and ss.get("_last_preset") != preset:
        ss.ticker = PRESETS[preset]
    ss["_last_preset"] = preset
    ticker = normalize(st.text_input("Ticker", key="ticker"))
    mode = st.radio("Mode", ["swing", "scalp"], horizontal=True, key="mode",
                    format_func=lambda m: "Swing (1W–3M)" if m == "swing" else "Scalp (15m–4h)")
    horizons = list(config.HORIZONS[mode])
    if ss.get("horizon") not in horizons:  # first run, or the mode just changed
        ss.horizon = horizons[1] if mode == "swing" else horizons[0]
    horizon = st.selectbox("Horizon", horizons, key="horizon")
    providers = ["gemini"] + (["claude"] if config.CLAUDE_ANALYST_MODEL else [])
    provider = st.selectbox("LLM", providers, index=providers.index(config.LLM_PROVIDER)
                            if config.LLM_PROVIDER in providers else 0)
    st.caption(f"Model: `{config.models_for(mode, provider)['analyst']}` · {classify(ticker)}")
    predict = st.button("🔮 Predict", type="primary", width="stretch")

    st.divider()
    spent = cost.total_spent_usd()
    used = cost.usd_to_thb(spent)
    st.progress(min(1.0, used / config.BUDGET_THB), text=f"Budget used: ฿{used:.2f} / ฿{config.BUDGET_THB:.0f}")
    st.caption(f"Tracked ฿{used:.2f} (searches at list price) · est. actual ฿{cost.usd_to_thb(cost.total_actual_usd()):.2f}"
               f" (first {config.GEMINI_SEARCH_FREE_PER_MONTH:,} searches/month free)")
    try:
        est = BudgetGuard().estimate_usd(mode)
        st.caption(f"Next {mode} prediction ≈ ฿{cost.usd_to_thb(est):.2f} (estimate)")
    except Exception:
        pass

    st.divider()
    hist = scoreboard.recent(30)
    if not hist.empty:
        opts = {int(r.id): f"#{r.id} {r.ticker} {r.mode} {r.horizon} · {r.created_at[5:16]}" for r in hist.itertuples()}
        pick = st.selectbox("Replay a stored prediction (no API cost)", [None, *opts],
                            format_func=lambda i: "—" if i is None else opts[i])
        if pick is not None and st.button("Load", width="stretch"):
            ss.pred = scoreboard.load(pick, "arena")
            ss["_replay_sync"] = ss.pred
            st.rerun()  # widgets above are already drawn; sync them on the next run
    st.warning("⚠ For education and fun. **Not investment advice.**")

# --- run a prediction -------------------------------------------------------------------
if predict:
    from core.llm import LLM
    from orchestrator import run_prediction

    with st.status(f"Consulting the oracle on {ticker} · {mode} · {horizon}…", expanded=True) as status:
        try:
            pred = run_async(run_prediction(ticker, mode, horizon, progress=lambda m: status.write(m),
                                            llm=LLM(provider=provider), source="manual"))
            ss.pred = pred
            status.update(label=f"Done in {pred.elapsed_s:.0f}s · ฿{cost.usd_to_thb(pred.cost_usd):.2f}",
                          state="complete", expanded=False)
        except BudgetExceeded as e:
            status.update(label="Budget guard stopped the prediction", state="error")
            st.error(str(e))
        except Exception as e:
            status.update(label="Prediction failed", state="error")
            st.exception(e)


# --- render helpers ------------------------------------------------------------------------

def future_index(last: pd.Timestamp, p: Prediction) -> list[pd.Timestamp]:
    steps = [int(s) for s in p.forecast.fan.get("step", [])]
    interval = config.HORIZONS[p.mode][p.horizon]["interval"]
    if interval == "1d":
        if p.asset_type == "crypto":
            return [last + pd.Timedelta(days=s) for s in steps]
        bdays = pd.bdate_range(last, periods=max(steps) + 1, tz=last.tz)
        return [bdays[s] for s in steps]
    minutes = {"1m": 1, "5m": 5, "15m": 15}[interval]
    return [last + pd.Timedelta(minutes=minutes * s) for s in steps]


def price_fan_chart(p: Prediction) -> go.Figure:
    c = palette()
    interval = config.HORIZONS[p.mode][p.horizon]["interval"]
    try:
        hist = ds.fetch_ohlcv(p.ticker, interval, "1y" if interval == "1d" else "5d")
        hist = hist[hist.index <= pd.Timestamp(p.created_at)].tail(120 if interval == "1d" else 180)
    except Exception:
        hist = pd.DataFrame()
    last = hist.index[-1] if not hist.empty else pd.Timestamp(p.created_at)
    fx = future_index(last, p)
    fan = p.forecast.fan
    fig = go.Figure()
    if not hist.empty:
        fig.add_trace(go.Scatter(x=hist.index, y=hist["Close"], name="Price", mode="lines",
                                 line=dict(color=c["series"], width=2),
                                 hovertemplate="%{x|%Y-%m-%d %H:%M}<br>%{y:,.2f}<extra>Price</extra>"))
    fig.add_trace(go.Scatter(x=fx, y=fan["p90"], mode="lines", line=dict(width=0), showlegend=False,
                             hovertemplate="P90 %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=fx, y=fan["p10"], mode="lines", line=dict(width=0), fill="tonexty",
                             fillcolor=c["band"], name="P10–P90 range",
                             hovertemplate="P10 %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=fx, y=fan["p50"], mode="lines", name="P50 (base case)",
                             line=dict(color=c["series"], width=2, dash="dash"),
                             hovertemplate="P50 %{y:,.2f}<extra></extra>"))
    for key, label in (("p90", "Bull"), ("p50", "Base"), ("p10", "Bear")):
        fig.add_annotation(x=fx[-1], y=fan[key][-1], text=f"{label} {fan[key][-1]:,.2f}", showarrow=False,
                           xanchor="left", xshift=6, font=dict(color=c["muted"], size=11))
    base_layout(fig, 380, hovermode="x unified")
    fig.update_layout(legend_traceorder="normal")
    start = hist.index[0] if not hist.empty else fx[0]
    fig.update_xaxes(range=[start, fx[-1] + (fx[-1] - start) * 0.14])  # room for the Bear/Base/Bull labels
    return fig


def score_chart(p: Prediction) -> go.Figure:
    c = palette()
    rows = [r for r in p.reports]
    names = [r.agent.title() + ("" if r.ok else " ⚠") for r in rows] + ["Team (S_final)"]
    vals = [r.score if r.ok else 0 for r in rows] + [p.s_final]
    colors = [c["up"] if v > 0 else c["down"] if v < 0 else c["neutral"] for v in vals]
    text = [f"{v:+.2f}" for v in vals]
    custom = [f"conf {r.confidence:.2f} · weight {p.weights.get(r.agent, 0):.2f}" if r.ok else f"absent: {r.error}"
              for r in rows] + [f"S_raw {p.s_raw:+.3f} + judge {p.verdict.adjustment if p.verdict else 0:+.2f}"]
    fig = go.Figure(go.Bar(x=vals, y=names, orientation="h", marker=dict(color=colors, cornerradius=4),
                           text=text, textposition="outside", cliponaxis=False, customdata=custom,
                           textfont=dict(color=c["ink"]),
                           hovertemplate="%{y}: %{x:+.2f}<br>%{customdata}<extra></extra>"))
    base_layout(fig, 60 + 34 * len(names), showlegend=False)
    fig.update_xaxes(range=[-1.15, 1.15], tickvals=[-1, -0.5, 0, 0.5, 1],
                     ticktext=["-1 strong down", "-0.5", "0", "+0.5", "+1 strong up"], showgrid=True,
                     gridcolor=c["grid"], zeroline=True, zerolinecolor=c["muted"], zerolinewidth=1)
    fig.update_yaxes(autorange="reversed", showgrid=False)
    return fig


def render_prediction(p: Prediction):
    f = p.forecast
    head = st.columns([3, 2])
    head[0].markdown(f"### 🔮 {p.ticker} · {p.mode.title()} · {p.horizon}")
    head[0].caption(f"Prediction #{p.id} · {p.created_at} UTC · target {p.target_time} UTC · "
                    f"{p.elapsed_s:.0f}s · ฿{cost.usd_to_thb(p.cost_usd):.2f} · search: {p.search_mode}")
    if p.verdict:
        head[1].markdown(f"> **{p.verdict.headline}**")

    st.plotly_chart(price_fan_chart(p), width="stretch")

    m = st.columns(5)
    m[0].metric("🐻 Bear (P10)", f"{f.p10:,.2f}", f"{(f.p10 / f.price_now - 1):+.1%}")
    m[1].metric("Base (P50)", f"{f.p50:,.2f}", f"{(f.p50 / f.price_now - 1):+.1%}")
    m[2].metric("🐂 Bull (P90)", f"{f.p90:,.2f}", f"{(f.p90 / f.price_now - 1):+.1%}")
    m[3].metric("P(up)", f"{f.prob_up:.0%}", help="Share of 10,000 simulated paths ending above today's price")
    m[4].metric("Confidence", f"{p.confidence:.2f}")
    st.caption(f"Now {f.price_now:,.2f} · σ {f.sigma_per_step:.4f} per {f.step_label} bar × {f.steps} steps · "
               f"S_final {p.s_final:+.3f} shifts the median by S·λ·σ√T (λ={config.LAMBDA})")

    st.markdown("#### Agent scores")
    st.plotly_chart(score_chart(p), width="stretch")

    cards = st.columns(len(p.reports))
    for col, r in zip(cards, p.reports):
        with col.container(border=True):
            st.markdown(f"**{r.agent.title()}** · w {p.weights.get(r.agent, 0):.2f}")
            if not r.ok:
                st.markdown(f"⚠ absent  \n`{r.error}`")
                continue
            st.markdown(f"### {r.score:+.2f}")
            st.caption(f"confidence {r.confidence:.2f} · {len(r.trace)} tool calls · ฿{cost.usd_to_thb(r.cost_usd):.2f}")
            for k in r.key_points:
                st.markdown(f"- {k}")
            with st.expander("Reasoning"):
                st.write(r.reasoning)

    with st.expander("🐂 vs 🐻 Debate & Judge", expanded=bool(p.debate)):
        if p.debate:
            turns = p.debate if all(d.turn for d in p.debate) else sorted(p.debate, key=lambda d: d.side != "bull")
            labels = {"opening": "opens", "rebuttal": "rebuts", "reply": "replies"}
            for d in turns:
                name = "Bull" if d.side == "bull" else "Bear"
                with st.chat_message(name, avatar="🐂" if d.side == "bull" else "🐻"):
                    st.markdown(f"**{name}** {labels.get(d.turn or '', '')}")
                    if d.turn in ("rebuttal", "reply") and d.strongest_opponent_point:
                        st.caption(f"↩ responding to: {d.strongest_opponent_point}")
                    st.write(d.argument)
        else:
            st.caption("No debate in scalp mode.")
        if p.verdict:
            st.markdown(f"**⚖️ Judge:** {p.verdict.summary}")
            st.markdown(f"Adjustment **{p.verdict.adjustment:+.2f}** · final confidence **{p.verdict.final_confidence:.2f}**")
            st.markdown("**Key risks:** " + " · ".join(p.verdict.key_risks))

    with st.expander("🔍 Tool-call trace (what each agent chose to look up)"):
        rows = [{"agent": r.agent, "#": i + 1, "tool": t.tool,
                 "args": json.dumps(t.args, ensure_ascii=False)[:120], "latency_ms": t.latency_ms,
                 "bytes": t.result_bytes, "error": t.error or ""}
                for r in p.reports for i, t in enumerate(r.trace)]
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        else:
            st.caption("No tool calls recorded.")

    with st.expander("📰 Sources & news"):
        if p.news:
            st.markdown("**News brief**")
            for b in p.news.bullets:
                st.markdown(f"- {b}")
        for r in p.reports:
            urls = [s for s in r.sources if "http" in s]
            tools = [s for s in r.sources if "http" not in s]
            st.markdown(f"**{r.agent.title()}** — tools: {', '.join(tools) or '—'}")
            for u in urls:
                title, _, link = u.rpartition(" | ")
                st.markdown(f"  - [{title or link}]({link})")
    st.caption("⚠ Not investment advice. The LLM only produces a directional score; the price range comes from a "
               "Monte Carlo simulation on historical volatility.")


# --- tabs ----------------------------------------------------------------------------------------
tab_pred, tab_score, tab_about = st.tabs(["Predict", "Scoreboard", "About"])

with tab_pred:
    if ss.pred is None:
        st.info("Pick a ticker, mode and horizon, then press **🔮 Predict** — or replay a stored prediction "
                "from the sidebar.")
    else:
        render_prediction(ss.pred)

with tab_score:
    with st.spinner("Checking predictions whose target time has passed…"):
        try:
            n_new = scoreboard.evaluate_due()
        except Exception as e:  # network trouble shouldn't break the tab
            n_new = 0
            st.warning(f"Evaluation skipped: {e}")
    m = scoreboard.metrics()
    if n_new:
        st.success(f"Evaluated {n_new} new prediction(s).")
    c = st.columns(4)
    hit = m.get("hit_rate")
    c[0].metric("Direction hit rate", f"{hit:.0%}" if hit is not None else "—",
                f"{(hit - 0.5) * 100:+.0f} pts vs coin flip" if hit is not None else None,
                help="Predicted direction = P(up) > 50%. A coin flip scores 50%.")
    cov = m.get("band_coverage")
    c[1].metric("Band coverage (P10–P90)", f"{cov:.0%}" if cov is not None else "—",
                help="Well-calibrated ≈ 80%")
    mape = m.get("mape")
    c[2].metric("MAPE of P50", f"{mape:.2%}" if mape is not None else "—")
    c[3].metric("Evaluated / pending", f"{m['n']} / {m['pending']}")
    if hit is not None:
        st.markdown(f"**The AI is right {hit:.0%} of the time… a coin flip gets 50%.**")

    lb = m.get("leaderboard")
    if lb:
        st.markdown("#### Agent leaderboard (direction of each agent's score)")
        df = pd.DataFrame(lb)
        pal = palette()
        fig = go.Figure(go.Bar(x=df["hit_rate"], y=df["agent"].str.title(), orientation="h",
                               marker=dict(color=pal["series"], cornerradius=4),
                               text=[f"{h:.0%} (n={n})" for h, n in zip(df["hit_rate"], df["n"])],
                               textposition="outside", cliponaxis=False, textfont=dict(color=pal["ink"]),
                               hovertemplate="%{y}: %{x:.0%}<extra></extra>"))
        fig.add_vline(x=0.5, line=dict(color=pal["muted"], dash="dot", width=1),
                      annotation_text="coin flip", annotation_font_color=pal["muted"])
        base_layout(fig, 60 + 36 * len(df), showlegend=False)
        fig.update_xaxes(range=[0, 1.15], tickformat=".0%", showgrid=True, gridcolor=pal["grid"])
        fig.update_yaxes(autorange="reversed")
        st.plotly_chart(fig, width="stretch")

    st.markdown("#### Recent predictions")
    st.dataframe(scoreboard.recent(50), hide_index=True, width="stretch")

    st.divider()
    st.markdown("#### 🤖 Auto-Scalp demo")
    auto: AutoScalpState = ss.auto
    st.caption(f"Predicts BTC-USD 15m every {auto.interval_s // 60} min while this page is open "
               f"(cap {auto.max_runs} runs per session; budget guard applies).")
    ss.auto_on = st.toggle("Auto-Scalp", value=ss.auto_on)

    @st.fragment(run_every=20)
    def auto_panel():
        a: AutoScalpState = ss.auto
        if ss.auto_on:
            line = tick(a)
            if a.stopped_reason:
                st.warning(f"Auto-Scalp stopped: {a.stopped_reason}")
            else:
                st.info(f"Running · {a.runs}/{a.max_runs} runs · next in {a.seconds_to_next()}s"
                        + (f" · {line}" if line else ""))
        else:
            st.caption(f"Off · {a.runs} runs this session")
        for entry in a.log[-8:][::-1]:
            st.text(entry)

    auto_panel()

with tab_about:
    st.markdown("""
**Funny Oracle** — five AI analysts (Business, Technical, Valuation, Macro, Psychology) look up real market
data through an MCP server, argue in a Bull-vs-Bear debate, and a Judge settles the score.

The LLM does **not** guess the price. It produces a directional score in [-1, +1]; that score nudges a
**Monte Carlo simulation** driven by historical volatility (|S| = 1 moves the median by at most ½σ over the
horizon). The scoreboard then checks the oracle against reality — and against a coin flip.

Pipeline: classify asset → weights by mode × asset → analysts in parallel (agentic tool loop over MCP) →
weighted score → debate → judge (±0.2 max) → Monte Carlo P10/P50/P90 → scoreboard.

**For education and fun. Not investment advice.**
""")
    st.json(cost.spend_summary(), expanded=False)
