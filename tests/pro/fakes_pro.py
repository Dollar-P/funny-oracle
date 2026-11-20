"""Pro test doubles: the Arena's role-aware fakes extended with the v2 (Desk) output fields."""
from __future__ import annotations

import json

from tests.fakes import text_response
from tests.test_orchestrator import SCORES, RoleFake

DESK = "Desk (research) rules"

PRO_AGENT_EXTRA = {
    "scenario_probs": {"bull": 0.5, "base": 0.3, "bear": 0.2},
    "key_levels": {"support": [95.0, 90.0], "resistance": [110.0]},
    "invalidation": "a daily close below 95",
}
PRO_JUDGE_EXTRA = {
    "thesis": "Trend and flows outweigh stretched valuation.",
    "invalidation": "a close below the SMA50",
    "catalysts": [{"date": "2026-12-10", "event": "FOMC", "impact": "high"}],
    "no_trade_reason": "",
}


def _with_pro_fields(resp, extra_agent=PRO_AGENT_EXTRA, extra_judge=PRO_JUDGE_EXTRA):
    try:
        text = resp.candidates[0].content.parts[0].text
        data = json.loads(text)
    except Exception:  # function-call turn or non-JSON
        return resp
    if "score" in data:
        data = {**data, **extra_agent}
    elif "adjustment" in data:
        data = {**data, **extra_judge}
    else:
        return resp
    return text_response(json.dumps(data))


class ProRoleFake(RoleFake):
    """RoleFake that answers v2 prompts with the Pro fields (and v1 prompts exactly like RoleFake)."""

    def __init__(self, *a, judge_extra=None, agent_extra=None, scores=None, **kw):
        super().__init__(*a, **kw)
        self.judge_extra = PRO_JUDGE_EXTRA if judge_extra is None else judge_extra
        self.agent_extra = PRO_AGENT_EXTRA if agent_extra is None else agent_extra
        self.scores = scores

    async def _gen(self, model, contents, config):
        resp = await super()._gen(model, contents, config)
        sys_ = (config.system_instruction or "") if config else ""
        if self.scores:
            resp = self._rescore(resp)
        if DESK in sys_:
            return _with_pro_fields(resp, self.agent_extra, self.judge_extra)
        return resp

    def _rescore(self, resp):
        try:
            data = json.loads(resp.candidates[0].content.parts[0].text)
        except Exception:
            return resp
        if "score" in data:
            # RoleFake used SCORES[role]; map the value back to the role and substitute
            role = next((r for r, v in SCORES.items() if v == data["score"]), None)
            if role and role in self.scores:
                data["score"] = self.scores[role]
                return text_response(json.dumps(data))
        return resp


def local_role_reply(payload: dict):
    """Scripted /chat/completions reply for the local backend that answers by role (Pro fields when v2)."""
    from tests.test_local_llm import msg

    system = payload["messages"][0]["content"]
    desk = DESK in system
    if "JUDGE" in system:
        v = {"adjustment": 0.05, "final_confidence": 0.6, "headline": "Plain view", "summary": "s",
             "key_risks": ["r1", "r2"]}
        return msg(json.dumps({**v, **(PRO_JUDGE_EXTRA if desk else {})}))
    if "You are the BULL" in system or "You are the BEAR" in system:
        return msg(json.dumps({"argument": "case", "strongest_opponent_point": "point"}))
    if "news researcher" in system:
        return msg(json.dumps({"bullets": ["News A"], "links": []}))
    a = {"score": 0.3, "confidence": 0.7, "key_points": ["a 1", "b 2", "c 3"], "reasoning": "r", "sources": []}
    return msg(json.dumps({**a, **(PRO_AGENT_EXTRA if desk else {})}))


class LocalRoleFake:
    def __init__(self):
        self.requests: list[dict] = []

    async def post(self, url, json=None, timeout=None):
        self.requests.append(json)
        return local_role_reply(json)
