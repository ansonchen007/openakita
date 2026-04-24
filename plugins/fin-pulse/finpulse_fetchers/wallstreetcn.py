"""WallStreet CN (华尔街见闻) — NewsNow-first with RSS / HTML fallback.

Primary path routes through the community-run NewsNow aggregator
(``?id=wallstreetcn-hot``) — see :mod:`finpulse_fetchers.newsnow_base`.
When the aggregator is unavailable (``newsnow.mode=off``, cooldown,
network error, or an empty envelope) we fall back to the original
direct-scraping path: RSS at ``https://wallstreetcn.com/feed`` with an
HTML homepage fallback parsing the Next.js ``__NEXT_DATA__`` blob.

The fallback layer stays around so (a) users in firewalled environments
where NewsNow is unreachable can disable the aggregator and still get
rows, and (b) the existing regression fixtures keep biting.

Reference: ``D:/plugin-research-refs/repos/TrendRadar/trendradar/crawler/fetcher.py``
(L20-115) — same envelope, different transport layer.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from finpulse_fetchers._http import fetch_text, make_client
from finpulse_fetchers.base import BaseFetcher, NormalizedItem
from finpulse_fetchers.newsnow_base import fetch_from_newsnow
from finpulse_fetchers.rss import fetch_one_feed, parse_feed

_RSS_URL = "https://wallstreetcn.com/feed"
_FALLBACK_HOME = "https://wallstreetcn.com/"

# Next.js ships initial data inside a JSON script tag; the fallback parser
# extracts the ``items: [...]`` array without pulling a DOM parser.
_NEXT_DATA_RE = re.compile(
    r'<script\s+id="__NEXT_DATA__"[^>]*>(?P<json>.+?)</script>', re.DOTALL
)

logger = logging.getLogger(__name__)


class WallStreetCNFetcher(BaseFetcher):
    source_id = "wallstreetcn"
    # TrendRadar platform id inside the NewsNow aggregator. Keep the
    # attribute exposed so tests can monkey-patch or inspect it.
    NEWSNOW_PLATFORM_ID = "wallstreetcn-hot"

    def __init__(
        self, *, config: dict[str, str] | None = None, timeout_sec: float = 15.0
    ) -> None:
        super().__init__(config=config, timeout_sec=timeout_sec)
        # The pipeline reads ``_last_via`` after ``fetch()`` resolves so
        # the Today tab can surface which code path served the rows.
        # Values: ``"newsnow"`` / ``"direct"`` / ``"none"``.
        self._last_via: str = "none"

    async def fetch(self, **_: Any) -> list[NormalizedItem]:
        # 1. Try NewsNow aggregator when enabled. Any error / empty
        #    response logs + falls through to the direct path.
        try:
            primary = await fetch_from_newsnow(
                platform_id=self.NEWSNOW_PLATFORM_ID,
                source_id=self.source_id,
                config=self._config,
                timeout_sec=self._timeout_sec,
            )
        except Exception as exc:  # noqa: BLE001 — fallback is intentional
            logger.info(
                "wallstreetcn via newsnow failed, will try direct: %s", exc
            )
            primary = []
        if primary:
            self._last_via = "newsnow"
            return primary

        # 2. Respect a Settings opt-out for the direct path (advanced;
        #    empty or ``"true"`` keeps the fallback on).
        if (self._config.get("source.wallstreetcn.fallback_direct") or "true").lower() == "false":
            self._last_via = "none"
            return []

        direct = await self._fetch_direct()
        self._last_via = "direct" if direct else "none"
        return direct

    async def _fetch_direct(self) -> list[NormalizedItem]:
        """Legacy RSS-first path kept as the graceful-degradation branch."""
        items: list[NormalizedItem] = []
        try:
            items = await fetch_one_feed(
                self.source_id, _RSS_URL, timeout=self._timeout_sec
            )
        except Exception:  # noqa: BLE001 — fallback is the whole point.
            items = []
        if items:
            return items
        async with make_client(timeout=self._timeout_sec) as client:
            body = await fetch_text(client, _FALLBACK_HOME)
        return self._parse_next_data(body)

    @classmethod
    def _parse_next_data(cls, html: str) -> list[NormalizedItem]:
        match = _NEXT_DATA_RE.search(html)
        if not match:
            return []
        try:
            data = json.loads(match.group("json"))
        except ValueError:
            return []
        items: list[NormalizedItem] = []

        def _walk(node: Any) -> None:
            if isinstance(node, dict):
                title = node.get("title")
                url = node.get("url") or node.get("uri")
                if isinstance(title, str) and isinstance(url, str) and title and url.startswith("http"):
                    items.append(
                        NormalizedItem(
                            source_id="wallstreetcn",
                            title=title.strip(),
                            url=url.strip(),
                            summary=node.get("content_short"),
                            extra={"raw_keys": sorted(node.keys())[:6]},
                        )
                    )
                for v in node.values():
                    _walk(v)
            elif isinstance(node, list):
                for x in node:
                    _walk(x)

        _walk(data)
        # Defensive dedupe — the NEXT_DATA tree repeats featured articles.
        seen: set[str] = set()
        deduped: list[NormalizedItem] = []
        for item in items:
            key = item.url_hash()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped[:50]


__all__ = ["WallStreetCNFetcher"]


def _debug_parse(body: str) -> list[NormalizedItem]:  # pragma: no cover
    """Dev helper for manual iteration; the main path goes through RSS."""
    return parse_feed("wallstreetcn", body)
