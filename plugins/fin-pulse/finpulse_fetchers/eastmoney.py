"""EastMoney (东方财富) — public news API.

EastMoney exposes a JSONP-style news list. We hit the ``newsapi`` JSON
variant which returns an array under ``LsjzList`` (or ``list``
depending on the parameter set).
"""

from __future__ import annotations

from typing import Any

from finpulse_fetchers._http import fetch_json, make_client
from finpulse_fetchers.base import BaseFetcher, NormalizedItem


_ENDPOINT = (
    "https://np-listapi.eastmoney.com/comm/wap/getListInfo"
    "?cb=&client=web&mTypeAndCode=&type=1&column=&pageSize=30&pageIndex=1&_="
)


class EastmoneyFetcher(BaseFetcher):
    source_id = "eastmoney"

    async def fetch(self, **_: Any) -> list[NormalizedItem]:
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
