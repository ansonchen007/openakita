"""EastMoney (东方财富) — NewsNow-first with API fallback.

Primary path reads through the NewsNow aggregator (``?id=eastmoney``)
which TrendRadar has proved reliable across upstream shape changes.
The legacy ``np-listapi.eastmoney.com`` JSON/JSONP endpoint stays as
a graceful-degradation branch for firewalled installs.
"""

from __future__ import annotations

import logging
from typing import Any

from finpulse_fetchers._http import fetch_json, make_client
from finpulse_fetchers.base import BaseFetcher, NormalizedItem
from finpulse_fetchers.newsnow_base import fetch_from_newsnow


_ENDPOINT = (
    "https://np-listapi.eastmoney.com/comm/wap/getListInfo"
    "?cb=&client=web&mTypeAndCode=&type=1&column=&pageSize=30&pageIndex=1&_="
)

logger = logging.getLogger(__name__)


class EastmoneyFetcher(BaseFetcher):
    source_id = "eastmoney"
    NEWSNOW_PLATFORM_ID = "eastmoney"

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
            logger.info("eastmoney via newsnow failed, will try direct: %s", exc)
            primary = []
        if primary:
            self._last_via = "newsnow"
            return primary

        if (self._config.get("source.eastmoney.fallback_direct") or "true").lower() == "false":
            self._last_via = "none"
            return []

        direct = await self._fetch_direct()
        self._last_via = "direct" if direct else "none"
        return direct

    async def _fetch_direct(self) -> list[NormalizedItem]:
        async with make_client(timeout=self._timeout_sec) as client:
            try:
                data = await fetch_json(client, _ENDPOINT)
            except Exception:
                # JSONP fallback: strip callback wrapper if ever returned.
                from finpulse_fetchers._http import fetch_text

                txt = await fetch_text(client, _ENDPOINT)
                data = self._unwrap_jsonp(txt)
        return self._parse(data)

    @staticmethod
    def _unwrap_jsonp(txt: str) -> Any:
        import json
        import re

        match = re.search(r"\((\{.+\})\)\s*;?\s*$", txt, re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group(1))
        except ValueError:
            return {}

    @staticmethod
    def _parse(payload: Any) -> list[NormalizedItem]:
        if not isinstance(payload, dict):
            return []
        data = payload.get("data") or {}
        rows = data.get("list") or data.get("LsjzList") or []
        out: list[NormalizedItem] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = (row.get("Art_Title") or row.get("title") or "").strip()
            url = (row.get("Art_URL") or row.get("url") or "").strip()
            if not title or not url:
                continue
            published = row.get("Art_ShowTime") or row.get("showTime") or None
            out.append(
                NormalizedItem(
                    source_id="eastmoney",
                    title=title,
                    url=url,
                    published_at=published,
                    summary=(row.get("Art_Summary") or row.get("summary") or None),
                    extra={"media": row.get("Art_MediaName") or row.get("media")},
                )
            )
        return out


__all__ = ["EastmoneyFetcher"]
