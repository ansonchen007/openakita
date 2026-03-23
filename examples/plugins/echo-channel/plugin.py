"""echo-channel: registers a stub IM adapter named echo."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from openakita.channels.base import ChannelAdapter
from openakita.channels.types import MediaFile, OutgoingMessage
from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)


class EchoAdapter(ChannelAdapter):
    """Stub adapter: logs send/download/upload; no real transport."""

    capabilities = {
        **ChannelAdapter.capabilities,
        "streaming": False,
        "send_image": False,
        "send_file": False,
    }

    def __init__(
        self,
        creds: dict,
        *,
        channel_name: str,
        bot_id: str,
        agent_profile_id: str,
    ) -> None:
        super().__init__(channel_name=channel_name, bot_id=bot_id, agent_profile_id=agent_profile_id)
        self._creds = creds

    async def start(self) -> None:
        self._running = True
        logger.info("EchoAdapter started (stub) creds_keys=%s", list(self._creds.keys()))

    async def stop(self) -> None:
        self._running = False
        logger.info("EchoAdapter stopped (stub)")

    async def send_message(self, message: OutgoingMessage) -> str:
        text = message.content.text if message.content else ""
        logger.info("EchoAdapter send_message chat_id=%s text=%s", message.chat_id, text[:200])
        return "echo-stub-msg-id"

    async def download_media(self, media: MediaFile) -> Path:
        logger.info("EchoAdapter download_media id=%s filename=%s", media.id, media.filename)
        return Path(tempfile.gettempdir()) / f"echo-dl-{media.id}.bin"

    async def upload_media(self, path: Path, mime_type: str) -> MediaFile:
        logger.info("EchoAdapter upload_media path=%s mime=%s", path, mime_type)
        return MediaFile.create(path.name, mime_type)


def _echo_factory(
    creds: dict,
    *,
    channel_name: str,
    bot_id: str,
    agent_profile_id: str,
) -> EchoAdapter:
    return EchoAdapter(
        creds,
        channel_name=channel_name,
        bot_id=bot_id,
        agent_profile_id=agent_profile_id,
    )


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        api.register_channel("echo", _echo_factory)

    def on_unload(self) -> None:
        pass
