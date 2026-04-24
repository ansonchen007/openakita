"""RSS / Atom / JSON-Feed fetcher.

Wraps ``feedparser`` as an optional dependency (only used by sources
that opt into the RSS flow) and exposes :class:`GenericRSSFetcher`
which reads its feed list from ``config['rss_generic.feeds']`` (one
URL per line).

The helper :func:`parse_feed` is exported so RSS-first sources
(wallstreetcn / xueqiu / nbs / sec_edgar) can delegate to it without
re-implementing the feedparser dance.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from finpulse_fetchers._http import fetch_text, make_client
from finpulse_fetchers.base import BaseFetcher, NormalizedItem

try:  # pragma: no cover — feedparser is optional
    import feedparser  # type: ignore

    FEEDPARSER_AVAILABLE = True
except ImportError:
    feedparser = None  # type: ignore
    FEEDPARSER_AVAILABLE = False

logger = logging.getLogger(__name__)


def _to_iso(struct_time: Any) -> str | None:
    if struct_time is None:
        return None
    try:
        dt = datetime(*struct_time[:6], tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:  # noqa: BLE001
        return None


def parse_feed(source_id: str, body: str) -> list[NormalizedItem]:
    """Parse an RSS/Atom ``body`` into canonical items.

    Returns ``[]`` on parse failure — the caller is expected to examine
    ``feedparser`` bozo state if it needs finer-grained narration.
    """
    if not FEEDPARSER_AVAILABLE:
        raise ImportError(
            "feedparser is required for RSS sources; install via `pip install feedparser`"
        )
    parsed = feedparser.parse(body)
    items: list[NormalizedItem] = []
    for entry in parsed.entries or []:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        summary = (entry.get("summary") or entry.get("description") or "").strip() or None
        published_iso = _to_iso(entry.get("published_parsed") or entry.get("updated_parsed"))
        items.append(
            NormalizedItem(
                source_id=source_id,
                title=title,
                url=link,
                summary=summary,
                published_at=published_iso,
                extra={
                    "id": entry.get("id"),
                    "author": entry.get("author"),
                    "tags": [t.get("term") for t in entry.get("tags", []) if t.get("term")],
                },
            )
        )
    return items


class GenericRSSFetcher(BaseFetcher):
    """Configurable RSS aggregator — reads feed URLs from config.

    ``config['rss_generic.feeds']`` is a newline-separated list of feed
    URLs. Each URL emits items under ``source_id='rss_generic'``; the
    originating feed host is preserved in ``extra['feed_host']`` so the
    UI can distinguish sources within the same aggregator.
    """

    source_id = "rss_generic"

    async def fetch(self, **_: Any) -> list[NormalizedItem]:
        feeds_cfg = self._config.get("rss_generic.feeds", "")
        feeds = [ln.strip() for ln in feeds_cfg.splitlines() if ln.strip()]
        if not feeds:
            return []
        out: list[NormalizedItem] = []
        async with make_client(timeout=self._timeout_sec) as client:
            for feed_url in feeds[:32]:  # hard cap so a huge paste cannot DoS the run
                try:
                    body = await fetch_text(client, feed_url)
                except Exception as exc:  # noqa: BLE001 — per-feed isolation
                    logger.warning("rss feed failed %s: %s", feed_url, exc)
                    continue
                try:
                    items = parse_feed(self.source_id, body)
                except ImportError:
                    raise  # surface dependency error to the pipeline
                except Exception as exc:  # noqa: BLE001
                    logger.warning("rss parse failed %s: %s", feed_url, exc)
                    continue
                for item in items:
                    item.extra.setdefault("feed_url", feed_url)
                out.extend(items)
        return out


async def fetch_one_feed(
    source_id: str, feed_url: str, *, timeout: float = 15.0
) -> list[NormalizedItem]:
    """Fetch + parse a single feed. Used by RSS-first fetchers that map
    to exactly one feed URL (wallstreetcn / xueqiu / nbs / sec_edgar).
    """
    async with make_client(timeout=timeout) as client:
        body = await fetch_text(client, feed_url)
    return parse_feed(source_id, body)


__all__ = [
    "FEEDPARSER_AVAILABLE",
    "GenericRSSFetcher",
    "fetch_one_feed",
    "parse_feed",
]
