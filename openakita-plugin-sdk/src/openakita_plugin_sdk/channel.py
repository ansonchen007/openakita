"""Channel adapter abstractions for IM channel plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ChannelAdapter(ABC):
    """Abstract base for IM channel adapters.

    Mirrors ``openakita.channels.base.ChannelAdapter`` so plugin authors
    can subclass without installing the full runtime.
    """

    @abstractmethod
    async def start(self) -> None:
        """Start the adapter (connect, poll, etc.)."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the adapter and release resources."""

    @abstractmethod
    async def send_message(self, message: Any) -> Any:
        """Send an outgoing message."""

    @abstractmethod
    async def send_text(self, chat_id: str, text: str, **kwargs: Any) -> Any:
        """Send a plain text message."""


class ChannelPluginMixin:
    """Convenience mixin for channel plugins.

    Provides a standard ``register`` helper that calls
    ``api.register_channel(type_name, factory)`` during ``on_load``.

    Usage::

        class Plugin(PluginBase, ChannelPluginMixin):
            channel_type = "whatsapp"

            def on_load(self, api):
                self.register(api, self.create_adapter)

            def create_adapter(self, creds, *, channel_name, bot_id, agent_profile_id):
                return WhatsAppAdapter(...)
    """

    channel_type: str = ""

    def register(self, api: Any, factory: Any) -> None:
        if not self.channel_type:
            raise ValueError("Set channel_type before calling register()")
        api.register_channel(self.channel_type, factory)
