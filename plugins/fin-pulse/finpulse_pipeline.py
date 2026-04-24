# ruff: noqa: N999
"""Ingest + analysis pipeline for fin-pulse.

The pipeline is deliberately thin: each stage is an ``async def`` that
reads rows from :class:`FinpulseTaskManager` and writes the next stage
back. Phase 2 lands :func:`ingest` (collect → normalize → dedupe);
Phase 3 layers AI scoring on top; Phase 4 renders digests and hands the
payload to :mod:`finpulse_dispatch`.

All stages are side-effect free on the event loop — long-running
fetches move off the hot path via :func:`asyncio.gather` with a
concurrency cap read from ``config['fetch_concurrency']``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from finpulse_errors import map_exception
from finpulse_fetchers import SOURCE_REGISTRY, get_fetcher
from finpulse_fetchers.base import FetchReport, NormalizedItem
from finpulse_frequency import FrequencyMatcher, compile_matcher
from finpulse_models import SESSIONS, SOURCE_DEFS
from finpulse_report import build_daily_brief

if TYPE_CHECKING:
    from finpulse_dispatch import DispatchService
    from finpulse_task_manager import FinpulseTaskManager

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _resolve_enabled_sources(
    tm: FinpulseTaskManager, *, include: list[str] | None = None
) -> list[str]:
    """Read enabled-source flags from config, intersected with ``include``.

    ``include == None`` → every registered source whose
    ``config['source.{id}.enabled']`` is ``"true"`` is returned.
    Passing ``include`` restricts the run to the named subset (still
    requiring the enabled flag).
    """
    cfg = await tm.get_all_config()
    sources: list[str] = []
    universe = include if include else list(SOURCE_REGISTRY.keys())
    for source_id in universe:
        if source_id not in SOURCE_REGISTRY:
            continue
        if cfg.get(f"source.{source_id}.enabled", "false") != "true":
            continue
        sources.append(source_id)
    return sources


async def _fetch_one(
    source_id: str,
    *,
    cfg: dict[str, str],
    timeout_sec: float,
    since: datetime | None,
) -> FetchReport:
    """Run a single fetcher and wrap its outcome in :class:`FetchReport`.

    Exceptions never escape — they are classified via
    :func:`finpulse_errors.map_exception` and surface as ``error_kind``
    on the report so the pipeline can write ``config['source.{id}.last_error']``.
    """
    t0 = time.perf_counter()
    fetcher = get_fetcher(source_id, config=cfg)
    if fetcher is None:
        return FetchReport(
            source_id=source_id,
            error=f"fetcher not available: {source_id}",
            error_kind="dependency",
            duration_ms=(time.perf_counter() - t0) * 1000.0,
        )
    fetcher._timeout_sec = float(timeout_sec)  # type: ignore[attr-defined]
    try:
        if fetcher.supports_since:
            items = await fetcher.fetch(since=since)
        else:
            items = await fetcher.fetch()
        return FetchReport(
            source_id=source_id,
            items=list(items or []),
            duration_ms=(time.perf_counter() - t0) * 1000.0,
        )
    except Exception as exc:  # noqa: BLE001 — intentional pipeline boundary
        kind, msg, _hints = map_exception(exc)
        logger.warning("fetcher %s failed: %s (%s)", source_id, msg, kind)
        return FetchReport(
            source_id=source_id,
            error=msg,
            error_kind=kind,
            duration_ms=(time.perf_counter() - t0) * 1000.0,
        )


async def _persist_items(
    tm: FinpulseTaskManager, items: list[NormalizedItem]
) -> tuple[int, int]:
    """Insert-or-update every item; return ``(inserted, updated)`` counts."""
    inserted = 0
    updated = 0
    now = _utcnow_iso()
    for item in items:
        if not item.title or not item.url:
            continue
        try:
            _aid, is_new = await tm.upsert_article(
                source_id=item.source_id,
                url=item.url,
                url_hash=item.url_hash(),
                title=item.title,
                fetched_at=now,
                summary=item.summary,
                content=item.content,
                published_at=item.published_at,
                raw=item.extra,
            )
        except Exception as exc:  # noqa: BLE001 — defensive per-row isolation
            logger.warning("upsert article failed for %s: %s", item.url, exc)
            continue
        if is_new:
            inserted += 1
        else:
            updated += 1
    return inserted, updated


async def ingest(
    tm: FinpulseTaskManager,
    *,
    sources: list[str] | None = None,
    since_hours: int | None = 24,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Fan-out to every enabled source, dedupe into ``articles``, update
    ``last_ok`` / ``last_error`` config keys, and return a per-source
    summary suitable for the ``tasks.result_json`` payload.
    """
    cfg = await tm.get_all_config()
    enabled = await _resolve_enabled_sources(tm, include=sources)
    if not enabled:
        return {"ok": False, "reason": "no_sources_enabled", "by_source": {}}

    since: datetime | None = None
    if since_hours:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        since = datetime.fromtimestamp(
            now.timestamp() - int(since_hours) * 3600, tz=timezone.utc
        )

    timeout_sec = float(cfg.get("fetch_timeout_sec", "15") or "15")
    try:
        concurrency = int(cfg.get("fetch_concurrency", "4") or "4")
    except ValueError:
        concurrency = 4
    concurrency = max(1, min(concurrency, 16))

    sem = asyncio.Semaphore(concurrency)

    async def _guarded(source_id: str) -> FetchReport:
        async with sem:
            return await _fetch_one(
                source_id, cfg=cfg, timeout_sec=timeout_sec, since=since
            )

    reports = await asyncio.gather(*[_guarded(sid) for sid in enabled])

    summary: dict[str, Any] = {
        "ok": True,
        "since": since.strftime("%Y-%m-%dT%H:%M:%SZ") if since else None,
        "by_source": {},
        "totals": {"fetched": 0, "inserted": 0, "updated": 0, "failed_sources": 0},
    }
    updates: dict[str, str] = {}

    for report in reports:
        entry: dict[str, Any] = {
            "fetched": len(report.items),
            "duration_ms": round(report.duration_ms, 2),
        }
        if report.error:
            entry["error_kind"] = report.error_kind
            entry["error"] = report.error
            updates[f"source.{report.source_id}.last_error"] = (
                f"{_utcnow_iso()}: {report.error_kind}: {report.error}"
            )
            summary["totals"]["failed_sources"] += 1
        else:
            # Only persist items + clear last_error if the fetch succeeded.
            inserted, updated = await _persist_items(tm, report.items)
            entry["inserted"] = inserted
            entry["updated"] = updated
            summary["totals"]["inserted"] += inserted
            summary["totals"]["updated"] += updated
            updates[f"source.{report.source_id}.last_ok"] = _utcnow_iso()
            updates[f"source.{report.source_id}.last_error"] = ""
        summary["totals"]["fetched"] += entry["fetched"]
        summary["by_source"][report.source_id] = entry

    if updates:
        await tm.set_configs(updates)

    if task_id is not None:
        await tm.update_task_safe(
            task_id,
            status="succeeded",
            progress=1.0,
            result=summary,
            completed_at=time.time(),
            finished_at=_utcnow_iso(),
        )
    return summary


