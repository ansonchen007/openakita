"""CLS Telegram (财联社电报) — NewsNow-first with API fallback.

Primary path reads through the NewsNow aggregator (``?id=cls-hot``) —
TrendRadar does the same and has proved more resilient to cls.com
API shape changes. The legacy direct path (``nodeapi/updateTelegraphList``)
is retained as a graceful-degradation branch for firewalled users.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from finpulse_fetchers._http import fetch_json, make_client
from finpulse_fetchers.base import BaseFetcher, NormalizedItem
from finpulse_fetchers.newsnow_base import fetch_from_newsnow


_CLS_ENDPOINT = (
    "https://www.cls.cn/nodeapi/updateTelegraphList"
    "?app=CailianpressWeb&category=&os=web&rn=20&subscribedColumnIds=&sv=7.7.5"
)

logger = logging.getLogger(__name__)


class CLSFetcher(BaseFetcher):
    source_id = "cls"
    NEWSNOW_PLATFORM_ID = "cls-hot"

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
            logger.info("cls via newsnow failed, will try direct: %s", exc)
            primary = []
        if primary:
            self._last_via = "newsnow"
            return primary

        if (self._config.get("source.cls.fallback_direct") or "true").lower() == "false":
            self._last_via = "none"
            return []

        direct = await self._fetch_direct()
        self._last_via = "direct" if direct else "none"
        return direct

    async def _fetch_direct(self) -> list[NormalizedItem]:
        ts = int(time.time())
        url = f"{_CLS_ENDPOINT}&lastTime={ts}"
        async with make_client(timeout=self._timeout_sec) as client:
            data = await fetch_json(client, url)
        return self._parse(data)

    @staticmethod
    def _parse(payload: Any) -> list[NormalizedItem]:
        items: list[NormalizedItem] = []
        roll = []
        if isinstance(payload, dict):
            data = payload.get("data") or {}
            roll = data.get("roll_data") or data.get("rollList") or []
        for row in roll:
            if not isinstance(row, dict):
                continue
            title = row.get("title") or row.get("brief") or ""
            content = row.get("brief") or row.get("content") or ""
            url = row.get("shareurl") or row.get("share_url") or ""
            if not title:
                title = (content or "").split("\n")[0][:80]
            if not title or not url:
                continue
            published = row.get("ctime") or row.get("time") or None
            pub_iso: str | None = None
            if isinstance(published, int):
                pub_iso = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(published)
                )
            elif isinstance(published, str) and published:
                pub_iso = published
            items.append(
                NormalizedItem(
                    source_id="cls",
                    title=title.strip(),
                    url=url.strip(),
                    summary=content.strip() or None,
                    published_at=pub_iso,
                    extra={
                        "level": row.get("level"),
                        "type": row.get("type"),
                        "reading_num": row.get("reading_num"),
                    },
                )
            )
        return items


__all__ = ["CLSFetcher"]
