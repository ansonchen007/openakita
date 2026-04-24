# ruff: noqa: N999
"""fin-pulse (财经脉动) — finance news radar plugin entry.

Three canonical modes — ``daily_brief`` / ``hot_radar`` / ``ask_news`` —
are surfaced over a FastAPI router and a small set of agent tools
registered with the host Brain. Data sources (Phase 2), AI filter
(Phase 3), daily-brief rendering and host-gateway dispatch (Phase 4),
and agent-tools dispatch (Phase 5) are layered on this skeleton without
breaking the initial minimal-loadable contract:

* ``on_load`` registers the router + tool definitions, spawns an async
  bootstrap task for the SQLite schema, and logs a single status line so
  the host plugin-status panel ticks green immediately.
* ``on_unload`` cancels the bootstrap task and closes the task manager
  connection.

The skeleton is deliberately kept import-safe: modules that arrive in
later Phases are imported lazily inside ``on_load`` so the earliest
Phase-1a commit stays loadable even before Phase 1b lands.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import HTMLResponse

from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)

PLUGIN_ID = "fin-pulse"
PLUGIN_VERSION = "1.0.0"


# V1.0 canonical mode identifiers; mirrored in finpulse_models.MODES from
# Phase 1b onwards. The skeleton keeps an inline fallback so /modes
# responds even before the models module lands.
_FALLBACK_MODES: dict[str, dict[str, Any]] = {
    "daily_brief": {
        "display_zh": "早午晚报",
        "display_en": "Daily Brief",
        "sessions": ("morning", "noon", "evening"),
    },
    "hot_radar": {
        "display_zh": "热点雷达",
        "display_en": "Hot Radar",
    },
    "ask_news": {
        "display_zh": "Agent 问询",
        "display_en": "Ask News",
    },
}


class Plugin(PluginBase):
    """fin-pulse plugin entry — see module docstring for lifecycle shape."""

    # ── Lifecycle ────────────────────────────────────────────────────

    def on_load(self, api: PluginAPI) -> None:
        # Step 1 — cache handle + per-plugin data dir.
        self._api = api
        host_data_dir = api.get_data_dir()
        if host_data_dir is None:
            api.log(
                "data.own permission missing — fin-pulse will run in "
                "degraded read-only mode; task manager will not open.",
                "error",
            )
            self._data_dir: Path | None = None
        else:
            self._data_dir = Path(host_data_dir) / "fin_pulse"
            self._data_dir.mkdir(parents=True, exist_ok=True)

        # Step 2 — task manager + pipeline (Phase 1b / 2a). Imported lazily
        # so a missing module in a half-applied branch still yields a
        # degraded-but-loadable plugin.
        self._tm: Any | None = None
        self._pipeline: Any | None = None
        self._dispatch: Any | None = None
        self._init_task: asyncio.Task | None = None
        if self._data_dir is not None:
            try:
                from finpulse_task_manager import FinpulseTaskManager  # type: ignore

                self._tm = FinpulseTaskManager(self._data_dir / "finpulse.sqlite")
            except ImportError:
                api.log(
                    "finpulse_task_manager not yet available — skeleton "
                    "skipping DB bootstrap until Phase 1b lands.",
                    "debug",
                )
                self._tm = None
            if self._tm is not None:
                try:
                    from finpulse_pipeline import FinpulsePipeline  # type: ignore

                    self._pipeline = FinpulsePipeline(self._tm, api)
                except ImportError:
                    api.log(
                        "finpulse_pipeline not yet available — ingest "
                        "routes will return 503 until Phase 2a lands.",
                        "debug",
                    )
                    self._pipeline = None
        # Dispatch service (Phase 4b) — pure over-the-gateway wrapper,
        # works even when the task manager is None so /dispatch/send can
        # still probe an IM adapter from the plugin UI for smoke tests.
        try:
            from finpulse_dispatch import DispatchService  # type: ignore

            self._dispatch = DispatchService(api)
        except ImportError:
            api.log(
                "finpulse_dispatch not yet available — hot_radar push "
                "routes will return 503 until Phase 4b lands.",
                "debug",
            )
            self._dispatch = None

        # Step 3 — FastAPI router (21 routes eventually; the skeleton
        # registers the read-only /health, /modes and /config endpoints
        # so the loader contract is satisfied immediately).
        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        # Step 4 — register agent tools (7 tools — see plugin.json
        # provides.tools). The handler routes into the query service
        # from Phase 5; Phase 1a stubs it with a ``not_implemented``
        # envelope so the host never sees a hard exception.
        api.register_tools(self._tool_definitions(), handler=self._handle_tool)

        # Step 5 — async bootstrap (SQLite schema seeding). Silent no-op
        # when Phase 1b has not landed yet.
        if self._tm is not None:
            self._init_task = api.spawn_task(
                self._async_init(), name=f"{PLUGIN_ID}:init"
            )

        # Step 6 — log so the host status panel ticks green immediately.
        api.log(
            f"fin-pulse plugin loaded (v{PLUGIN_VERSION}, "
            f"{len(self._tool_definitions())} tools)"
        )

    async def _async_init(self) -> None:
        try:
            if self._tm is not None:
                await self._tm.init()
        except Exception as exc:  # noqa: BLE001 — top-level bootstrap
            logger.error("fin-pulse task manager init failed: %s", exc)
            raise

    async def on_unload(self) -> None:
        if self._init_task is not None and not self._init_task.done():
            self._init_task.cancel()
            try:
                await self._init_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.warning("fin-pulse init task drain error: %s", exc)
        if self._tm is not None:
            try:
                await self._tm.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("fin-pulse task manager close error: %s", exc)

    # ── Agent tools (Phase 5 fills in the body) ─────────────────────

    def _tool_definitions(self) -> list[dict]:
        """Seven tools exposed to the host Brain — keep in lockstep with
        ``plugin.json`` ``provides.tools``. Phase 5 wires the handler
        into ``finpulse_services.query.*``; Phase 1a returns a stub
        envelope so the host never sees a hard exception.
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": "fin_pulse_create",
                    "description": "Create a fin-pulse task (ingest / daily_brief / hot_radar).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "mode": {
                                "type": "string",
                                "enum": ["ingest", "daily_brief", "hot_radar"],
                            },
                            "params": {"type": "object"},
                        },
                        "required": ["mode"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fin_pulse_status",
                    "description": "Inspect a fin-pulse task by id.",
                    "parameters": {
                        "type": "object",
                        "properties": {"task_id": {"type": "string"}},
                        "required": ["task_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fin_pulse_list",
                    "description": "List recent fin-pulse tasks.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "mode": {"type": "string"},
                            "status": {"type": "string"},
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 200,
                                "default": 50,
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fin_pulse_cancel",
                    "description": "Cancel a running fin-pulse task.",
                    "parameters": {
                        "type": "object",
                        "properties": {"task_id": {"type": "string"}},
                        "required": ["task_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fin_pulse_settings_get",
                    "description": "Read fin-pulse configuration (webhook / api_key redacted).",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fin_pulse_settings_set",
                    "description": "Write fin-pulse configuration values.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "updates": {
                                "type": "object",
                                "description": "Flat string map of config keys to values.",
                            }
                        },
                        "required": ["updates"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fin_pulse_search_news",
                    "description": "Search finance news by keyword, source, or date range.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "q": {
                                "type": "string",
                                "description": "Keyword; supports + must and ! exclude syntax.",
                            },
                            "source_id": {
                                "type": "string",
                                "description": "Restrict to one source.",
                            },
                            "days": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 90,
                                "default": 1,
                            },
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 200,
                                "default": 50,
                            },
                            "min_score": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 10,
                            },
                        },
                    },
                },
            },
        ]

    def _handle_tool(self, name: str, args: dict, **_: Any) -> Any:
        """Stub dispatch for Phase 1a — Phase 5 replaces this with a
        router into ``finpulse_services.query.*``.
        """
        return {
            "ok": False,
            "error": "not_implemented",
            "hint": "fin-pulse agent tools land in Phase 5.",
            "tool": name,
        }

    # ── FastAPI routes ──────────────────────────────────────────────

    def _register_routes(self, router: APIRouter) -> None:
        """Register the Phase-1 read-only surface so the host health page
        can confirm the plugin is alive even before later Phases land.
        """

        @router.get("/health")
        async def health() -> dict[str, Any]:
            return {
                "ok": True,
                "plugin_id": PLUGIN_ID,
                "version": PLUGIN_VERSION,
                "phase": "skeleton",
                "db_ready": self._tm is not None
                and getattr(self._tm, "_db", None) is not None,
                "data_dir": str(self._data_dir) if self._data_dir else None,
                "timestamp": time.time(),
            }

        @router.get("/modes")
        async def modes() -> dict[str, Any]:
            try:
                from finpulse_models import MODES  # type: ignore

                return {"modes": MODES}
            except ImportError:
                return {"modes": _FALLBACK_MODES}

        @router.get("/config")
        async def get_config() -> dict[str, Any]:
            if self._tm is None:
                return {"ok": False, "error": "task_manager_unavailable", "config": {}}
            try:
                cfg = await self._tm.get_all_config()
                return {"ok": True, "config": _redact_secrets(cfg)}
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=500, detail=str(exc)) from exc

        @router.put("/config")
        async def put_config(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
            if self._tm is None:
                raise HTTPException(status_code=503, detail="task_manager_unavailable")
            updates = payload.get("updates")
            if not isinstance(updates, dict):
                raise HTTPException(status_code=400, detail="updates must be an object")
            flat: dict[str, str] = {}
            for k, v in updates.items():
                if not isinstance(k, str):
                    continue
                flat[k] = v if isinstance(v, str) else str(v)
            await self._tm.set_configs(flat)
            return {"ok": True, "applied": sorted(flat.keys())}

        @router.get("/tasks")
        async def list_tasks(
            mode: str | None = Query(None),
            status: str | None = Query(None),
            offset: int = Query(0, ge=0),
            limit: int = Query(50, ge=1, le=200),
        ) -> dict[str, Any]:
            if self._tm is None:
                raise HTTPException(status_code=503, detail="task_manager_unavailable")
            items, total = await self._tm.list_tasks(
                mode=mode, status=status, offset=offset, limit=limit
            )
            return {"ok": True, "items": items, "total": total}

        @router.get("/tasks/{task_id}")
        async def get_task(task_id: str) -> dict[str, Any]:
            if self._tm is None:
                raise HTTPException(status_code=503, detail="task_manager_unavailable")
            row = await self._tm.get_task(task_id)
            if row is None:
                raise HTTPException(status_code=404, detail="not_found")
            return {"ok": True, "task": row}

        @router.post("/tasks/{task_id}/cancel")
        async def cancel_task(task_id: str) -> dict[str, Any]:
            if self._tm is None:
                raise HTTPException(status_code=503, detail="task_manager_unavailable")
            await self._tm.update_task_safe(task_id, status="canceled")
            return {"ok": True, "task_id": task_id, "status": "canceled"}

        @router.post("/ingest")
        async def ingest_all(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
            if self._tm is None or self._pipeline is None:
                raise HTTPException(status_code=503, detail="pipeline_unavailable")
            sources = payload.get("sources") if isinstance(payload, dict) else None
            since_hours = payload.get("since_hours") if isinstance(payload, dict) else 24
            task = await self._tm.create_task(
                mode="ingest",
                params={"sources": sources, "since_hours": since_hours},
                status="running",
            )
            try:
                summary = await self._pipeline.ingest(
                    sources=sources,
                    since_hours=int(since_hours) if since_hours is not None else 24,
                    task_id=task["id"],
                )
                return {"ok": True, "task_id": task["id"], "summary": summary}
            except Exception as exc:  # noqa: BLE001
                from finpulse_errors import map_exception  # lazy — may be absent

                kind, msg, hints = map_exception(exc)
                await self._tm.update_task_safe(
                    task["id"],
                    status="failed",
                    error_kind=kind,
                    error_message=msg,
                    error_hints=hints,
                )
                raise HTTPException(status_code=500, detail=msg) from exc

        @router.post("/ingest/source/{source_id}")
        async def ingest_source(source_id: str) -> dict[str, Any]:
            if self._tm is None or self._pipeline is None:
                raise HTTPException(status_code=503, detail="pipeline_unavailable")
            task = await self._tm.create_task(
                mode="ingest",
                params={"sources": [source_id], "since_hours": 24},
                status="running",
            )
            summary = await self._pipeline.ingest(
                sources=[source_id], since_hours=24, task_id=task["id"]
            )
            return {"ok": True, "task_id": task["id"], "summary": summary}

        @router.get("/articles")
        async def list_articles(
            q: str | None = Query(None),
            source_id: str | None = Query(None),
            since: str | None = Query(None),
            min_score: float | None = Query(None),
            sort: str = Query("time"),
            offset: int = Query(0, ge=0),
            limit: int = Query(50, ge=1, le=200),
        ) -> dict[str, Any]:
            if self._tm is None:
                raise HTTPException(status_code=503, detail="task_manager_unavailable")
            items, total = await self._tm.list_articles(
                source_id=source_id,
                since=since,
                q=q,
                min_score=min_score,
                sort=sort,
                offset=offset,
                limit=limit,
            )
            return {"ok": True, "items": items, "total": total}

        @router.get("/articles/{article_id}")
        async def get_article(article_id: str) -> dict[str, Any]:
            if self._tm is None:
                raise HTTPException(status_code=503, detail="task_manager_unavailable")
            row = await self._tm.get_article(article_id)
            if row is None:
                raise HTTPException(status_code=404, detail="not_found")
            return {"ok": True, "article": row}

        @router.post("/digest/run")
        async def run_digest(
            payload: dict[str, Any] = Body(default={}),
        ) -> dict[str, Any]:
            if self._tm is None or self._pipeline is None:
                raise HTTPException(status_code=503, detail="pipeline_unavailable")
            session = payload.get("session") if isinstance(payload, dict) else None
            if session not in {"morning", "noon", "evening"}:
                raise HTTPException(
                    status_code=400,
                    detail="session must be one of morning|noon|evening",
                )
            since_hours = payload.get("since_hours", 12)
            top_k = payload.get("top_k", 20)
            lang = payload.get("lang", "zh") or "zh"
            try:
                since_hours_int = max(1, min(int(since_hours), 72))
                top_k_int = max(1, min(int(top_k), 60))
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail=f"invalid numeric arg: {exc}"
                ) from exc
            task = await self._tm.create_task(
                mode="daily_brief",
                params={
                    "session": session,
                    "since_hours": since_hours_int,
                    "top_k": top_k_int,
                    "lang": lang,
                },
                status="running",
            )
            try:
                result = await self._pipeline.run_daily_brief(
                    session=session,
                    since_hours=since_hours_int,
                    top_k=top_k_int,
                    lang=lang,
                    task_id=task["id"],
                )
                return {"ok": True, "task_id": task["id"], "digest": result}
            except Exception as exc:  # noqa: BLE001
                from finpulse_errors import map_exception

                kind, msg, hints = map_exception(exc)
                await self._tm.update_task_safe(
                    task["id"],
                    status="failed",
                    error_kind=kind,
                    error_message=msg,
                    error_hints=hints,
                )
                raise HTTPException(status_code=500, detail=msg) from exc

        @router.get("/digests")
        async def list_digests(
            session: str | None = Query(None),
            offset: int = Query(0, ge=0),
            limit: int = Query(50, ge=1, le=200),
        ) -> dict[str, Any]:
            if self._tm is None:
                raise HTTPException(status_code=503, detail="task_manager_unavailable")
            items, total = await self._tm.list_digests(
                session=session, offset=offset, limit=limit
            )
            for item in items:
                item.pop("html_blob", None)
                item.pop("markdown_blob", None)
            return {"ok": True, "items": items, "total": total}

        @router.get("/digests/{digest_id}")
        async def get_digest(digest_id: str) -> dict[str, Any]:
            if self._tm is None:
                raise HTTPException(status_code=503, detail="task_manager_unavailable")
            row = await self._tm.get_digest(digest_id)
            if row is None:
                raise HTTPException(status_code=404, detail="not_found")
            return {"ok": True, "digest": row}

        @router.get("/digests/{digest_id}/html", response_class=HTMLResponse)
        async def get_digest_html(digest_id: str) -> HTMLResponse:
            if self._tm is None:
                raise HTTPException(status_code=503, detail="task_manager_unavailable")
            row = await self._tm.get_digest(digest_id)
            if row is None:
                raise HTTPException(status_code=404, detail="not_found")
            html_blob = row.get("html_blob") or ""
            return HTMLResponse(content=html_blob, media_type="text/html")

        # ── Hot radar ───────────────────────────────────────────────

        @router.post("/radar/evaluate")
        async def radar_evaluate(
            payload: dict[str, Any] = Body(default={}),
        ) -> dict[str, Any]:
            if self._tm is None or self._pipeline is None:
                raise HTTPException(status_code=503, detail="pipeline_unavailable")
            rules_text = payload.get("rules_text") if isinstance(payload, dict) else None
            if not isinstance(rules_text, str):
                raise HTTPException(status_code=400, detail="rules_text must be a string")
            since_hours = int(payload.get("since_hours", 24) or 24)
            limit = int(payload.get("limit", 100) or 100)
            min_score = payload.get("min_score")
            min_score_f = float(min_score) if min_score is not None else None
            return await self._pipeline.evaluate_radar(
                rules_text=rules_text,
                since_hours=max(1, min(since_hours, 168)),
                limit=max(1, min(limit, 500)),
                min_score=min_score_f,
            )

        @router.post("/hot_radar/run")
        async def hot_radar_run(
            payload: dict[str, Any] = Body(default={}),
        ) -> dict[str, Any]:
            if self._tm is None or self._pipeline is None:
                raise HTTPException(status_code=503, detail="pipeline_unavailable")
            if self._dispatch is None:
                raise HTTPException(status_code=503, detail="dispatch_unavailable")
            rules_text = payload.get("rules_text") if isinstance(payload, dict) else None
            if not isinstance(rules_text, str) or not rules_text.strip():
                raise HTTPException(status_code=400, detail="rules_text must be non-empty")
            targets = payload.get("targets") or []
            if not isinstance(targets, list) or not targets:
                raise HTTPException(status_code=400, detail="targets must be a non-empty list")
            clean_targets: list[dict[str, str]] = []
            for t in targets:
                if not isinstance(t, dict):
                    continue
                ch = str(t.get("channel") or "").strip()
                ci = str(t.get("chat_id") or "").strip()
                if ch and ci:
                    clean_targets.append({"channel": ch, "chat_id": ci})
            if not clean_targets:
                raise HTTPException(status_code=400, detail="no usable targets (channel/chat_id)")
            since_hours = max(1, min(int(payload.get("since_hours", 24) or 24), 168))
            limit = max(1, min(int(payload.get("limit", 100) or 100), 500))
            min_score = payload.get("min_score")
            min_score_f = float(min_score) if min_score is not None else None
            cooldown_s = float(payload.get("cooldown_s", 600) or 600)
            title = payload.get("title")
            task = await self._tm.create_task(
                mode="hot_radar",
                params={
                    "targets": clean_targets,
                    "since_hours": since_hours,
                    "limit": limit,
                    "min_score": min_score_f,
                    "cooldown_s": cooldown_s,
                    "title": title,
                },
                status="running",
            )
            try:
                result = await self._pipeline.run_hot_radar(
                    self._dispatch,
                    rules_text=rules_text,
                    targets=clean_targets,
                    since_hours=since_hours,
                    limit=limit,
                    min_score=min_score_f,
                    title=title if isinstance(title, str) else None,
                    cooldown_s=cooldown_s,
                    task_id=task["id"],
                )
                return {"ok": True, "task_id": task["id"], "result": result}
            except Exception as exc:  # noqa: BLE001
                from finpulse_errors import map_exception

                kind, msg, hints = map_exception(exc)
                await self._tm.update_task_safe(
                    task["id"],
                    status="failed",
                    error_kind=kind,
                    error_message=msg,
                    error_hints=hints,
                )
                raise HTTPException(status_code=500, detail=msg) from exc

        @router.post("/dispatch/send")
        async def dispatch_send(
            payload: dict[str, Any] = Body(default={}),
        ) -> dict[str, Any]:
            if self._dispatch is None:
                raise HTTPException(status_code=503, detail="dispatch_unavailable")
            channel = str(payload.get("channel") or "").strip()
            chat_id = str(payload.get("chat_id") or "").strip()
            content = payload.get("content")
            if not channel or not chat_id:
                raise HTTPException(status_code=400, detail="channel and chat_id are required")
            if not isinstance(content, str):
                raise HTTPException(status_code=400, detail="content must be a string")
            cooldown_key = payload.get("cooldown_key")
            cooldown_s = float(payload.get("cooldown_s", 0) or 0)
            dedupe = bool(payload.get("dedupe_by_content"))
            header = str(payload.get("header") or "")
            result = await self._dispatch.send(
                channel=channel,
                chat_id=chat_id,
                content=content,
                cooldown_key=cooldown_key if isinstance(cooldown_key, str) else None,
                cooldown_s=cooldown_s,
                dedupe_by_content=dedupe,
                header=header,
            )
            return {"ok": result.ok, "dispatch": result.as_dict()}

        @router.get("/available-channels")
        async def available_channels() -> dict[str, Any]:
            """Expose the list of adapter names the host gateway
            currently carries so the Settings UI can render a channel
            picker without hard-coding the 7-strong roster. Falls back
            to a probe list when the gateway hides ``_adapters``.
            """
            host = getattr(self._api, "_host", None) or {}
            gateway = host.get("gateway") if isinstance(host, dict) else None
            if gateway is None:
                return {"ok": True, "channels": []}
            names: list[str] = []
            adapters = getattr(gateway, "_adapters", None)
            if isinstance(adapters, dict):
                names = [str(k) for k in adapters.keys()]
            else:
                probe = [
                    "feishu",
                    "wework",
                    "wework_ws",
                    "dingtalk",
                    "telegram",
                    "onebot",
                    "qqbot",
                    "wechat",
                    "email",
                ]
                get = getattr(gateway, "get_adapter", None)
                if callable(get):
                    for name in probe:
                        try:
                            if get(name) is not None:
                                names.append(name)
                        except Exception:  # noqa: BLE001 — probe only
                            continue
            return {"ok": True, "channels": names}


# ── Utilities ────────────────────────────────────────────────────────


_SECRET_KEYS = (
    "api_key",
    "token",
    "webhook",
    "secret",
    "password",
)


def _redact_secrets(cfg: dict[str, str]) -> dict[str, str]:
    """Mask any config value whose key contains a secret-looking suffix."""
    redacted: dict[str, str] = {}
    for k, v in cfg.items():
        if any(s in k.lower() for s in _SECRET_KEYS) and v:
            redacted[k] = "***"
        else:
            redacted[k] = v
    return redacted


__all__ = ["Plugin", "PLUGIN_ID", "PLUGIN_VERSION"]
