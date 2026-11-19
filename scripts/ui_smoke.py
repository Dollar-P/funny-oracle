"""Real (non-mocked) UI smoke test: press Predict in a headless Streamlit AppTest and check every section.

    python scripts/ui_smoke.py GC=F swing 1M      # costs one real prediction
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from streamlit.testing.v1 import AppTest  # noqa: E402

SECTIONS = ["Bear (P10)", "Base (P50)", "Bull (P90)", "P(up)", "Confidence", "Agent scores",
            "Debate", "Tool-call trace", "Sources"]


def main(ticker: str, mode: str, horizon: str) -> int:
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=240).run()
    at.text_input(key="ticker").set_value(ticker).run()
    at.radio[0].set_value(mode).run()
    next(s for s in at.selectbox if s.label == "Horizon").set_value(horizon).run()
    next(b for b in at.button if "Predict" in b.label).click().run()
    if at.exception:
        print("EXCEPTION:", at.exception)
        return 1
    errors = [e.value for e in at.error]
    texts = "\n".join(str(x) for x in [*(m.value for m in at.markdown), *(m.label for m in at.metric),
                                       *(e.label for e in at.expander), *(c.value for c in at.caption)])
    missing = [s for s in SECTIONS if s not in texts]
    charts = len(at.get("plotly_chart"))
    print(f"charts={charts} missing={missing} errors={errors}")
    for m in at.metric:
        print(f"  {m.label}: {m.value} {m.delta or ''}")
    heads = [m.value for m in at.markdown if m.value.startswith(("### 🔮", "> **"))]
    print("  " + "\n  ".join(heads))
    chat = [c.proto.name for c in at.get("chat_message")]
    print("  debate thread:", " → ".join(chat) or "(none)")
    from core import scoreboard

    p = scoreboard.load(int(scoreboard.recent(1)["id"].iloc[0]))
    for d in p.debate:
        print(f"  [{d.side} {d.turn}] ({len(d.argument.split())} words) {d.argument[:160]}…")
    tech = next((r for r in p.reports if r.agent == "technical"), None)
    if tech:
        print("  technical key points:")
        for k in tech.key_points:
            print("   -", k)
    debate_ok = p.mode != "swing" or chat == ["Bull", "Bear", "Bull"]
    return 0 if not missing and charts >= 2 and not errors and debate_ok else 1


if __name__ == "__main__":
    raise SystemExit(main(*(sys.argv[1:4] if len(sys.argv) >= 4 else ("GC=F", "swing", "1M"))))
