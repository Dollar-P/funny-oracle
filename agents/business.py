from agents.base import BaseAgent


class BusinessAgent(BaseAgent):
    name = "business"
    role = "business / fundamental"
    tools = {"stock": ["get_fundamentals", "get_news"], "*": ["get_news"]}
    uses_search = True
    uses_news_brief = True
