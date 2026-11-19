from agents.base import BaseAgent


class MacroAgent(BaseAgent):
    name = "macro"
    role = "macro"
    tools = {"*": ["get_macro", "get_news"]}
    uses_search = True
    uses_news_brief = True
