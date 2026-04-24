"""XueQiu (雪球) — NewsNow-first with RSS fallback.

Primary path reads through the NewsNow aggregator
(``?id=xueqiu-hotstock``). The legacy RSS feed
(``https://xueqiu.com/hots/topic/rss``) stays as a fallback for
firewalled installs where NewsNow is unreachable.
"""

from __future__ import annotations

import logging
from typing import Any

from finpulse_fetchers.base import BaseFetcher, NormalizedItem
from finpulse_fetchers.newsnow_base import fetch_from_newsnow
from finpulse_fetchers.rss import fetch_one_feed


_RSS_URL = "https://xueqiu.com/hots/topic/rss"

logger = logging.getLogger(__name__)


class XueqiuFetcher(BaseFetcher):
    source_id = "xueqiu"
    NEWSNOW_PLATFORM_ID = "xueqiu-hotstock"

    def __init__(
        self, *, config: dict[str, str] | None = None, timeout_sec: float = 15.0
    ) -> None:
        super().__init__(config=config, timeout_sec=timeout_sec)
        self._last_via: str = "none"

    async def fetch(self, **_: Any) -> list[NormalizedItem]:
        try:
            primary = await fetch_from_newsnow(
                platform_id=self.NEWSNOW_PLATFORM_ID,
                source_id=self.source_id,
                config=self._config,
                timeout_sec=self._timeout_sec,
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("xueqiu via newsnow failed, will try direct: %s", exc)
            primary = []
        if primary:
            self._last_via = "newsnow"
            return primary

        if (self._config.get("source.xueqiu.fallback_direct") or "true").lower() == "false":
            self._last_via = "none"
            return []

        direct = await self._fetch_direct()
        self._last_via = "direct" if direct else "none"
        return direct

    async def _fetch_direct(self) -> list[NormalizedItem]:
        return await fetch_one_feed(
            self.source_id, _RSS_URL, timeout=self._timeout_sec
        )


__all__ = ["XueqiuFetcher"]
