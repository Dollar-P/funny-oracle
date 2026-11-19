"""Agent registry. Add a new analyst by creating a BaseAgent subclass and listing it here."""
from agents.business import BusinessAgent
from agents.macro import MacroAgent
from agents.psychology import PsychologyAgent
from agents.technical import TechnicalAgent
from agents.valuation import ValuationAgent

REGISTRY = {a.name: a for a in (BusinessAgent, TechnicalAgent, ValuationAgent, MacroAgent, PsychologyAgent)}

__all__ = ["REGISTRY"]
