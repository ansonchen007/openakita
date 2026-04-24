"""People's Bank of China — Open Market Operations crawler.

The PBC site guards the real landing URL with an obfuscated ``atob(...)``
JavaScript redirect. We lift the PbcCrawler workaround: pluck the first
``<script>`` tag, rewrite ``atob(`` to ``window["atob"](``, wrap in a
``getURL()`` shim and execute via PyExecJS to resolve the redirect.

PyExecJS is soft-imported — when Node.js / PyExecJS isn't available the
fetcher raises ``ImportError`` so the pipeline writes ``error_kind =
"dependency"`` and the Settings panel nudges the operator with
hint text (§12 of the plan).
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from finpulse_fetchers._http import fetch_text, make_client
from finpulse_fetchers.base import BaseFetcher, NormalizedItem

try:  # pragma: no cover — optional dep, documented in VALIDATION.md
    import execjs  # type: ignore

    EXECJS_AVAILABLE = True
except ImportError:
    execjs = None  # type: ignore
    EXECJS_AVAILABLE = False

try:  # pragma: no cover — optional dep, standard on the host
    import bs4  # type: ignore

    BS4_AVAILABLE = True
except ImportError:
    bs4 = None  # type: ignore
    BS4_AVAILABLE = False


_HOME = "http://www.pbc.gov.cn"
_ENTRY = f"{_HOME}/zhengcehuobisi/125207/125213/125431/125475/17081/index.html"


def _resolve_redirect(html: str) -> str:
    """Run the inline obfuscation JS and return the absolute target URL."""
    if not EXECJS_AVAILABLE:
        raise ImportError(
            "pbc_omo requires PyExecJS + a JS runtime (Node.js). "
            "Install via `pip install PyExecJS` and ensure `node` is on PATH."
        )
    if not BS4_AVAILABLE:
        raise ImportError(
            "pbc_omo requires beautifulsoup4. Install via `pip install beautifulsoup4`."
        )
    soup = bs4.BeautifulSoup(html, "html.parser")
    scripts = soup.select("script")
    js_code = (scripts[0].string or "") if scripts else ""
    js_code = re.sub(r"atob\(", 'window["atob"](', js_code)
    js_fn = (
        'function getURL(){ var window = {};' + js_code + 'return window["location"];}'
    )
    path = execjs.compile(js_fn).call("getURL")
    return _HOME + path


class PbcOmoFetcher(BaseFetcher):
    source_id = "pbc_omo"

    async def fetch(self, **_: Any) -> list[NormalizedItem]:
        async with make_client(timeout=self._timeout_sec) as client:
            entry_body = await fetch_text(client, _ENTRY)
            target = _resolve_redirect(entry_body)
            body = await fetch_text(
                client,
                target,
                headers={"Referer": _HOME + "/"},
            )
        return self._parse(target, body)

    @staticmethod
    def _parse(base_url: str, html: str) -> list[NormalizedItem]:
        if not BS4_AVAILABLE:
            raise ImportError("pbc_omo requires beautifulsoup4")
        soup = bs4.BeautifulSoup(html, "html.parser")
        items: list[NormalizedItem] = []
        # The OMO page renders each release as a date + title row; parse
        # both typical layouts defensively.
        for anchor in soup.select("a[title], a[href]")[:80]:
            href = (anchor.get("href") or "").strip()
            title = (anchor.get("title") or anchor.text or "").strip()
            if not title or len(title) < 6 or not href:
                continue
            if not href.startswith("http"):
                href = _HOME + href
            published = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            uid = hashlib.sha256(href.encode("utf-8")).hexdigest()[:16]
            items.append(
                NormalizedItem(
                    source_id="pbc_omo",
                    title=title,
                    url=href,
                    published_at=published,
                    extra={"uid": uid, "parent": base_url},
                )
            )
        # Dedupe within a run (same URL often rendered twice).
        seen: set[str] = set()
        out: list[NormalizedItem] = []
        for item in items:
            key = item.url_hash()
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out[:30]


__all__ = ["EXECJS_AVAILABLE", "PbcOmoFetcher"]
