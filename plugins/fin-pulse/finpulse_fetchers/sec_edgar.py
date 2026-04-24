"""SEC EDGAR filings — RSS feed (public, requires UA with contact)."""

from __future__ import annotations

from typing import Any

from finpulse_fetchers._http import fetch_text, make_client
from finpulse_fetchers.base import BaseFetcher, NormalizedItem
from finpulse_fetchers.rss import parse_feed


_EDGAR_RSS = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcurrent&type=8-K&company=&dateb=&owner=include&count=40&output=atom"
)


class SecEdgarFetcher(BaseFetcher):
    source_id = "sec_edgar"

    async def fetch(self, **_: Any) -> list[NormalizedItem]:
        contact = self._config.get(
            "sec_edgar.contact", "OpenAkita fin-pulse contact@openakita.com"
        )
        headers = {"User-Agent": contact}
        async with make_client(
            timeout=self._timeout_sec, extra_headers=headers
        ) as client:
            body = await fetch_text(client, _EDGAR_RSS)
        return parse_feed(self.source_id, body)


__all__ = ["SecEdgarFetcher"]
