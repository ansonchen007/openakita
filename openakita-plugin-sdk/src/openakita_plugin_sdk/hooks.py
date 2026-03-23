"""Hook constants, callback signatures, and documentation.

Each hook is dispatched at a specific point in the OpenAkita lifecycle.
Callbacks can be sync or async; sync callbacks are automatically wrapped
in a thread to avoid blocking the event loop.

All callbacks receive keyword arguments only — never positional.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

HOOK_NAMES = frozenset({
    "on_init",
    "on_shutdown",
    "on_message_received",
    "on_message_sending",
    "on_retrieve",
    "on_tool_result",
    "on_session_start",
    "on_session_end",
    "on_prompt_build",
    "on_schedule",
})

HookCallback = Callable[..., Coroutine[Any, Any, Any]]

HOOK_SIGNATURES: dict[str, dict[str, Any]] = {
    "on_init": {
        "description": "Fired once after all plugins are loaded and the agent is ready.",
        "kwargs": {},
        "permission": "hooks.basic",
        "return": "None (ignored)",
        "example": 'async def on_init(**kwargs): api.log("Ready!")',
    },
    "on_shutdown": {
        "description": "Fired when the agent is shutting down. Clean up resources here.",
        "kwargs": {},
        "permission": "hooks.basic",
        "return": "None (ignored)",
        "example": 'async def on_shutdown(**kwargs): await cleanup()',
    },
    "on_schedule": {
        "description": "Fired before each scheduled task executes.",
        "kwargs": {
            "task_id": "str — ID of the scheduled task",
        },
        "permission": "hooks.basic",
        "return": "None (ignored)",
    },
    "on_message_received": {
        "description": "Fired when a new message arrives from any IM channel, before processing.",
        "kwargs": {
            "channel": "str — channel type (telegram, feishu, etc.)",
            "chat_id": "str — conversation ID",
            "user_id": "str — sender user ID",
            "text": "str — message text content",
            "message": "UnifiedMessage — full message object",
        },
        "permission": "hooks.message",
        "return": "None (ignored)",
    },
    "on_message_sending": {
        "description": "Fired just before a response is sent back to the user.",
        "kwargs": {
            "channel": "str — channel type",
            "chat_id": "str — conversation ID",
            "text": "str — response text about to be sent",
        },
        "permission": "hooks.message",
        "return": "None (ignored)",
    },
    "on_session_start": {
        "description": "Fired when a new conversation session is created.",
        "kwargs": {
            "session_id": "str — unique session identifier",
        },
        "permission": "hooks.message",
        "return": "None (ignored)",
    },
    "on_session_end": {
        "description": "Fired when a conversation session is closed.",
        "kwargs": {
            "session_id": "str — unique session identifier",
        },
        "permission": "hooks.message",
        "return": "None (ignored)",
    },
    "on_retrieve": {
        "description": "Fired after memory retrieval, before results are used. Can observe or augment candidates.",
        "kwargs": {
            "query": "str — the retrieval query",
            "candidates": "list[dict] — retrieved memory candidates (mutable list)",
        },
        "permission": "hooks.retrieve",
        "return": "None (ignored; mutate candidates list in-place to augment)",
    },
    "on_tool_result": {
        "description": "Fired after a tool call completes, with the result.",
        "kwargs": {
            "tool_name": "str — name of the tool that was called",
            "arguments": "dict — arguments passed to the tool",
            "result": "str — the tool's return value",
        },
        "permission": "hooks.retrieve",
        "return": "None (ignored)",
    },
    "on_prompt_build": {
        "description": "Fired after the system prompt is assembled. Return text to append to the prompt.",
        "kwargs": {
            "prompt": "str — the current system prompt text",
        },
        "permission": "hooks.retrieve",
        "return": "str | None — extra text to append to the system prompt",
        "example": (
            'async def on_prompt_build(**kwargs):\n'
            '    return "\\n\\nAdditional context from my plugin..."'
        ),
    },
}
