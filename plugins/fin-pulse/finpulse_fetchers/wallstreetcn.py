"""WallStreet CN (华尔街见闻) — RSS-first with HTML fallback.

The primary feed lives at https://wallstreetcn.com/feed; if the feed
comes back empty (the upstream sometimes truncates on burst access)
the fetcher falls back to the homepage JSON API rendered by Next.js.
"""

from __future__ import annotations

import json
import re
from typing import Any

from finpulse_fetchers._http import fetch_text, make_client
from finpulse_fetchers.base import BaseFetcher, NormalizedItem
from finpulse_fetchers.rss import fetch_one_feed, parse_feed

_RSS_URL = "https://wallstreetcn.com/feed"
_FALLBACK_HOME = "https://wallstreetcn.com/"

# Next.js ships initial data inside a JSON script tag; the fallback parser
# extracts the ``items: [...]`` array without pulling a DOM parser.
_NEXT_DATA_RE = re.compile(
    r'<script\s+id="__NEXT_DATA__"[^>]*>(?P<json>.+?)</script>', re.DOTALL
)


class WallStreetCNFetcher(BaseFetcher):
    source_id = "wallstreetcn"

    async def fetch(self, **_: Any) -> list[NormalizedItem]:
        # Try RSS first; treat any soft failure (parser missing, feed empty,
        # upstream glitch) as a signal to fall back to the HTML homepage.
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
