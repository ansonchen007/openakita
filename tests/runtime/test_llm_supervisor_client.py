"""Tests for the gateway-backed supervisor LLM endpoint lock."""

from __future__ import annotations

import pytest

from openakita.llm.types import ConfigurationError
from openakita.runtime.llm_supervisor_client import GatewaySupervisorLLMClient


class _MissingEndpointGateway:
    def __init__(self) -> None:
        self.switch_calls: list[dict[str, object]] = []
        self.chat_calls = 0

    def switch_model(
        self,
        endpoint_name: str,
        *,
        conversation_id: str | None = None,
        policy: str = "prefer",
        reason: str = "",
    ) -> tuple[bool, str]:
        self.switch_calls.append(
            {
                "endpoint_name": endpoint_name,
                "conversation_id": conversation_id,
                "policy": policy,
                "reason": reason,
            }
        )
        return False, f"endpoint {endpoint_name!r} does not exist"

    async def chat(self, **_kwargs):
        self.chat_calls += 1
        raise AssertionError("default routing must not run")


async def test_missing_required_supervisor_endpoint_does_not_use_default_model() -> None:
    gateway = _MissingEndpointGateway()
    client = GatewaySupervisorLLMClient(
        gateway,  # type: ignore[arg-type]
        endpoint="supervisor-json-model",
        conversation_id="orgsup-test",
    )

    with pytest.raises(ConfigurationError, match="Supervisor 端点.*不可用"):
        await client.complete(
            role="progress_ledger",
            system="system",
            user="user",
        )

    assert gateway.chat_calls == 0
    assert gateway.switch_calls == [
        {
            "endpoint_name": "supervisor-json-model",
            "conversation_id": "orgsup-test",
            "policy": "require",
            "reason": "rc5 org orchestration brain (no-thinking)",
        }
    ]
