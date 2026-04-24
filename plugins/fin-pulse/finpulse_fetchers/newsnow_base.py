"""Shared NewsNow aggregator helper — single TrendRadar-style call.

TrendRadar's ``crawler/fetcher.py::DataFetcher`` survives every upstream
HTML/JSON drift because it only ever speaks one protocol: the NewsNow
envelope ``{status, items:[{title, url, mobileUrl, desc, ...}]}``. We
mirror the same contract here so the CN hot-list fetchers
(``wallstreetcn`` / ``cls`` / ``eastmoney`` / ``xueqiu``) can try the
aggregator first and only fall back to their fragile direct scrapers
when the aggregator is unavailable.

Keeping the logic here (instead of duplicating it into every fetcher)
gives us one testable surface: the parse rules, the retry behaviour,
and the rate-limit hook live in one place and every CN fetcher opts in
by pointing at a platform id.

Reference: ``D:/plugin-research-refs/repos/TrendRadar/trendradar/crawler/fetcher.py``
(L20-115) — we share its envelope + item-shape expectations but keep
our own canonical-item payload (``NormalizedItem``) so dedupe via
``articles.url_hash`` stays consistent.
"""

from __future__ import annotations

import logging
from typing import Any

from finpulse_fetchers._http import fetch_json, make_client
from finpulse_fetchers.base import NormalizedItem

logger = logging.getLogger(__name__)


# The public NewsNow endpoint maintained by the open-source author. The
# default lines up with TrendRadar's ``DataFetcher.DEFAULT_API_URL`` so
# users who migrate from there don't need to reconfigure anything.
DEFAULT_NEWSNOW_URL = "https://newsnow.busiyi.world/api/s"

# Only these statuses are treated as usable — matches
# ``DataFetcher.fetch_data`` (L95-100). Any other value (``error``,
# ``forbidden``, ``rate_limited``, missing) raises so the caller can
# count it as a failed probe and fall back to the direct scraper.
_ALLOWED_STATUS: frozenset[str] = frozenset({"success", "cache"})


def _resolve_api_url(config: dict[str, str]) -> str:
    """Pick the NewsNow endpoint from config, honouring self-host mode."""
    url = (config.get("newsnow.api_url") or "").strip()
    if url:
        return url
    return DEFAULT_NEWSNOW_URL


def newsnow_mode(config: dict[str, str]) -> str:
    """Return ``public`` / ``self_host`` / ``off`` normalised."""
    mode = (config.get("newsnow.mode") or "off").strip().lower()
    return mode if mode in {"public", "self_host", "off"} else "off"


async def fetch_from_newsnow(
    *,
    platform_id: str,
    source_id: str,
    config: dict[str, str],
    timeout_sec: float,
) -> list[NormalizedItem]:
    """Call the NewsNow aggregator and return normalised items.

    ``platform_id`` is the NewsNow-side slug (e.g. ``wallstreetcn-hot``,
    ``cls-hot``, ``eastmoney``, ``xueqiu-hotstock``). ``source_id`` is
    the fin-pulse-side id we want to tag the items with so they land in
    ``articles.source_id`` under the same slug the rest of the codebase
    uses (so the Today tab filter keeps working).

    The helper is deliberately small: no retries, no sleeps — the caller
    (a CN fetcher) is responsible for deciding what to do when we
    return an empty list or raise. Keeping it dumb makes it easy to
    monkey-patch in tests without stubbing an HTTP client.
    """
    mode = newsnow_mode(config)
    if mode == "off":
        return []

    api_url = _resolve_api_url(config)
    if not api_url:
        return []

    url = f"{api_url}?id={platform_id}&latest"
    async with make_client(timeout=timeout_sec) as client:
        payload = await fetch_json(client, url)

    return _parse_envelope(payload, platform_id=platform_id, source_id=source_id)


def _parse_envelope(
    payload: Any, *, platform_id: str, source_id: str
) -> list[NormalizedItem]:
    """Turn a NewsNow response body into :class:`NormalizedItem` rows.

    Guard rails mirror TrendRadar's ``crawl_websites`` (L151-173):
    skip ``None`` / ``float`` / empty titles, use ``url`` with
    ``mobileUrl`` fallback, and leave ranking / extra metadata inside
    ``extra`` so downstream consumers can opt into the signal.
    """
    if not isinstance(payload, dict):
        return []

    status = payload.get("status")
    if status not in _ALLOWED_STATUS:
        raise ValueError(f"unexpected newsnow status: {status!r}")

    rows = payload.get("items") or []
    if not isinstance(rows, list):
        return []

    out: list[NormalizedItem] = []
    seen_urls: set[str] = set()
    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue

        title_raw = row.get("title")
        if title_raw is None or isinstance(title_raw, float):
            continue
        title = str(title_raw).strip()
        if not title:
            continue

        url_raw = row.get("url") or row.get("mobileUrl") or ""
        url = str(url_raw).strip()
        if not url:
            continue

        # Title-based dedupe inside a single platform response so the
        # pinned-plus-repeated-in-body pattern (toutiao, weibo, cls) does
        # not double-count rows.
        if url in seen_urls:
            continue
        seen_urls.add(url)

        summary = _first_non_empty(row.get("desc"), row.get("summary"))
        published_at = _coerce_published(row.get("pubDate") or row.get("time"))

        extra: dict[str, Any] = {
            "via": "newsnow",
            "platform": platform_id,
            "rank": idx,
            "mobileUrl": row.get("mobileUrl") or None,
        }
        # Preserve any other key we didn't explicitly map so downstream
        # consumers (ai scoring, radar) can opt into extra signal.
        for k, v in row.items():
            if k in {"title", "url", "mobileUrl", "desc", "summary", "pubDate", "time"}:
                continue
            if v is None:
                continue
            extra.setdefault(k, v)

        out.append(
            NormalizedItem(
                source_id=source_id,
                title=title,
                url=url,
                summary=summary,
                published_at=published_at,
                extra=extra,
            )
        )

    return out


def _first_non_empty(*values: Any) -> str | None:
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None


def _coerce_published(value: Any) -> str | None:
    """NewsNow payloads use either an ISO string, a human date, or a
    millisecond timestamp. We keep ISO / human as-is (the AI layer
    tolerates both) and convert ms → ISO so downstream sort queries
    get a sortable lexical form.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            ts = float(value)
        except (TypeError, ValueError):
            return None
        if ts <= 0:
            return None
        # NewsNow ships both 10-digit and 13-digit timestamps; normalise.
        if ts > 1e12:
            ts /= 1000.0
        import time as _time

        return _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(ts))
    s = str(value).strip()
    return s or None


__all__ = [
    "DEFAULT_NEWSNOW_URL",
    "fetch_from_newsnow",
    "newsnow_mode",
]
