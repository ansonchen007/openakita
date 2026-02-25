"""
Multi-agent handler — delegate_to_agent and create_agent.

Only registered when settings.multi_agent_enabled is True.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)

DYNAMIC_AGENT_POLICIES = {
    "max_agents_per_session": 3,
    "max_delegation_depth": 5,
    "forbidden_tools": {"create_agent"},
    "max_lifetime_minutes": 60,
}


class AgentToolHandler:
    """Handles delegate_to_agent, delegate_parallel, and create_agent tool calls."""

    TOOLS = ["delegate_to_agent", "delegate_parallel", "create_agent"]

    def __init__(self, agent: Agent):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name == "delegate_to_agent":
            return await self._delegate(params)
        elif tool_name == "delegate_parallel":
            return await self._delegate_parallel(params)
        elif tool_name == "create_agent":
            return await self._create(params)
        return f"❌ Unknown agent tool: {tool_name}"

    # ------------------------------------------------------------------
    # delegate_to_agent
    # ------------------------------------------------------------------

    async def _delegate(self, params: dict[str, Any]) -> str:
        agent_id = (params.get("agent_id") or "").strip()
        message = (params.get("message") or "").strip()
        reason = (params.get("reason") or "").strip()

        if not agent_id:
            return "❌ agent_id is required"
        if not message:
            return "❌ message is required"

        orchestrator = self._get_orchestrator()
        if orchestrator is None:
            return "❌ Orchestrator not available — multi-agent mode may not be fully initialised"

        session = getattr(self.agent, "_current_session", None)
        if session is None:
            return "❌ No active session — delegation requires a session context"

        current_agent = getattr(
            getattr(session, "context", None), "agent_profile_id", "default"
        ) or "default"

        logger.info(
            f"[AgentToolHandler] Delegation: {current_agent} -> {agent_id} | reason={reason}"
        )

        try:
            result = await orchestrator.delegate(
                session=session,
                from_agent=current_agent,
                to_agent=agent_id,
                message=message,
                reason=reason,
            )
            return str(result)
        except Exception as e:
            logger.error(f"[AgentToolHandler] Delegation failed: {e}", exc_info=True)
            return f"❌ Delegation to {agent_id} failed: {e}"

    # ------------------------------------------------------------------
    # delegate_parallel
    # ------------------------------------------------------------------

    async def _delegate_parallel(self, params: dict[str, Any]) -> str:
        import asyncio

        tasks_param = params.get("tasks")
        if not tasks_param or not isinstance(tasks_param, list):
            return "❌ tasks is required and must be a list"
        if len(tasks_param) < 2:
            return "❌ delegate_parallel requires at least 2 tasks (use delegate_to_agent for single)"
        if len(tasks_param) > 5:
            return "❌ Maximum 5 parallel delegations allowed"

        orchestrator = self._get_orchestrator()
        if orchestrator is None:
            return "❌ Orchestrator not available"

        session = getattr(self.agent, "_current_session", None)
        if session is None:
            return "❌ No active session"

        current_agent = getattr(
            getattr(session, "context", None), "agent_profile_id", "default"
        ) or "default"

        async def _run_one(task: dict) -> tuple[str, str]:
            agent_id = (task.get("agent_id") or "").strip()
            message = (task.get("message") or "").strip()
            reason = (task.get("reason") or "").strip()
            if not agent_id or not message:
                return agent_id or "?", "❌ agent_id and message are required"
            logger.info(
                f"[AgentToolHandler] Parallel delegation: {current_agent} -> {agent_id} | reason={reason}"
            )
            try:
                result = await orchestrator.delegate(
                    session=session,
                    from_agent=current_agent,
                    to_agent=agent_id,
                    message=message,
                    reason=reason,
                )
                return agent_id, str(result)
            except BaseException as e:
                logger.error(f"[AgentToolHandler] Parallel delegation to {agent_id} failed: {e}")
                return agent_id, f"❌ Failed: {e}"

        coros = [_run_one(t) for t in tasks_param]
        raw_results = await asyncio.gather(*coros, return_exceptions=True)

        parts = []
        for i, res in enumerate(raw_results):
            if isinstance(res, BaseException):
                aid = (tasks_param[i].get("agent_id") or "?").strip()
                parts.append(f"## Agent: {aid}\n❌ Failed: {res}")
            else:
                agent_id, result = res
                parts.append(f"## Agent: {agent_id}\n{result}")
        return "\n\n---\n\n".join(parts)

    # ------------------------------------------------------------------
    # create_agent
    # ------------------------------------------------------------------

    async def _create(self, params: dict[str, Any]) -> str:
        name = (params.get("name") or "").strip()
        description = (params.get("description") or "").strip()
        skills = params.get("skills") or []
        custom_prompt = (params.get("custom_prompt") or "").strip()

        if not name:
            return "❌ name is required"
        if not description:
            return "❌ description is required"

        session = getattr(self.agent, "_current_session", None)
        if session is None:
            return "❌ No active session — agent creation requires a session context"

        # Enforce per-session limit
        ctx = getattr(session, "context", None)
        history: list[dict] = getattr(ctx, "agent_switch_history", []) if ctx else []
        created_count = sum(1 for h in history if h.get("type") == "dynamic_create")
        max_allowed = DYNAMIC_AGENT_POLICIES["max_agents_per_session"]
        if created_count >= max_allowed:
            return f"❌ Maximum dynamic agents per session reached ({max_allowed})"

        from ...agents.profile import (
            AgentProfile,
            AgentType,
            ProfileStore,
            SkillsMode,
        )
        from ...config import settings

        session_key = getattr(session, "session_key", "") or getattr(session, "id", "")
        raw_key = str(session_key)[:12] if session_key else "anon"
        short_key = re.sub(r"[^a-z0-9_]", "", raw_key.lower()) or "anon"
        short_key = short_key[:8]
        raw = name.lower().replace(" ", "_")
        safe_name = re.sub(r"[^a-z0-9_]", "", raw)
        if not safe_name:
            safe_name = hashlib.md5(name.encode("utf-8")).hexdigest()[:8]
        profile_id = f"dynamic_{safe_name}_{short_key}"

        profile = AgentProfile(
            id=profile_id,
            name=name,
            description=description,
            type=AgentType.DYNAMIC,
            skills=skills,
            skills_mode=SkillsMode.INCLUSIVE if skills else SkillsMode.ALL,
            custom_prompt=custom_prompt,
            icon="🤖",
            color="#6b7280",
            created_by="ai",
        )

        store = ProfileStore(settings.data_dir / "agents")
        store.save(profile)

        # Record in session history (if available)
        if ctx is not None and hasattr(ctx, "agent_switch_history"):
            ctx.agent_switch_history.append({
                "type": "dynamic_create",
                "agent_id": profile_id,
                "name": name,
                "at": datetime.now(timezone.utc).isoformat(),
            })

        logger.info(f"[AgentToolHandler] Created dynamic agent: {profile_id}")
        return f"✅ Agent created: {profile_id} ({name})"

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _get_orchestrator(self):
        """Try to find the orchestrator from the main module globals."""
        try:
            from ...main import _orchestrator
            return _orchestrator
        except (ImportError, AttributeError):
            return None


def create_handler(agent: Agent):
    """Factory function following the project convention."""
    handler = AgentToolHandler(agent)
    return handler.handle
