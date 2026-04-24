"""XueQiu (雪球) — RSS-first for the hot-post feed."""

from __future__ import annotations

from typing import Any

from finpulse_fetchers.base import BaseFetcher, NormalizedItem
from finpulse_fetchers.rss import fetch_one_feed


_RSS_URL = "https://xueqiu.com/hots/topic/rss"


class XueqiuFetcher(BaseFetcher):
    source_id = "xueqiu"

    async def fetch(self, **_: Any) -> list[NormalizedItem]:
        return await fetch_one_feed(
            self.source_id, _RSS_URL, timeout=self._timeout_sec
        )


__all__ = ["XueqiuFetcher"]
