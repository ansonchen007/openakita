"""NewsNow aggregator adapter.

NewsNow is an optional enhancer — the Settings wizard (§11.7) offers
``off`` / ``public`` / ``self_host`` modes, each pointing
``newsnow.api_url`` at the right service. This fetcher fixes the bug
filed against TrendRadar's DataFetcher where the ``api_url`` was
hard-coded to the public demo service; here it reads straight from
config so self-hosted users can point at ``http://127.0.0.1:4444/api/s``.
"""

from __future__ import annotations

import logging
from typing import Any

from finpulse_fetchers._http import fetch_json, jittered_sleep, make_client
from finpulse_fetchers.base import BaseFetcher, NormalizedItem

logger = logging.getLogger(__name__)


_ALLOWED_STATUS = {"success", "cache"}


class NewsNowFetcher(BaseFetcher):
    source_id = "newsnow"

    async def fetch(self, **_: Any) -> list[NormalizedItem]:
        mode = self._config.get("newsnow.mode", "off")
        if mode not in {"public", "self_host"}:
            return []
        api_url = (self._config.get("newsnow.api_url") or "").strip()
        if not api_url:
            return []
        channels_cfg = self._config.get("newsnow.channels", "wallstreetcn-hot,cls-hot")
        channels = [c.strip() for c in channels_cfg.split(",") if c.strip()]
        out: list[NormalizedItem] = []
        async with make_client(timeout=self._timeout_sec) as client:
            for channel in channels:
                if out.count(channel) > 200:  # defensive
                    break
                try:
                    data = await fetch_json(client, f"{api_url}?id={channel}")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("newsnow channel %s failed: %s", channel, exc)
                    continue
                items = self._parse(channel, data)
                out.extend(items)
                await jittered_sleep(100, 100)
        return out

    @staticmethod
    def _parse(channel: str, payload: Any) -> list[NormalizedItem]:
        if not isinstance(payload, dict):
            return []
        status = payload.get("status")
        if status not in _ALLOWED_STATUS:
            raise ValueError(f"unexpected newsnow status: {status!r}")
        rows = payload.get("items") or []
        out: list[NormalizedItem] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = (row.get("title") or "").strip()
            url = (row.get("url") or row.get("mobileUrl") or "").strip()
            if not title or not url:
                continue
            out.append(
                NormalizedItem(
                    source_id=f"newsnow:{channel}",
                    title=title,
                    url=url,
                    summary=row.get("desc") or row.get("summary"),
                    extra={
                        "rank": row.get("rank"),
                        "mobileUrl": row.get("mobileUrl"),
                        "channel": channel,
                        "extra_raw": {
                            k: row[k]
                            for k in row
                            if k not in {"title", "url", "mobileUrl", "desc", "summary"}
                        },
                    },
                )
            )
        return out


__all__ = ["NewsNowFetcher"]