async def run_daily_brief(
    tm: FinpulseTaskManager,
    *,
    session: str,
    since_hours: int = 12,
    top_k: int = 20,
    lang: str = "zh",
    task_id: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Build a daily-brief digest and persist it to the ``digests`` table.

    This deliberately sits above :func:`ingest` — callers are expected
    to have already ingested fresh articles before triggering a digest,
    either via the ``pipeline_ingest`` task or the scheduled hook. The
    function only reads ``articles`` and writes the rendered blob into
    ``digests``; it does **not** dispatch notifications (that is Phase
    4b's job).

    ``session`` must be one of :data:`finpulse_models.SESSIONS`. The
    window defaults to 12h back — morning/noon/evening cadences each
    look back through the previous session's tail.
    """
    if session not in SESSIONS:
        raise ValueError(f"invalid session {session!r}, expected one of {SESSIONS}")

    top_k = max(1, min(int(top_k), 60))
    since_hours = max(1, min(int(since_hours), 72))
    now = datetime.now(timezone.utc)
    since = datetime.fromtimestamp(
        now.timestamp() - since_hours * 3600, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    rows, total = await tm.list_articles(
        since=since,
        sort="score",
        limit=max(top_k * 3, 60),
        offset=0,
    )
    generated_at = _utcnow_iso()
    markdown, html_blob, stats = build_daily_brief(
        rows,
        session=session,
        top_k=top_k,
        lang=lang,
        generated_at=generated_at,
        title=title,
    )

    digest_id = await tm.create_digest(
        session=session,
        generated_at=generated_at,
        title=title,
        markdown_blob=markdown,
        html_blob=html_blob,
        stats=stats.as_dict(),
        task_id=task_id,
    )

    result: dict[str, Any] = {
        "ok": True,
        "digest_id": digest_id,
        "session": session,
        "generated_at": generated_at,
        "stats": stats.as_dict(),
        "window": {"since_hours": since_hours, "scanned_total": total},
    }

    if task_id is not None:
        await tm.update_task_safe(
            task_id,
            status="succeeded",
            progress=1.0,
            result=result,
            completed_at=time.time(),
            finished_at=_utcnow_iso(),
        )
    return result


async def evaluate_radar(
    tm: FinpulseTaskManager,
    *,
    rules_text: str,
    since_hours: int = 24,
    limit: int = 100,
    min_score: float | None = None,
) -> dict[str, Any]:
    """Compile ``rules_text`` into a :class:`FrequencyMatcher` and run
    it over recent articles. Purely read-only — used by the Radar tab's
    preview and by :func:`run_hot_radar` below.

    Returns a dict with ``hits`` (articles that matched) and ``meta``
    (compiled rule counts / window) so the UI can render a rule-parse
    error banner without falling through to 500.
    """
    try:
        matcher = compile_matcher(rules_text or "")
    except Exception as exc:  # noqa: BLE001 — DSL error boundary
        logger.warning("radar rule compile failed: %s", exc)
        return {
            "ok": False,
            "error": "rule_compile_failed",
            "error_detail": str(exc),
            "hits": [],
            "meta": {"groups": 0, "filters": 0},
        }

    since_hours = max(1, min(int(since_hours), 168))
    limit = max(1, min(int(limit), 500))
    now = datetime.now(timezone.utc)
    since = datetime.fromtimestamp(
        now.timestamp() - since_hours * 3600, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    rows, _total = await tm.list_articles(
        since=since,
        min_score=min_score,
        sort="time",
        limit=limit,
        offset=0,
    )

    hits: list[dict[str, Any]] = []
    for row in rows:
        title = row.get("title") or ""
        if not matcher.match(title):
            continue
        terms = matcher.matched_terms(title)
        hits.append(
            {
                "id": row.get("id"),
                "source_id": row.get("source_id"),
                "title": title,
                "url": row.get("url"),
                "fetched_at": row.get("fetched_at"),
                "published_at": row.get("published_at"),
                "ai_score": row.get("ai_score"),
                "matched_terms": terms,
            }
        )

    return {
        "ok": True,
        "hits": hits,
        "meta": {
            "groups": len(matcher.rules.groups),
            "filters": len(matcher.rules.filter_words),
            "global_filters": len(matcher.rules.global_filters),
            "window_hours": since_hours,
            "scanned": len(rows),
            "matched": len(hits),
        },
    }


def _radar_markdown(
    *, header: str | None, hits: list[dict[str, Any]], limit: int = 20
) -> str:
    """Render radar hits as the compact markdown the dispatcher will
    push over IM. Truncates above ``limit`` so a runaway rule doesn't
    silently spam a 200-line payload — the iframe in the UI still
    shows the full list.
    """
    lines: list[str] = []
    if header:
        lines.append(header.rstrip())
        lines.append("")
    trimmed = list(hits[:limit])
    for i, hit in enumerate(trimmed, start=1):
        title = (hit.get("title") or "").strip()
        url = (hit.get("url") or "").strip()
        src = (hit.get("source_id") or "").strip()
        score = hit.get("ai_score")
        terms = hit.get("matched_terms") or []
        score_suffix = (
            f" · score {float(score):.1f}"
            if isinstance(score, (int, float))
            else ""
        )
        term_suffix = f" · {' '.join(f'[{t}]' for t in terms[:4])}" if terms else ""
        if url:
            lines.append(f"{i}. [{title}]({url}) · {src}{score_suffix}{term_suffix}")
        else:
            lines.append(f"{i}. {title} · {src}{score_suffix}{term_suffix}")
    if len(hits) > limit:
        lines.append("")
        lines.append(f"… +{len(hits) - limit} more")
    return "\n".join(lines).rstrip() + "\n"


async def run_hot_radar(
    tm: FinpulseTaskManager,
    dispatch: DispatchService,
    *,
    rules_text: str,
    targets: list[dict[str, str]],
    since_hours: int = 24,
    limit: int = 100,
    min_score: float | None = None,
    title: str | None = None,
    cooldown_s: float = 600.0,
    task_id: str | None = None,
    dedupe_by_content: bool = True,
) -> dict[str, Any]:
    """Evaluate the rules and fan matching titles out to every target
    using :class:`DispatchService`. Cooldown keys include the radar key
    so the same ruleset can't re-fire within ``cooldown_s`` seconds.

    When no hits are found the call returns early without touching the
    dispatcher — this keeps quiet days truly quiet (important for
    chat rooms with multiple plugin tenants).
    """
    eval_result = await evaluate_radar(
        tm,
        rules_text=rules_text,
        since_hours=since_hours,
        limit=limit,
        min_score=min_score,
    )
    hits = eval_result.get("hits", [])

    dispatch_results: list[dict[str, Any]] = []
    if eval_result.get("ok") and hits:
        header = title or "📡 fin-pulse 热点雷达"
        md = _radar_markdown(header=header, hits=hits, limit=20)
        # Cooldown key derives from the header + hit set so identical
        # firings dedupe but a fresh batch of hits gets through. The
        # key is suffixed with ``channel:chat_id`` in the loop below so
        # fanning to multiple targets never self-cancels.
        key_basis = (header + "\n" + "|".join(str(h.get("id") or "") for h in hits)).encode(
            "utf-8"
        )
        base_key = "radar:" + hashlib.sha256(key_basis).hexdigest()[:8]
        for tgt in targets:
            channel = str(tgt.get("channel") or "").strip()
            chat_id = str(tgt.get("chat_id") or "").strip()
            if not channel or not chat_id:
                dispatch_results.append(
                    {
                        "ok": False,
                        "channel": channel,
                        "chat_id": chat_id,
                        "sent_chunks": 0,
                        "skipped": None,
                        "errors": ["missing_target"],
                    }
                )
                continue
            outcome = await dispatch.send(
                channel=channel,
                chat_id=chat_id,
                content=md,
                cooldown_key=f"{base_key}:{channel}:{chat_id}",
                cooldown_s=cooldown_s,
                dedupe_by_content=dedupe_by_content,
            )
            dispatch_results.append(outcome.as_dict())

    result: dict[str, Any] = {
        "ok": bool(eval_result.get("ok")),
        "hits": hits,
        "meta": eval_result.get("meta", {}),
        "dispatched": dispatch_results,
    }
    if not eval_result.get("ok"):
        result["error"] = eval_result.get("error")
        result["error_detail"] = eval_result.get("error_detail")

    if task_id is not None:
        status = "succeeded" if result["ok"] else "failed"
        await tm.update_task_safe(
            task_id,
            status=status,
            progress=1.0,
            result=result,
            completed_at=time.time(),
            finished_at=_utcnow_iso(),
        )
    return result


class FinpulsePipeline:
    """Thin wrapper that bundles the pipeline entry points for
    ``plugin.py`` to call. Keeps the plugin module free of direct
    function-import clutter.
    """

    def __init__(self, tm: FinpulseTaskManager, api: Any) -> None:
        self._tm = tm
        self._api = api

    async def ingest(
        self,
        *,
        sources: list[str] | None = None,
        since_hours: int | None = 24,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        return await ingest(
            self._tm, sources=sources, since_hours=since_hours, task_id=task_id
        )

    async def run_daily_brief(
        self,
        *,
        session: str,
        since_hours: int = 12,
        top_k: int = 20,
        lang: str = "zh",
        task_id: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        return await run_daily_brief(
            self._tm,
            session=session,
            since_hours=since_hours,
            top_k=top_k,
            lang=lang,
            task_id=task_id,
            title=title,
        )

    async def evaluate_radar(
        self,
        *,
        rules_text: str,
        since_hours: int = 24,
        limit: int = 100,
        min_score: float | None = None,
    ) -> dict[str, Any]:
        return await evaluate_radar(
            self._tm,
            rules_text=rules_text,
            since_hours=since_hours,
            limit=limit,
            min_score=min_score,
        )

    async def run_hot_radar(
        self,
        dispatch: DispatchService,
        *,
        rules_text: str,
        targets: list[dict[str, str]],
        since_hours: int = 24,
        limit: int = 100,
        min_score: float | None = None,
        title: str | None = None,
        cooldown_s: float = 600.0,
        task_id: str | None = None,
        dedupe_by_content: bool = True,
    ) -> dict[str, Any]:
        return await run_hot_radar(
            self._tm,
            dispatch,
            rules_text=rules_text,
            targets=targets,
            since_hours=since_hours,
            limit=limit,
            min_score=min_score,
            title=title,
            cooldown_s=cooldown_s,
            task_id=task_id,
            dedupe_by_content=dedupe_by_content,
        )


__all__ = [
    "FinpulsePipeline",
    "FetchReport",
    "evaluate_radar",
    "ingest",
    "run_daily_brief",
    "run_hot_radar",
]
