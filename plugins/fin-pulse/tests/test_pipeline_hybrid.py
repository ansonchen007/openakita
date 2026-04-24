"""Pipeline-level coverage for the hybrid-fetch rework.

The pipeline gained three user-visible behaviours that tests pin down:

1. Each ``summary.by_source[id]`` now carries a ``via`` field —
   ``"newsnow"`` / ``"direct"`` / ``"none"`` — sourced from the
   fetcher's ``_last_via`` attribute.
2. ``summary.totals`` exposes ``sources_total`` and ``sources_ok`` so
   the Today-tab toast can render ``X 源成功 · Y 失败 · Z 无结果``
   without the UI re-counting ``by_source``.
3. The ``no_sources_enabled`` early return must flip the task row to
   ``"skipped"`` — previously it left the row stuck at ``"running"``.
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

import finpulse_pipeline as pipeline_mod
from finpulse_fetchers import SOURCE_REGISTRY
from finpulse_fetchers.base import BaseFetcher, NormalizedItem
from finpulse_pipeline import ingest
from finpulse_task_manager import FinpulseTaskManager


def _disable_all_sources() -> dict[str, str]:
    """Force every registered source off — callers flip the ones they need back on.

    ``tm.init()`` seeds per-source flags from ``SOURCE_DEFS.default_enabled``,
    so tests that want a predictable subset must explicitly reset them.
    """
    return {f"source.{sid}.enabled": "false" for sid in SOURCE_REGISTRY.keys()}


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class _HybridStub(BaseFetcher):
    """Stub that mimics the hybrid fetchers' ``_last_via`` attribute."""

    def __init__(
        self,
        *,
        source_id: str,
        items: list[NormalizedItem],
        via: str = "newsnow",
    ) -> None:
        super().__init__(config={})
        self.source_id = source_id  # type: ignore[assignment]
        self._items = items
        self._last_via = via

    async def fetch(self, **_: Any) -> list[NormalizedItem]:
        return list(self._items)


class TestIngestVia:
    def test_summary_by_source_contains_via(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tm = FinpulseTaskManager(tmp_path / "pipe.db")
        _run(tm.init())
        overrides = _disable_all_sources()
        overrides["source.wallstreetcn.enabled"] = "true"
        overrides["source.cls.enabled"] = "true"
        _run(tm.set_configs(overrides))

        def fake_get_fetcher(
            source_id: str, *, config: dict[str, str] | None = None
        ) -> Any:
            if source_id == "wallstreetcn":
                return _HybridStub(
                    source_id="wallstreetcn",
                    items=[
                        NormalizedItem(
                            source_id="wallstreetcn",
                            title="WSCN via NewsNow",
                            url="https://wallstreetcn.com/a/1",
                        )
                    ],
                    via="newsnow",
                )
            if source_id == "cls":
                return _HybridStub(
                    source_id="cls",
                    items=[
                        NormalizedItem(
                            source_id="cls",
                            title="CLS via direct",
                            url="https://www.cls.cn/b/1",
                        )
                    ],
                    via="direct",
                )
            return None

        monkeypatch.setattr(pipeline_mod, "get_fetcher", fake_get_fetcher)

        summary = _run(ingest(tm, since_hours=24))
        assert summary["ok"] is True
        by_source = summary["by_source"]
        assert by_source["wallstreetcn"]["via"] == "newsnow"
        assert by_source["cls"]["via"] == "direct"

        totals = summary["totals"]
        assert totals["sources_total"] == 2
        assert totals["sources_ok"] == 2


class TestIngestNoSourcesEnabled:
    def test_returns_skipped_and_flips_task_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tm = FinpulseTaskManager(tmp_path / "pipe.db")
        _run(tm.init())
        # Explicitly disable every source.
        _run(tm.set_configs(_disable_all_sources()))

        task = _run(tm.create_task(mode="ingest", params={"since_hours": 24}, status="running"))

        summary = _run(ingest(tm, since_hours=24, task_id=task["id"]))
        assert summary["ok"] is False
        assert summary["reason"] == "no_sources_enabled"
        assert summary["totals"]["sources_total"] == 0

        # Task row is no longer stuck at running — it flips to skipped
        # so the UI can render a grey pill instead of a spinning one.
        reloaded = _run(tm.get_task(task["id"]))
        assert reloaded is not None
        assert reloaded.get("status") == "skipped"


class TestIngestAutoPromotePublic:
    def test_cn_source_lifts_newsnow_mode_in_memory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tm = FinpulseTaskManager(tmp_path / "pipe.db")
        _run(tm.init())
        overrides = _disable_all_sources()
        overrides["source.wallstreetcn.enabled"] = "true"
        # Persist mode=off so the auto-promote path is the one under test
        # (the seed default is already "public"; we want to prove the
        # pipeline flips it in memory when a CN source needs it).
        overrides["newsnow.mode"] = "off"
        _run(tm.set_configs(overrides))

        observed_mode: dict[str, str | None] = {"value": None}

        def fake_get_fetcher(
            source_id: str, *, config: dict[str, str] | None = None
        ) -> Any:
            observed_mode["value"] = (config or {}).get("newsnow.mode")
            return _HybridStub(
                source_id="wallstreetcn",
                items=[
                    NormalizedItem(
                        source_id="wallstreetcn",
                        title="t",
                        url="https://wallstreetcn.com/a/42",
                    )
                ],
                via="newsnow",
            )

        monkeypatch.setattr(pipeline_mod, "get_fetcher", fake_get_fetcher)

        summary = _run(ingest(tm, since_hours=24))
        assert summary["ok"] is True
        # The in-memory cfg handed to the fetcher was lifted to public
        # even though the persisted value was explicitly "off".
        assert observed_mode["value"] == "public"
        # But the persisted value must remain untouched — the pipeline
        # deliberately does NOT commit the auto-promoted mode.
        persisted = _run(tm.get_all_config())
        assert persisted.get("newsnow.mode") == "off", (
            "auto-promote must stay in-memory; must not persist to config"
        )
