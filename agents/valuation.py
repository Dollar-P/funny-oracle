from agents.base import BaseAgent


class ValuationAgent(BaseAgent):
    name = "valuation"
    role = "valuation"
    tools = {
        "stock": ["get_fundamentals", "get_price_history", "get_indicators"],
        "crypto": ["get_onchain_lite", "get_indicators", "get_price_history"],
        "gold": ["get_macro", "get_indicators", "get_price_history"],
    }
