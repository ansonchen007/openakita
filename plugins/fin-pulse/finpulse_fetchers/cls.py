"""CLS Telegram (财联社电报) — API-driven source.

CLS publishes a JSON endpoint with short-form news flashes (电报). We
hit the public ``depth`` endpoint and normalise the payload. The API
shape has evolved in the past; the parser is defensive — unknown keys
land in ``extra`` rather than raising.
"""

from __future__ import annotations

import time
from typing import Any

from finpulse_fetchers._http import fetch_json, make_client
from finpulse_fetchers.base import BaseFetcher, NormalizedItem


_CLS_ENDPOINT = (
    "https://www.cls.cn/nodeapi/updateTelegraphList"
    "?app=CailianpressWeb&category=&os=web&rn=20&subscribedColumnIds=&sv=7.7.5"
)


class CLSFetcher(BaseFetcher):
    source_id = "cls"

    async def fetch(self, **_: Any) -> list[NormalizedItem]:
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
