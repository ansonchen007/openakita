"""Hybrid fetcher coverage — verifies the 4 CN hot-list scrapers now
try the NewsNow aggregator first and fall back to their legacy direct
path when the aggregator returns empty or raises.

We monkey-patch both :func:`finpulse_fetchers.newsnow_base.fetch_from_newsnow`
and the per-fetcher ``_fetch_direct`` coroutine so no real HTTP
traffic escapes, then assert the ``_last_via`` instrumentation the
pipeline reads to surface the transport in the Today tab drawer.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from finpulse_fetchers import cls as cls_mod
from finpulse_fetchers import eastmoney as em_mod
from finpulse_fetchers import newsnow_base
from finpulse_fetchers import wallstreetcn as wscn_mod
from finpulse_fetchers import xueqiu as xq_mod
from finpulse_fetchers.base import NormalizedItem


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _make_items(source_id: str, prefix: str, n: int = 2) -> list[NormalizedItem]:
    return [
        NormalizedItem(
            source_id=source_id,
            title=f"{prefix} item {i}",
            url=f"https://example.com/{source_id}/{i}",
        )
        for i in range(n)
    ]


FETCHER_CASES = [
    (wscn_mod.WallStreetCNFetcher, wscn_mod),
    (cls_mod.CLSFetcher, cls_mod),
    (em_mod.EastmoneyFetcher, em_mod),
    (xq_mod.XueqiuFetcher, xq_mod),
]


class TestHybridPrimary:
    @pytest.mark.parametrize("cls,mod", FETCHER_CASES)
    def test_newsnow_success_short_circuits_direct(
        self,
        cls: Any,
        mod: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """NewsNow returns rows → direct path is never called, via=newsnow."""
        fetched_items = _make_items(cls.source_id, "nn")

        async def fake_newsnow(**kwargs: Any) -> list[NormalizedItem]:
            assert kwargs["platform_id"] == cls.NEWSNOW_PLATFORM_ID
            return list(fetched_items)

        async def forbidden_direct(self: Any) -> list[NormalizedItem]:  # noqa: ARG001
            raise AssertionError("_fetch_direct should not run when newsnow succeeds")

        monkeypatch.setattr(mod, "fetch_from_newsnow", fake_newsnow)
        monkeypatch.setattr(cls, "_fetch_direct", forbidden_direct)

        fetcher = cls(config={"newsnow.mode": "public"})
        items = _run(fetcher.fetch())
        assert [i.title for i in items] == [i.title for i in fetched_items]
        assert fetcher._last_via == "newsnow"


class TestHybridFallback:
    @pytest.mark.parametrize("cls,mod", FETCHER_CASES)
    def test_newsnow_empty_falls_back_to_direct(
        self,
        cls: Any,
        mod: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def empty_newsnow(**_: Any) -> list[NormalizedItem]:
            return []

        direct_items = _make_items(cls.source_id, "direct")

        async def stub_direct(self: Any) -> list[NormalizedItem]:  # noqa: ARG001
            return list(direct_items)

        monkeypatch.setattr(mod, "fetch_from_newsnow", empty_newsnow)
        monkeypatch.setattr(cls, "_fetch_direct", stub_direct)

        fetcher = cls(config={"newsnow.mode": "public"})
        items = _run(fetcher.fetch())
        assert [i.title for i in items] == [i.title for i in direct_items]
        assert fetcher._last_via == "direct"

    @pytest.mark.parametrize("cls,mod", FETCHER_CASES)
    def test_newsnow_raises_falls_back_to_direct(
        self,
        cls: Any,
        mod: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def raising_newsnow(**_: Any) -> list[NormalizedItem]:
            raise ValueError("forbidden")

        direct_items = _make_items(cls.source_id, "recovered")

        async def stub_direct(self: Any) -> list[NormalizedItem]:  # noqa: ARG001
            return list(direct_items)

        monkeypatch.setattr(mod, "fetch_from_newsnow", raising_newsnow)
        monkeypatch.setattr(cls, "_fetch_direct", stub_direct)

        fetcher = cls(config={"newsnow.mode": "public"})
        items = _run(fetcher.fetch())
        assert len(items) == len(direct_items)
        assert fetcher._last_via == "direct"


class TestHybridBothFail:
    @pytest.mark.parametrize("cls,mod", FETCHER_CASES)
    def test_both_empty_returns_none_via(
        self,
        cls: Any,
        mod: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def empty_newsnow(**_: Any) -> list[NormalizedItem]:
            return []

        async def empty_direct(self: Any) -> list[NormalizedItem]:  # noqa: ARG001
            return []

        monkeypatch.setattr(mod, "fetch_from_newsnow", empty_newsnow)
        monkeypatch.setattr(cls, "_fetch_direct", empty_direct)

        fetcher = cls(config={"newsnow.mode": "public"})
        items = _run(fetcher.fetch())
        assert items == []
        assert fetcher._last_via == "none"


class TestHybridFallbackOptOut:
    @pytest.mark.parametrize("cls,mod", FETCHER_CASES)
    def test_fallback_disabled_skips_direct(
        self,
        cls: Any,
        mod: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def empty_newsnow(**_: Any) -> list[NormalizedItem]:
            return []

        async def forbidden_direct(self: Any) -> list[NormalizedItem]:  # noqa: ARG001
            raise AssertionError("direct should be skipped")

        monkeypatch.setattr(mod, "fetch_from_newsnow", empty_newsnow)
        monkeypatch.setattr(cls, "_fetch_direct", forbidden_direct)

        fetcher = cls(
            config={
                "newsnow.mode": "public",
                f"source.{cls.source_id}.fallback_direct": "false",
            }
        )
        items = _run(fetcher.fetch())
        assert items == []
        assert fetcher._last_via == "none"


def test_fetchers_expose_platform_id_constants() -> None:
    """Smoke: platform ids must match TrendRadar's naming so users
    migrating from TrendRadar don't need to rewire their config."""
    assert wscn_mod.WallStreetCNFetcher.NEWSNOW_PLATFORM_ID == "wallstreetcn-hot"
    assert cls_mod.CLSFetcher.NEWSNOW_PLATFORM_ID == "cls-hot"
    assert em_mod.EastmoneyFetcher.NEWSNOW_PLATFORM_ID == "eastmoney"
    assert xq_mod.XueqiuFetcher.NEWSNOW_PLATFORM_ID == "xueqiu-hotstock"
    # And the helper module advertises the default endpoint.
    assert newsnow_base.DEFAULT_NEWSNOW_URL.endswith("/api/s")
