"""Fed FOMC statement scraper with a release-calendar gate.

The Fed publishes FOMC statements on a known cadence (~8 meetings per
year). Scraping the calendar page on every ingest is wasteful and noisy,
so the fetcher consults :file:`extra/fomc_release_calendar.txt` first —
if today is not on the calendar, it returns an empty list immediately.
This mirrors the ``main.yml`` gating pattern in the ``fed-statement-scraping``
GitHub Action.

A ``most_recent_date`` cursor in ``config`` ensures we only report rows
strictly newer than the last successful ingest.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from finpulse_fetchers._http import fetch_text, make_client
from finpulse_fetchers.base import BaseFetcher, NormalizedItem


_CALENDAR_FILE = (
    Path(__file__).resolve().parent.parent / "extra" / "fomc_release_calendar.txt"
)
_STATEMENTS_URL = (
    "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
)


def _load_calendar() -> set[str]:
    if not _CALENDAR_FILE.exists():
        return set()
    out: set[str] = set()
    for line in _CALENDAR_FILE.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.add(line)
    return out


class FedFOMCFetcher(BaseFetcher):
    source_id = "fed_fomc"

    @property
    def supports_since(self) -> bool:
        return True

    async def fetch(
        self, *, since: datetime | None = None, **_: Any
    ) -> list[NormalizedItem]:
        today = datetime.now(timezone.utc).date().isoformat()
        calendar = _load_calendar()
        if calendar and today not in calendar:
            return []
        cursor = self._config.get("fed_fomc.most_recent_date", "")
        async with make_client(timeout=self._timeout_sec) as client:
            body = await fetch_text(client, _STATEMENTS_URL)
        return self._parse(body, cursor_date=cursor)

    @staticmethod
    def _parse(html: str, *, cursor_date: str = "") -> list[NormalizedItem]:
        try:
            import bs4  # type: ignore
        except ImportError as exc:
            raise ImportError("fed_fomc requires beautifulsoup4") from exc
        soup = bs4.BeautifulSoup(html, "html.parser")
        items: list[NormalizedItem] = []
        seen: set[str] = set()
        for anchor in soup.select("a[href]"):
            href = (anchor.get("href") or "").strip()
            if not any(
                tok in href
                for tok in ("newsevents/pressreleases", "monetary", "fomccalendars")
            ):
                continue
            title = (anchor.text or "").strip()
            if not title or len(title) < 6:
                continue
            if not href.startswith("http"):
                href = "https://www.federalreserve.gov" + href
            # Calendar pages expose dates in the href (e.g. …20260430…).
            published = _extract_iso_date(href)
            if cursor_date and published and published <= cursor_date:
                continue
            if href in seen:
                continue
            seen.add(href)
            items.append(
                NormalizedItem(
                    source_id="fed_fomc",
                    title=title,
                    url=href,
                    published_at=published,
                    extra={"gate": "calendar"},
                )
            )
        return items[:20]


def _extract_iso_date(href: str) -> str | None:
    import re

    match = re.search(r"(20\d{2})(\d{2})(\d{2})", href)
    if not match:
        return None
    try:
        parts = tuple(int(g) for g in match.groups())
        d = date(parts[0], parts[1], parts[2])
    except ValueError:
        return None
    return d.isoformat()


__all__ = ["FedFOMCFetcher"]
