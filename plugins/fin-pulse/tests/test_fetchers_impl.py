"""Phase 2b — per-source fetcher behaviour.

Every test stubs the network layer so the suite stays hermetic. httpx
ships ``MockTransport`` in the standard distribution, so we do not pull
in respx. Sources that need ``feedparser`` skip gracefully when the dep
is absent (the pipeline already classifies that as ``error_kind =
dependency``).
"""

from __future__ import annotations

import asyncio
import importlib
import json
from typing import Any

import httpx
import pytest

import finpulse_fetchers._http as http_mod
from finpulse_fetchers.base import NormalizedItem
from finpulse_fetchers.cls import CLSFetcher
from finpulse_fetchers.eastmoney import EastmoneyFetcher
from finpulse_fetchers.fed_fomc import FedFOMCFetcher
from finpulse_fetchers.newsnow import NewsNowFetcher
from finpulse_fetchers.rss import FEEDPARSER_AVAILABLE, GenericRSSFetcher, parse_feed
from finpulse_fetchers.wallstreetcn import WallStreetCNFetcher

BS4_AVAILABLE: bool
try:
    importlib.import_module("bs4")
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _patch_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
) -> None:
    """Rewire :func:`make_client` to return a MockTransport-backed client."""

    def _factory(
        *,
        timeout: float = 15.0,
        extra_headers: dict[str, str] | None = None,
        follow_redirects: bool = True,
    ) -> httpx.AsyncClient:
        headers = {"User-Agent": "test"}
        if extra_headers:
            headers.update(extra_headers)
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers=headers,
            timeout=timeout,
            follow_redirects=follow_redirects,
        )

    monkeypatch.setattr(http_mod, "make_client", _factory)
    # Several fetchers import make_client directly — patch those modules too.
    for mod_name in (
        "finpulse_fetchers.rss",
        "finpulse_fetchers.cls",
        "finpulse_fetchers.eastmoney",
        "finpulse_fetchers.wallstreetcn",
        "finpulse_fetchers.sec_edgar",
        "finpulse_fetchers.pbc_omo",
        "finpulse_fetchers.fed_fomc",
        "finpulse_fetchers.newsnow",
    ):
        mod = importlib.import_module(mod_name)
        if hasattr(mod, "make_client"):
            monkeypatch.setattr(mod, "make_client", _factory)


# ── CLS Telegram ─────────────────────────────────────────────────────────


