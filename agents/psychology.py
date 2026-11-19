from agents.base import BaseAgent


class PsychologyAgent(BaseAgent):
    name = "psychology"
    role = "market psychology / sentiment"
    tools = {"*": ["get_sentiment", "get_news", "get_price_history"]}
    uses_search = True
    uses_news_brief = True
