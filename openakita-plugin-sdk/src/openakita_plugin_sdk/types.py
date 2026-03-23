"""Shared type definitions used across plugin interfaces.

These mirror the runtime types so plugin authors can use them for
type hints without importing the full OpenAkita package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class UnifiedMessage:
    """Incoming message from any IM channel.

    Mirrors ``openakita.channels.models.UnifiedMessage`` for type hints.
    """

    channel: str = ""
    chat_id: str = ""
    user_id: str = ""
    text: str = ""
    thread_id: str | None = None
    channel_message_id: str | None = None
    channel_user_id: str | None = None
    display_name: str | None = None
    chat_type: str = "private"
    metadata: dict[str, Any] = field(default_factory=dict)
    attachments: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class OutgoingMessage:
    """Outgoing message to an IM channel.

    Mirrors ``openakita.channels.models.OutgoingMessage`` for type hints.
    """

    chat_id: str = ""
    text: str = ""
    reply_to: str | None = None
    thread_id: str | None = None
    parse_mode: str = "markdown"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCall:
    """A single tool call from the LLM.

    Mirrors the dict shape used by ``ReasoningEngine``.
    """

    name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""