class TestCLSFetcher:
    def test_parses_roll_data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {
            "data": {
                "roll_data": [
                    {
                        "title": "央行开展 2000 亿逆回购操作",
                        "brief": "内容摘要",
                        "shareurl": "https://www.cls.cn/detail/100",
                        "ctime": 1_713_600_000,
                        "level": "A",
                    },
                    {
                        "title": "",
                        "brief": "只有 brief 的短讯内容",
                        "shareurl": "https://www.cls.cn/detail/101",
                        "ctime": 1_713_600_060,
                    },
                    {
                        "title": "缺少 URL 的条目会被丢弃",
                        "shareurl": "",
                    },
                ]
            }
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        _patch_transport(monkeypatch, handler)
        items = _run(CLSFetcher(config={}).fetch())

        assert len(items) == 2
        assert items[0].url == "https://www.cls.cn/detail/100"
        assert items[0].extra.get("level") == "A"
        # Auto-derived title when ``title`` is empty but brief exists.
        assert items[1].title.startswith("只有 brief")


# ── EastMoney ────────────────────────────────────────────────────────────


class TestEastMoneyFetcher:
    def test_parses_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {
            "data": {
                "list": [
                    {
                        "Art_Title": "上市公司公告速览",
                        "Art_URL": "https://www.eastmoney.com/news/1",
                        "Art_ShowTime": "2026-04-24 09:00:00",
                        "Art_MediaName": "东方财富",
                    },
                    {"Art_Title": "", "Art_URL": ""},  # dropped
                ]
            }
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        _patch_transport(monkeypatch, handler)
        items = _run(EastmoneyFetcher(config={}).fetch())

        assert len(items) == 1
        assert items[0].title == "上市公司公告速览"
        assert items[0].extra.get("media") == "东方财富"


# ── WallStreet CN ────────────────────────────────────────────────────────


class TestWallStreetCNFetcher:
    @pytest.mark.skipif(
        not FEEDPARSER_AVAILABLE, reason="feedparser not installed"
    )
    def test_rss_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rss_body = (
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<rss version='2.0'><channel>"
            "<title>WSCN</title><link>https://wallstreetcn.com</link>"
            "<item><title>股市早报</title>"
            "<link>https://wallstreetcn.com/articles/3001</link>"
            "<description>摘要</description></item>"
            "</channel></rss>"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=rss_body)

        _patch_transport(monkeypatch, handler)
        items = _run(WallStreetCNFetcher(config={}).fetch())

        assert len(items) == 1
        assert items[0].url.endswith("3001")

    def test_empty_rss_falls_back_to_html(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # RSS returns empty channel; HTML fallback carries NEXT_DATA JSON.
        next_data = {
            "props": {
                "pageProps": {
                    "articles": [
                        {
                            "title": "华尔街见闻首页要闻",
                            "url": "https://wallstreetcn.com/articles/4000",
                            "content_short": "简短摘要",
                        }
                    ]
                }
            }
        }
        html = (
            "<html><body>"
            "<script id=\"__NEXT_DATA__\">" + json.dumps(next_data) + "</script>"
            "</body></html>"
        )
        empty_rss = "<?xml version='1.0'?><rss><channel><title>empty</title></channel></rss>"
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            if "feed" in request.url.path:
                return httpx.Response(200, text=empty_rss)
            return httpx.Response(200, text=html)

        _patch_transport(monkeypatch, handler)
        items = _run(WallStreetCNFetcher(config={}).fetch())

        # Either feedparser parsed the empty RSS (0 items) and the HTML fallback
        # produced one, or feedparser is missing and the RSS path raised into
        # the HTML fallback still. Both should end with >= 1 HTML-sourced item.
        assert any(
            "wallstreetcn.com/articles/4000" in (item.url or "")
            for item in items
        )
        assert any("/feed" in p for p in seen)


# ── NewsNow ──────────────────────────────────────────────────────────────


class TestNewsNowFetcher:
    def test_off_mode_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            pytest.fail("newsnow must not perform IO when mode==off")

        _patch_transport(monkeypatch, handler)
        cfg = {"newsnow.mode": "off", "newsnow.api_url": "https://example.com/api/s"}
        items = _run(NewsNowFetcher(config=cfg).fetch())
        assert items == []

    def test_public_mode_reads_api_url_from_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []
        payload = {
            "status": "success",
            "items": [
                {
                    "title": "Hot tag",
                    "url": "https://site.com/a",
                    "mobileUrl": "https://m.site.com/a",
                    "rank": 1,
                }
            ],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(200, json=payload)

        _patch_transport(monkeypatch, handler)
        cfg = {
            "newsnow.mode": "public",
            "newsnow.api_url": "https://custom.example.com/api/s",
            "newsnow.channels": "wallstreetcn-hot",
        }
        items = _run(NewsNowFetcher(config=cfg).fetch())

        assert len(items) == 1
        # Confirms TrendRadar bug fix: api_url honoured from config, not hard-coded.
        assert calls and calls[0].startswith("https://custom.example.com/api/s")
        assert items[0].source_id == "newsnow:wallstreetcn-hot"

    def test_status_outside_whitelist_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "ratelimited", "items": []})

        _patch_transport(monkeypatch, handler)
        cfg = {
            "newsnow.mode": "public",
            "newsnow.api_url": "https://x.example/api/s",
            "newsnow.channels": "wallstreetcn-hot",
        }
        with pytest.raises(ValueError, match="unexpected newsnow status"):
            _run(NewsNowFetcher(config=cfg).fetch())


# ── Fed FOMC calendar gating ────────────────────────────────────────────


class TestFedFOMCCalendarGate:
    def test_returns_empty_when_today_not_in_calendar(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Simulate a calendar with no dates matching today.
        from finpulse_fetchers import fed_fomc as mod

        monkeypatch.setattr(mod, "_load_calendar", lambda: {"1900-01-01"})

        def handler(request: httpx.Request) -> httpx.Response:
            pytest.fail("fed_fomc must not hit the network on non-release days")

        _patch_transport(monkeypatch, handler)
        items = _run(FedFOMCFetcher(config={}).fetch())
        assert items == []

    def test_empty_calendar_allows_scrape(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An empty calendar means "gate disabled"; the fetcher proceeds.
        if not BS4_AVAILABLE:
            pytest.skip("bs4 not installed")
        from finpulse_fetchers import fed_fomc as mod

        monkeypatch.setattr(mod, "_load_calendar", lambda: set())
        html = (
            "<html><body>"
            "<a href='/newsevents/pressreleases/20260430-foo.htm'>FOMC Statement 20260430</a>"
            "</body></html>"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=html)

        _patch_transport(monkeypatch, handler)
        items = _run(FedFOMCFetcher(config={}).fetch())
        assert len(items) >= 1
        assert any("20260430" in it.url for it in items)


# ── Generic RSS ──────────────────────────────────────────────────────────


class TestGenericRSSFetcher:
    def test_no_feeds_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        items = _run(GenericRSSFetcher(config={"rss_generic.feeds": ""}).fetch())
        assert items == []

    @pytest.mark.skipif(
        not FEEDPARSER_AVAILABLE, reason="feedparser not installed"
    )
    def test_multi_feed_aggregation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _make_body(feed_id: int) -> str:
            return (
                "<?xml version='1.0'?><rss version='2.0'><channel>"
                f"<title>feed-{feed_id}</title>"
                "<item><title>Post</title>"
                f"<link>https://feed{feed_id}.example.com/p</link></item>"
                "</channel></rss>"
            )

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "f1" in url:
                return httpx.Response(200, text=_make_body(1))
            return httpx.Response(200, text=_make_body(2))

        _patch_transport(monkeypatch, handler)
        cfg = {
            "rss_generic.feeds": (
                "https://f1.example/rss\nhttps://f2.example/rss"
            )
        }
        items = _run(GenericRSSFetcher(config=cfg).fetch())
        # Each feed's item has ``extra['feed_url']`` threaded through.
        urls = [item.url for item in items]
        assert "https://feed1.example.com/p" in urls
        assert "https://feed2.example.com/p" in urls
        assert all("feed_url" in item.extra for item in items)


# ── Parse feed direct ────────────────────────────────────────────────────


@pytest.mark.skipif(not FEEDPARSER_AVAILABLE, reason="feedparser not installed")
class TestParseFeed:
    def test_drops_entries_without_title_or_link(self) -> None:
        body = (
            "<?xml version='1.0'?><rss version='2.0'><channel><title>x</title>"
            "<item><title>ok</title><link>https://e.com/1</link></item>"
            "<item><title></title><link>https://e.com/2</link></item>"
            "<item><title>no-link</title></item>"
            "</channel></rss>"
        )
        items = parse_feed("x", body)
        assert len(items) == 1
        assert items[0].url == "https://e.com/1"


# ── Smoke: every fetcher is importable & obeys BaseFetcher contract ──────


class TestFetcherContract:
    @pytest.mark.parametrize(
        "module_name, class_name",
        [
            ("finpulse_fetchers.wallstreetcn", "WallStreetCNFetcher"),
            ("finpulse_fetchers.cls", "CLSFetcher"),
            ("finpulse_fetchers.xueqiu", "XueqiuFetcher"),
            ("finpulse_fetchers.eastmoney", "EastmoneyFetcher"),
            ("finpulse_fetchers.pbc_omo", "PbcOmoFetcher"),
            ("finpulse_fetchers.nbs", "NBSFetcher"),
            ("finpulse_fetchers.fed_fomc", "FedFOMCFetcher"),
            ("finpulse_fetchers.sec_edgar", "SecEdgarFetcher"),
            ("finpulse_fetchers.rss", "GenericRSSFetcher"),
            ("finpulse_fetchers.newsnow", "NewsNowFetcher"),
        ],
    )
    def test_each_fetcher_defines_source_id(
        self, module_name: str, class_name: str
    ) -> None:
        mod = importlib.import_module(module_name)
        cls = getattr(mod, class_name)
        assert cls.source_id, f"{class_name} missing source_id"
        assert callable(getattr(cls, "fetch", None)), (
            f"{class_name} must define async fetch()"
        )


# ── NormalizedItem cross-source dedupe hash ─────────────────────────────


class TestCrossSourceDedupe:
    def test_same_canonical_url_hashes_equal_across_sources(self) -> None:
        """Same article posted on wallstreetcn and newsnow:wallstreetcn-hot
        produces the same ``url_hash`` so :func:`upsert_article` collapses
        them into one row (the 2c dedupe behaviour).
        """
        a = NormalizedItem(
            source_id="wallstreetcn",
            title="t",
            url="https://wallstreetcn.com/articles/9?utm_source=x",
        )
        b = NormalizedItem(
            source_id="newsnow:wallstreetcn-hot",
            title="t",
            url="https://wallstreetcn.com/articles/9",
        )
        assert a.url_hash() == b.url_hash()
