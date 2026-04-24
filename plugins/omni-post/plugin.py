"""omni-post — cross-platform publishing plugin entry point.

Wires up:

- :class:`OmniPostTaskManager` — sqlite3-backed CRUD for tasks, assets,
  accounts, schedules, selector-health, etc.
- :class:`CookiePool` — Fernet-encrypted cookie jar (issue #207 fix).
- :class:`UploadPipeline` — chunked/resumable upload + MD5 dedup +
  ffprobe metadata + ffmpeg thumbnails.
- :class:`PlaywrightEngine` — main publishing engine, driven by
  per-platform JSON selectors.
- :func:`run_publish_task` — the retry + auto-submit-degrade loop that
  calls ``engine.run_task`` and updates DB rows.

Routes (22+):

  Publish / tasks     POST /publish         POST /publish/dry-run
                      POST /schedule        GET  /tasks
                      GET  /tasks/{id}      POST /tasks/{id}/cancel
                      POST /tasks/{id}/retry
  Upload / assets     POST /upload/init     PUT  /upload/chunk
                      POST /upload/finalize GET  /assets
                      DELETE /assets/{id}
  Accounts            GET  /accounts        POST /accounts
                      POST /accounts/{id}/refresh
                      DELETE /accounts/{id}
  System              GET  /catalog         GET  /settings
                      PUT  /settings        GET  /healthz
                      GET  /stats           POST /asset-bus/pull

Tools (14):

  omni_post_publish / schedule / cancel / retry / list_tasks / get_task /
  list_accounts / add_account / remove_account / refresh_account /
  list_assets / delete_asset / pull_from_asset_bus / export_report

UI events broadcast:

  task_update / task_retry / upload_progress / upload_completed /
  account_added / account_refreshed / selector_alert / asset_bus_updated

All paths are rooted under ``/api/plugins/omni-post/``.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from datetime import UTC
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from omni_post_adapters import load_selector_bundle
from omni_post_assets import UploadPipeline
from omni_post_cookies import CookieEncryptError, CookiePool
from omni_post_engine_pw import PlaywrightEngine
from omni_post_models import (
    DEFAULT_SETTINGS,
    ERROR_HINTS,
    PLATFORMS,
    PLATFORMS_BY_ID,
    AccountCreateRequest,
    ErrorKind,
    OmniPostError,
    PublishPayload,
    PublishRequest,
    ScheduleRequest,
    SettingsUpdateRequest,
    build_catalog,
)
from omni_post_pipeline import (
    PipelineDeps,
    check_account_quota,
    run_publish_task,
)
from omni_post_task_manager import OmniPostTaskManager
from pydantic import BaseModel, ConfigDict
from starlette.responses import FileResponse

from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)

PLUGIN_ID = "omni-post"


class OmniPostPlugin(PluginBase):
    """Single-class entry point — builds the router, wires registrations.

    The host calls :meth:`on_load` once per plugin activation. We do all
    eager I/O here (open sqlite, create dirs, register API routes) so
    subsequent HTTP traffic never pays a setup cost.
    """

    def __init__(self) -> None:
        self._api: PluginAPI | None = None
        self._data_dir: Path | None = None
        self._tm: OmniPostTaskManager | None = None
        self._cookie_pool: CookiePool | None = None
        self._upload: UploadPipeline | None = None
        self._engine: PlaywrightEngine | None = None
        self._settings: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self._selectors_dir: Path | None = None
        self._screenshot_dir: Path | None = None
        self._uploads_dir: Path | None = None
        self._active_tasks: set[asyncio.Task[Any]] = set()

    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = api.get_data_dir()
        if data_dir is None:
            api.log("data.own permission denied; plugin disabled", "error")
            return
        self._data_dir = Path(data_dir)
        self._selectors_dir = Path(__file__).parent / "omni_post_selectors"
        self._uploads_dir = self._data_dir / "uploads"
        thumbs_dir = self._data_dir / "thumbs"
        self._screenshot_dir = self._data_dir / "screenshots"
        for p in (
            self._uploads_dir,
            thumbs_dir,
            self._screenshot_dir,
            self._data_dir / "user_data",
        ):
            p.mkdir(parents=True, exist_ok=True)

        self._settings = {**DEFAULT_SETTINGS, **(api.get_config() or {})}

        db_path = self._data_dir / "omni-post.db"
        self._tm = OmniPostTaskManager(db_path)
        self._cookie_pool = CookiePool(self._data_dir)
        self._upload = UploadPipeline(
            uploads_dir=self._uploads_dir,
            thumbs_dir=thumbs_dir,
            task_manager=self._tm,
            chunk_bytes=int(self._settings.get("upload_chunk_bytes", 5 * 1024 * 1024)),
        )
        self._engine = PlaywrightEngine(
            user_data_root=self._data_dir / "user_data",
            selectors_dir=self._selectors_dir,
            screenshot_dir=self._screenshot_dir,
            settings=self._settings,
        )

        api.spawn_task(self._async_bootstrap(), name="omni-post:bootstrap")

        router = self._build_router()
        api.register_api_routes(router)
        api.register_tools(_build_tool_definitions(), handler=self._handle_tool)

        api.log(f"omni-post loaded (data_dir={self._data_dir})")

    async def _async_bootstrap(self) -> None:
        assert self._tm is not None
        await self._tm.init()
        # Seed platform metadata on first load.
        for spec in PLATFORMS:
            await self._tm.upsert_platform(
                platform_id=spec.id,
                display_name=spec.display_name_zh,
                supported_kinds=list(spec.supported_kinds),
                selector_version="1.0.0",
                engine_preferred=spec.engine_preferred,
                notes=spec.notes or None,
            )
        if self._upload is not None:
            self._upload.sweep_stale_uploads(older_than_seconds=3600)

    def on_unload(self) -> Any:
        async def _close() -> None:
            for t in list(self._active_tasks):
                if not t.done():
                    t.cancel()
            if self._engine is not None:
                await self._engine.close()
            if self._tm is not None:
                await self._tm.close()

        return _close()

    # ── Router ────────────────────────────────────────────────────

    def _build_router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/healthz")
        async def healthz() -> dict:
            return {"ok": True, "plugin": PLUGIN_ID}

        @router.get("/catalog")
        async def catalog() -> dict:
            return build_catalog()

        @router.get("/settings")
        async def get_settings() -> dict:
            return dict(self._settings)

        @router.put("/settings")
        async def update_settings(body: SettingsUpdateRequest) -> dict:
            updates = body.model_dump(exclude_none=True)
            self._settings.update(updates)
            if self._api is not None:
                self._api.set_config(self._settings)
            if self._upload is not None and "upload_chunk_bytes" in updates:
                self._upload._chunk_bytes = int(updates["upload_chunk_bytes"])  # noqa: SLF001
            return dict(self._settings)

        @router.get("/stats")
        async def stats() -> dict:
            if self._tm is None:
                raise HTTPException(503, "not initialized")
            return await self._tm.stats()

        # Tasks ─────────────────────────────────────────────────────

        @router.post("/publish")
        async def publish(body: PublishRequest) -> dict:
            return await self._handle_publish(body)

        @router.post("/schedule")
        async def schedule(body: ScheduleRequest) -> dict:
            return await self._handle_publish(body, is_scheduled=True)

        @router.post("/publish/dry-run")
        async def publish_dry_run(body: PublishRequest) -> dict:
            self._require_tm()
            assert self._tm is not None
            issues: list[str] = []
            if body.asset_id:
                asset = await self._tm.get_asset(body.asset_id)
                if asset is None:
                    issues.append(f"asset {body.asset_id} not found")
            for pid in body.platforms:
                if pid not in PLATFORMS_BY_ID:
                    issues.append(f"unknown platform {pid}")
            for aid in body.account_ids:
                acc = await self._tm.get_account(aid)
                if acc is None:
                    issues.append(f"account {aid} not found")
                    continue
                quota = await check_account_quota(self._deps(), aid)
                if quota["daily"]["used"] >= quota["daily"]["limit"]:
                    issues.append(f"account {aid} daily quota reached")
            return {
                "ok": not issues,
                "issues": issues,
                "matrix": [
                    {"platform": p, "account_id": a}
                    for p in body.platforms
                    for a in body.account_ids
                ],
            }

        @router.get("/tasks")
        async def list_tasks(
            status: str | None = None,
            platform: str | None = None,
            account_id: str | None = None,
            asset_id: str | None = None,
            limit: int = 200,
        ) -> dict:
            self._require_tm()
            assert self._tm is not None
            rows = await self._tm.list_tasks(
                status=status,
                platform=platform,
                account_id=account_id,
                asset_id=asset_id,
                limit=limit,
            )
            return {"tasks": rows}

        @router.get("/tasks/{task_id}")
        async def get_task(task_id: str) -> dict:
            self._require_tm()
            assert self._tm is not None
            row = await self._tm.get_task(task_id)
            if row is None:
                raise HTTPException(404, "task not found")
            return row

        @router.post("/tasks/{task_id}/cancel")
        async def cancel_task(task_id: str) -> dict:
            self._require_tm()
            assert self._tm is not None
            await self._tm.update_task_safe(task_id, {"status": "cancelled"})
            if self._api is not None:
                self._api.broadcast_ui_event(
                    "task_update",
                    {"task_id": task_id, "status": "cancelled"},
                )
            return {"ok": True}

        @router.post("/tasks/{task_id}/retry")
        async def retry_task(task_id: str) -> dict:
            self._require_tm()
            assert self._tm is not None
            row = await self._tm.get_task(task_id)
            if row is None:
                raise HTTPException(404, "task not found")
            await self._tm.update_task_safe(
                task_id,
                {"status": "pending", "error_kind": None, "error_hint_i18n": None},
            )
            self._spawn(run_publish_task(self._deps(), task_id))
            return {"ok": True}

        # Upload ────────────────────────────────────────────────────

        @router.post("/upload/init")
        async def upload_init(body: _UploadInitBody) -> dict:
            self._require_upload()
            assert self._upload is not None
            return await self._upload.init_upload(
                filename=body.filename,
                filesize=body.filesize,
                kind=body.kind,
                md5_hint=body.md5_hint,
            )

        @router.put("/upload/chunk")
        async def upload_chunk(
            upload_id: str = Form(...),
            chunk_index: int = Form(...),
            chunk: UploadFile = File(...),
        ) -> dict:
            self._require_upload()
            assert self._upload is not None
            payload = await chunk.read()
            res = self._upload.write_chunk(
                upload_id=upload_id,
                chunk_index=chunk_index,
                payload=payload,
            )
            if self._api is not None:
                self._api.broadcast_ui_event(
                    "upload_progress",
                    {
                        "upload_id": upload_id,
                        "received": res["received"],
                        "total": res["total"],
                    },
                )
            return res

        @router.post("/upload/finalize")
        async def upload_finalize(body: _UploadFinalizeBody) -> dict:
            self._require_upload()
            assert self._upload is not None
            res = await self._upload.finalize(upload_id=body.upload_id, tags=body.tags)
            if self._api is not None:
                self._api.broadcast_ui_event(
                    "upload_completed",
                    {
                        "upload_id": body.upload_id,
                        "asset_id": res["asset_id"],
                        "deduped": res.get("deduped", False),
                    },
                )
            return res

        @router.get("/assets")
        async def list_assets(kind: str | None = None, limit: int = 500) -> dict:
            self._require_tm()
            assert self._tm is not None
            return {"assets": await self._tm.list_assets(kind=kind, limit=limit)}

        @router.delete("/assets/{asset_id}")
        async def delete_asset(asset_id: str) -> dict:
            self._require_tm()
            assert self._tm is not None
            removed = await self._tm.delete_asset(asset_id)
            return {"ok": removed}

        @router.get("/thumbs/{filename:path}")
        async def serve_thumb(filename: str) -> FileResponse:
            assert self._data_dir is not None
            p = (self._data_dir / "thumbs" / filename).resolve()
            base = (self._data_dir / "thumbs").resolve()
            try:
                p.relative_to(base)
            except ValueError as e:
                raise HTTPException(403, "forbidden") from e
            if not p.is_file():
                raise HTTPException(404, "not found")
            return FileResponse(str(p))

        # Accounts ──────────────────────────────────────────────────

        @router.get("/accounts")
        async def list_accounts(platform: str | None = None) -> dict:
            self._require_tm()
            assert self._tm is not None
            rows = await self._tm.list_accounts(platform=platform)
            for row in rows:
                row.pop("cookie_cipher", None)
            return {"accounts": rows}

        @router.post("/accounts")
        async def create_account(body: AccountCreateRequest) -> dict:
            self._require_tm()
            self._require_cookie_pool()
            assert self._tm is not None
            assert self._cookie_pool is not None
            if body.platform not in PLATFORMS_BY_ID:
                raise HTTPException(422, f"unknown platform {body.platform}")
            cipher = self._cookie_pool.seal(body.cookie_raw)
            acc_id = await self._tm.create_account(
                platform=body.platform,
                nickname=body.nickname,
                cookie_cipher=cipher,
                tags=body.tags,
                daily_limit=body.daily_limit,
                weekly_limit=body.weekly_limit,
                monthly_limit=body.monthly_limit,
            )
            if self._api is not None:
                self._api.broadcast_ui_event(
                    "account_added",
                    {"account_id": acc_id, "platform": body.platform},
                )
            row = await self._tm.get_account(acc_id)
            if row:
                row.pop("cookie_cipher", None)
            return row or {"id": acc_id}

        @router.post("/accounts/{account_id}/refresh")
        async def refresh_account(account_id: str) -> dict:
            self._require_tm()
            self._require_cookie_pool()
            assert self._tm is not None
            assert self._cookie_pool is not None
            account = await self._tm.get_account(account_id)
            if account is None:
                raise HTTPException(404, "account not found")
            try:
                _ = self._cookie_pool.open(account["cookie_cipher"])
                verdict = "ok"
            except CookieEncryptError:
                verdict = "cookie_expired"
            await self._tm.update_account_safe(
                account_id,
                {"health_status": verdict, "last_health_check": _now_iso()},
            )
            if self._api is not None:
                self._api.broadcast_ui_event(
                    "account_refreshed",
                    {"account_id": account_id, "health_status": verdict},
                )
            return {"account_id": account_id, "health_status": verdict}

        @router.delete("/accounts/{account_id}")
        async def delete_account(account_id: str) -> dict:
            self._require_tm()
            assert self._tm is not None
            return {"ok": await self._tm.delete_account(account_id)}

        # Asset Bus pull ────────────────────────────────────────────

        @router.post("/asset-bus/pull")
        async def pull_asset(body: _AssetBusPullBody) -> dict:
            return await self._pull_from_asset_bus(body.asset_id)

        # Selector inspection (read-only) ───────────────────────────

        @router.get("/selectors/{platform_id}")
        async def get_selector_bundle(platform_id: str) -> dict:
            if self._selectors_dir is None:
                raise HTTPException(503, "not initialized")
            try:
                return load_selector_bundle(platform_id, self._selectors_dir)
            except FileNotFoundError as e:
                raise HTTPException(404, str(e)) from e

        return router

    # ── Core publish dispatch ─────────────────────────────────────

    async def _handle_publish(self, body: PublishRequest, *, is_scheduled: bool = False) -> dict:
        self._require_tm()
        assert self._tm is not None
        if body.asset_id:
            asset = await self._tm.get_asset(body.asset_id)
            if asset is None:
                raise HTTPException(404, f"asset {body.asset_id} not found")
        for pid in body.platforms:
            if pid not in PLATFORMS_BY_ID:
                raise HTTPException(422, f"unknown platform {pid}")

        created_tasks: list[str] = []
        for pid in body.platforms:
            for aid in body.account_ids:
                acc = await self._tm.get_account(aid)
                if acc is None:
                    raise HTTPException(404, f"account {aid} not found")
                if acc["platform"] != pid:
                    # Silent skip — don't explode the whole matrix,
                    # just note the mismatch for the UI to render.
                    continue
                task_id = await self._tm.create_task(
                    platform=pid,
                    account_id=aid,
                    asset_id=body.asset_id,
                    payload=body.payload.model_dump(),
                    engine=body.engine,
                    client_trace_id=body.client_trace_id,
                    scheduled_at=body.scheduled_at,
                )
                created_tasks.append(task_id)
                if is_scheduled and body.scheduled_at:
                    await self._tm.create_schedule(
                        task_id=task_id,
                        scheduled_at=body.scheduled_at,
                        jitter_seconds=int(self._settings.get("schedule_jitter_seconds", 900)),
                    )
                    continue
                self._spawn(run_publish_task(self._deps(), task_id))
        return {"ok": True, "task_ids": created_tasks, "count": len(created_tasks)}

    async def _pull_from_asset_bus(self, asset_id: str) -> dict:
        if self._api is None or self._tm is None:
            raise HTTPException(503, "not initialized")
        if not hasattr(self._api, "consume_asset"):
            raise HTTPException(501, "asset bus unavailable in this host")
        data = await self._api.consume_asset(asset_id)
        if data is None:
            raise HTTPException(404, "asset not found or forbidden")
        source_path = (data.get("source_path") or "").strip()
        if not source_path:
            raise HTTPException(422, "source asset has no source_path")
        p = Path(source_path)
        if not p.is_file():
            raise HTTPException(422, f"source path is not a file: {source_path}")

        import hashlib

        kind = _infer_kind_from_path(p)
        md5_hasher = hashlib.md5()  # noqa: S324 - dedup only
        md5_hasher.update(p.read_bytes())
        md5 = md5_hasher.hexdigest()

        existing = await self._tm.find_asset_by_md5(md5)
        if existing is not None:
            return {"asset_id": existing["id"], "deduped": True}

        asset_id_new = await self._tm.create_asset(
            kind=kind,
            filename=p.name,
            filesize=p.stat().st_size,
            md5=md5,
            storage_path=str(p),
            source_plugin=str(data.get("plugin_id") or ""),
            source_asset_id=asset_id,
        )
        if self._api is not None:
            self._api.broadcast_ui_event(
                "asset_bus_updated",
                {
                    "asset_id": asset_id_new,
                    "source_plugin": data.get("plugin_id"),
                },
            )
        return {"asset_id": asset_id_new, "deduped": False}

    # ── Tool dispatcher ──────────────────────────────────────────

    async def _handle_tool(self, name: str, arguments: dict) -> Any:
        try:
            return await self._dispatch_tool(name, arguments)
        except OmniPostError as e:
            return {
                "error": True,
                "kind": e.kind.value,
                "message": str(e),
                "hint": ERROR_HINTS.get(e.kind.value, ERROR_HINTS["unknown"]),
            }
        except HTTPException as e:
            return {"error": True, "kind": "client", "message": e.detail}
        except Exception as e:  # noqa: BLE001
            logger.exception("tool %s failed", name)
            return {"error": True, "kind": "unknown", "message": str(e)}

    async def _dispatch_tool(self, name: str, arguments: dict) -> Any:
        if name == "omni_post_publish":
            return await self._handle_publish(PublishRequest(**arguments))
        if name == "omni_post_schedule":
            return await self._handle_publish(ScheduleRequest(**arguments), is_scheduled=True)
        if name == "omni_post_cancel":
            self._require_tm()
            assert self._tm is not None
            await self._tm.update_task_safe(arguments["task_id"], {"status": "cancelled"})
            return {"ok": True}
        if name == "omni_post_retry":
            self._require_tm()
            assert self._tm is not None
            tid = arguments["task_id"]
            row = await self._tm.get_task(tid)
            if row is None:
                raise OmniPostError(ErrorKind.NOT_FOUND, f"task {tid} not found")
            await self._tm.update_task_safe(
                tid, {"status": "pending", "error_kind": None, "error_hint_i18n": None}
            )
            self._spawn(run_publish_task(self._deps(), tid))
            return {"ok": True, "task_id": tid}
        if name == "omni_post_list_tasks":
            self._require_tm()
            assert self._tm is not None
            return {"tasks": await self._tm.list_tasks(**arguments)}
        if name == "omni_post_get_task":
            self._require_tm()
            assert self._tm is not None
            return await self._tm.get_task(arguments["task_id"]) or {}
        if name == "omni_post_list_accounts":
            self._require_tm()
            assert self._tm is not None
            rows = await self._tm.list_accounts(platform=arguments.get("platform"))
            for r in rows:
                r.pop("cookie_cipher", None)
            return {"accounts": rows}
        if name == "omni_post_add_account":
            self._require_tm()
            self._require_cookie_pool()
            body = AccountCreateRequest(**arguments)
            cipher = self._cookie_pool.seal(body.cookie_raw)  # type: ignore[union-attr]
            aid = await self._tm.create_account(  # type: ignore[union-attr]
                platform=body.platform,
                nickname=body.nickname,
                cookie_cipher=cipher,
                tags=body.tags,
                daily_limit=body.daily_limit,
                weekly_limit=body.weekly_limit,
                monthly_limit=body.monthly_limit,
            )
            return {"account_id": aid}
        if name == "omni_post_remove_account":
            self._require_tm()
            assert self._tm is not None
            return {"ok": await self._tm.delete_account(arguments["account_id"])}
        if name == "omni_post_refresh_account":
            self._require_tm()
            self._require_cookie_pool()
            assert self._tm is not None
            assert self._cookie_pool is not None
            acc = await self._tm.get_account(arguments["account_id"])
            if acc is None:
                raise OmniPostError(
                    ErrorKind.NOT_FOUND, f"account {arguments['account_id']} not found"
                )
            try:
                self._cookie_pool.open(acc["cookie_cipher"])
                verdict = "ok"
            except CookieEncryptError:
                verdict = "cookie_expired"
            await self._tm.update_account_safe(
                arguments["account_id"],
                {"health_status": verdict, "last_health_check": _now_iso()},
            )
            return {"account_id": arguments["account_id"], "health_status": verdict}
        if name == "omni_post_list_assets":
            self._require_tm()
            assert self._tm is not None
            return {
                "assets": await self._tm.list_assets(
                    kind=arguments.get("kind"), limit=int(arguments.get("limit", 500))
                )
            }
        if name == "omni_post_delete_asset":
            self._require_tm()
            assert self._tm is not None
            return {"ok": await self._tm.delete_asset(arguments["asset_id"])}
        if name == "omni_post_pull_from_asset_bus":
            return await self._pull_from_asset_bus(arguments["asset_id"])
        if name == "omni_post_export_report":
            return await self._export_report(
                since=arguments.get("since"),
                until=arguments.get("until"),
            )
        raise OmniPostError(ErrorKind.NOT_FOUND, f"unknown tool {name}")

    async def _export_report(self, since: str | None, until: str | None) -> dict:
        self._require_tm()
        assert self._tm is not None
        tasks = await self._tm.list_tasks(limit=1000)
        if since:
            tasks = [t for t in tasks if (t.get("created_at") or "") >= since]
        if until:
            tasks = [t for t in tasks if (t.get("created_at") or "") <= until]
        rows_by_platform: dict[str, dict[str, int]] = {}
        for t in tasks:
            b = rows_by_platform.setdefault(
                t["platform"], {"total": 0, "succeeded": 0, "failed": 0}
            )
            b["total"] += 1
            if t["status"] == "succeeded":
                b["succeeded"] += 1
            elif t["status"] == "failed":
                b["failed"] += 1
        return {
            "total_tasks": len(tasks),
            "by_platform": rows_by_platform,
            "generated_at": _now_iso(),
        }

    # ── Helpers ──────────────────────────────────────────────────

    def _deps(self) -> PipelineDeps:
        assert self._tm is not None
        assert self._cookie_pool is not None
        assert self._engine is not None
        assert self._selectors_dir is not None
        assert self._screenshot_dir is not None
        return PipelineDeps(
            task_manager=self._tm,
            cookie_pool=self._cookie_pool,
            engine=self._engine,
            selectors_dir=self._selectors_dir,
            screenshot_dir=self._screenshot_dir,
            settings=self._settings,
            api=self._api,
        )

    def _spawn(self, coro) -> None:
        if self._api is None:
            return
        task = self._api.spawn_task(coro, name="omni-post:publish")
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)

    def _require_tm(self) -> None:
        if self._tm is None:
            raise HTTPException(503, "task manager not initialized")

    def _require_upload(self) -> None:
        if self._upload is None:
            raise HTTPException(503, "upload pipeline not initialized")

    def _require_cookie_pool(self) -> None:
        if self._cookie_pool is None:
            raise HTTPException(503, "cookie pool not initialized")


# ── Small request body models (inline) ────────────────────────────


class _UploadInitBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    filesize: int
    kind: str
    md5_hint: str | None = None


class _UploadFinalizeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_id: str
    tags: list[str] | None = None


class _AssetBusPullBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str


# ── Tool definitions (LLM-callable) ───────────────────────────────


def _build_tool_definitions() -> list[dict]:
    """Return the 14 OpenAI-style tool JSON schemas."""

    return [
        {
            "type": "function",
            "function": {
                "name": "omni_post_publish",
                "description": (
                    "Publish one asset across N platforms x M accounts. "
                    "Returns a list of created task ids."
                ),
                "parameters": {
                    "type": "object",
                    "required": [
                        "asset_id",
                        "payload",
                        "platforms",
                        "account_ids",
                        "client_trace_id",
                    ],
                    "properties": {
                        "asset_id": {"type": "string"},
                        "payload": {
                            "type": "object",
                            "required": ["title"],
                            "properties": {
                                "title": {"type": "string"},
                                "description": {"type": "string"},
                                "tags": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "topic": {"type": "string"},
                                "cover_asset_id": {"type": "string"},
                                "location": {"type": "string"},
                                "per_platform_overrides": {"type": "object"},
                            },
                        },
                        "platforms": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "account_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "client_trace_id": {"type": "string"},
                        "auto_submit": {"type": "boolean"},
                        "engine": {
                            "type": "string",
                            "enum": ["auto", "pw", "mp"],
                        },
                        "scheduled_at": {"type": "string"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "omni_post_schedule",
                "description": "Schedule a publish at a future ISO timestamp.",
                "parameters": {
                    "type": "object",
                    "required": [
                        "asset_id",
                        "payload",
                        "platforms",
                        "account_ids",
                        "client_trace_id",
                        "scheduled_at",
                    ],
                    "properties": {
                        "asset_id": {"type": "string"},
                        "payload": {"type": "object"},
                        "platforms": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "account_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "client_trace_id": {"type": "string"},
                        "scheduled_at": {"type": "string"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "omni_post_cancel",
                "description": "Cancel an in-progress or queued task.",
                "parameters": {
                    "type": "object",
                    "required": ["task_id"],
                    "properties": {"task_id": {"type": "string"}},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "omni_post_retry",
                "description": "Retry a failed or cancelled task.",
                "parameters": {
                    "type": "object",
                    "required": ["task_id"],
                    "properties": {"task_id": {"type": "string"}},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "omni_post_list_tasks",
                "description": "Return recent tasks, optionally filtered.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "platform": {"type": "string"},
                        "account_id": {"type": "string"},
                        "asset_id": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "omni_post_get_task",
                "description": "Return one task row.",
                "parameters": {
                    "type": "object",
                    "required": ["task_id"],
                    "properties": {"task_id": {"type": "string"}},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "omni_post_list_accounts",
                "description": "List all publisher accounts (cookies redacted).",
                "parameters": {
                    "type": "object",
                    "properties": {"platform": {"type": "string"}},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "omni_post_add_account",
                "description": "Add a new publisher account with an encrypted cookie.",
                "parameters": {
                    "type": "object",
                    "required": ["platform", "nickname", "cookie_raw"],
                    "properties": {
                        "platform": {"type": "string"},
                        "nickname": {"type": "string"},
                        "cookie_raw": {"type": "string"},
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "daily_limit": {"type": "integer"},
                        "weekly_limit": {"type": "integer"},
                        "monthly_limit": {"type": "integer"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "omni_post_remove_account",
                "description": "Remove a publisher account by id.",
                "parameters": {
                    "type": "object",
                    "required": ["account_id"],
                    "properties": {"account_id": {"type": "string"}},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "omni_post_refresh_account",
                "description": "Run a lazy health probe on a single account.",
                "parameters": {
                    "type": "object",
                    "required": ["account_id"],
                    "properties": {"account_id": {"type": "string"}},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "omni_post_list_assets",
                "description": "List uploaded assets.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "omni_post_delete_asset",
                "description": "Delete one asset row.",
                "parameters": {
                    "type": "object",
                    "required": ["asset_id"],
                    "properties": {"asset_id": {"type": "string"}},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "omni_post_pull_from_asset_bus",
                "description": (
                    "Consume an upstream asset (clip-sense / media-post / "
                    "subtitle-craft / idea-research output) into omni-post."
                ),
                "parameters": {
                    "type": "object",
                    "required": ["asset_id"],
                    "properties": {"asset_id": {"type": "string"}},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "omni_post_export_report",
                "description": "Emit a cross-platform publishing report.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "since": {"type": "string"},
                        "until": {"type": "string"},
                    },
                },
            },
        },
    ]


# ── Module-level helpers ─────────────────────────────────────────


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _infer_kind_from_path(p: Path) -> str:
    ext = p.suffix.lower().lstrip(".")
    if ext in {"mp4", "mov", "m4v", "mkv", "webm", "avi"}:
        return "video"
    if ext in {"jpg", "jpeg", "png", "webp", "gif", "bmp"}:
        return "image"
    if ext in {"mp3", "wav", "m4a", "flac", "ogg", "opus"}:
        return "audio"
    return "video"


# Keep a few names referenced so the import pass never drops them.
_ = (base64, PublishPayload)
Plugin = OmniPostPlugin
