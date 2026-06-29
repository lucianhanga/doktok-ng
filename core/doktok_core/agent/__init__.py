"""Agentic chat: the single-agent tool-calling loop (Phase 2b) + cross-source merge (Phase 2c)."""

from doktok_core.agent.loop import run_agent, run_agent_stream
from doktok_core.agent.merge import evidence_block, merge_evidence

__all__ = ["evidence_block", "merge_evidence", "run_agent", "run_agent_stream"]
