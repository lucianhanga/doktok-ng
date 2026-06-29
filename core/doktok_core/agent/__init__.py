"""Agentic chat: the single-agent tool-calling loop (ADR-0022 Phase 2b)."""

from doktok_core.agent.loop import run_agent, run_agent_stream

__all__ = ["run_agent", "run_agent_stream"]
