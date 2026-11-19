from agents.base import BaseAgent

T = ["get_price_history", "get_indicators"]


class TechnicalAgent(BaseAgent):
    name = "technical"
    role = "technical"
    tools = {"*": T}
