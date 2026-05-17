"""LLM brain layer.

Phase 1 ships the Pydantic IO models for every tool the brain can
call. Track B implements ``gemini.py`` (the tool-calling loop) and
``tools.py`` (each tool function dispatching to a service Protocol).
"""

from fruit_market.brain import restock_agent_specs, tool_specs

__all__ = ["tool_specs", "restock_agent_specs"]
