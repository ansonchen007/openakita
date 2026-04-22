"""avatar-studio — DashScope digital human studio (Phase 4 wiring).

Backend entry point. Wires:

- ``AvatarTaskManager``  — sqlite3-backed CRUD for tasks / voices / figures.
- ``AvatarDashScopeClient`` — DashScope async client (hot reload via
  ``read_settings`` callable).
- ``run_pipeline``        — 8-step linear orchestrator, spawned per task as a
  background ``asyncio.Task`` via ``api.spawn_task``.
- ``add_upload_preview_route`` — vendored upload preview helper (issue #479).

Routes (16):

  Tasks      POST /tasks            POST /cost-preview
             GET  /tasks            POST /tasks/{id}/cancel
             GET  /tasks/{id}       POST /tasks/{id}/retry
             DELETE /tasks/{id}
  Voices     GET  /voices           POST /voices
             DELETE /voices/{id}    POST /voices/{id}/sample
  Figures    GET  /figures          POST /figures
             DELETE /figures/{id}
  System     GET  /settings         PUT  /settings   GET /healthz
  Upload     POST /upload           GET  /uploads/{rel_path:path}
  Catalog    GET  /catalog

Pixelle hardening
-----------------

- C5  Missing API key on ``on_load`` is a WARN (red dot in UI), not a raise.
- C6  Pydantic models reject unknown fields with a 422 + ``ignored`` list.
- A10 ``read_settings`` callable threaded into the DashScope client; PUT
       /settings calls ``client.update_api_key`` for the immediate path.
- C3  ``api.broadcast_ui_event`` is the SSE channel; pipeline ``emit``
       wraps it.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from avatar_dashscope_client import (
    DASHSCOPE_BASE_URL_BJ,
    AvatarDashScopeClient,
)
from avatar_models import (
    DEFAULT_COST_THRESHOLD_CNY,
    MODES_BY_ID,
    build_catalog,
    estimate_cost,
)
from avatar_pipeline import (
    AvatarPipelineContext,
    run_pipeline,
)
from avatar_studio_inline.upload_preview import (
    add_upload_preview_route,
    build_preview_url,
)
from avatar_studio_inline.vendor_client import VendorError
from avatar_task_manager import AvatarTaskManager
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)


PLUGIN_ID = "avatar-studio"
SETTINGS_KEY = "avatar_studio_settings"


# ─── Pydantic request bodies (Pixelle C6 — strict, 422-on-unknown) ─────


def _strict_model(*, populate_by_name: bool = True) -> ConfigDict:
    """Reject unknown fields (Pixelle C6: never silently drop params)."""
    return ConfigDict(extra="forbid", populate_by_name=populate_by_name)


class CreateTaskBody(BaseModel):
    model_config = _strict_model()

    mode: str
    prompt: str = ""
    text: str = ""
    voice_id: str = ""
    resolution: str = "480P"
    aspect: str = "16:9"
    duration: int | None = None
    mode_pro: bool = False
    watermark: bool = False
    seed: int = -1
    compose_prompt: str = ""
    compose_size: str = ""
    use_qwen_vl: bool = False
    qwen_token_estimate: int = 600
    ref_image_count: int = 1
    video_duration_sec: float | None = None
    audio_duration_sec: float | None = None
    text_chars: int | None = None
    figure_id: str = ""
    cost_approved: bool = False
    assets: dict[str, str] = Field(default_factory=dict)


class CostPreviewBody(BaseModel):
    model_config = _strict_model()

    mode: str
    prompt: str = ""
    text: str = ""
    voice_id: str = ""
    resolution: str = "480P"
    duration: int | None = None
    mode_pro: bool = False
    use_qwen_vl: bool = False
    qwen_token_estimate: int = 600
    ref_image_count: int = 1
    video_duration_sec: float | None = None
    audio_duration_sec: float | None = None
    text_chars: int | None = None


class CreateVoiceBody(BaseModel):
    model_config = _strict_model()

    label: str
    source_audio_path: str
    # ``dashscope_voice_id`` is optional — when the UI submits a fresh
    # clone request we don't know the DashScope-assigned id yet; the
    # backend allocates a placeholder and the cosyvoice-v2 clone
    # workflow fills it in once DashScope returns.
    dashscope_voice_id: str = ""
    sample_url: str | None = None
    language: str = "zh-CN"
    gender: str = "unknown"


class CreateFigureBody(BaseModel):
    model_config = _strict_model()

    label: str
    image_path: str
    preview_url: str
    detect_pass: bool = False
    detect_humanoid: bool = False
    detect_message: str | None = None


class SettingsBody(BaseModel):
    model_config = _strict_model()

    api_key: str | None = None
    base_url: str | None = None
    timeout: float | None = None
    timeout_sec: float | None = None  # UI-friendly alias.
    max_retries: int | None = None
    cost_threshold: float | None = None
    cost_threshold_cny: float | None = None  # UI-friendly alias (CNY-suffixed).
    auto_archive: bool | None = None
    retention_days: int | None = None
    default_resolution: str | None = None
    default_voice: str | None = None


class CleanupBody(BaseModel):
    model_config = _strict_model()

    retention_days: int = 30


class AiComposePromptBody(BaseModel):
    model_config = _strict_model()

    ref_images_url: list[str] = Field(default_factory=list)
    hint: str = ""
    user_intent: str = ""


# ─── Static prompt-guide content (mirrors tongyi-image's GET /prompt-guide) ──
# Hand-curated digital-human writing tips. Returned verbatim so the React
# layer can render six <Collapsible> chapters without a network round-trip
# to LLMs. Update keys here only — never inline strings into the React side.

_PROMPT_GUIDE_ZH: dict[str, Any] = {
    "intro": (
        "数字人工作室的输出质量极大依赖输入素材与口播文本。下方按业务场景"
        "整理常用配方、最佳实践与避坑清单，可点击展开。"
    ),
    "mode_formulas": {
        "photo_speak": (
            "正面单人证件照（建议 ≥ 512px，光线均匀） + 口播文本（≤ 1000 字，"
            "建议每 30 字加一个标点） + 系统音色或克隆音色 → wan2.2-s2v"
        ),
        "video_relip": (
            "≤ 30 秒原视频（人物正脸时长 ≥ 60%） + 全新口播音频/文本（音频时长"
            "≤ 视频时长） → videoretalk（仅替换嘴型，保留头部姿态与表情）"
        ),
        "video_reface": (
            "≤ 30 秒动作视频 + 1 张新人物正面照（无遮挡） → wan2.2-animate-mix"
            "（保留场景与肢体动作，仅替换主角面部与发型）。pro 档位价格 2× "
            "但贴合度更高，建议商用首选。"
        ),
        "avatar_compose": (
            "1-3 张参考图（人物 / 服饰 / 场景，建议人物图放第一张） + 中文融合"
            "指令（建议 ≤ 60 字，可让 qwen-vl-max 帮你写） + 口播文本 → "
            "wan2.5-i2i-preview 合成新形象 → wan2.2-s2v 生成口播视频"
        ),
    },
    "best_practices": [
        "口播文本避免英文专有名词单独成句，cosyvoice-v2 可能逐字母拼读。",
        "人物正面照请去除墨镜、口罩、刘海过厚等遮挡，否则 s2v-detect 预检不通过。",
        "16:9 视频建议主体居中且头肩占比 ≥ 40%，避免脸部被裁切。",
        "克隆音色上传 5–30 秒安静的纯人声样本（无背景音乐），效果最佳。",
        "使用 1080P 时单价大幅上升（约 2.5×），如非商用建议 720P 起步。",
    ],
    "voice_tips": [
        "知性温暖：longxiaochun_v2（默认）、longwan_v2",
        "新闻播报：longmiao_v2、longxiaoxuan_v2",
        "活泼少女：longxiaobai_v2、longxiaohui_v2",
        "沉稳男声：longxiaocheng_v2、longhan_v2、longhua_v2",
    ],
    "video_reface_tips": [
        "动作幅度大、频繁转身的视频效果差；建议正面对话/演讲类素材。",
        "新人物图与原视频主角性别、年龄相近时贴合度更高。",
        "若效果不理想，可切换 pro 档位（wan-pro）二次生成。",
    ],
    "compose_examples": [
        '"把第二张图的服饰穿到第一张人物身上，背景换成第三张的咖啡馆"',
        '"参考第一张人物的五官，融合第二张的发型，输出半身像"',
        '"将三张图融合为一张电商套图，主角居中，左右各一件商品"',
    ],
    "faq": [
        ("任务一直 pending？", "请到「设置」检查 API Key 是否填对，或在「任务」里取消后重新提交。"),
        ("提示「内容审核未通过」？", "口播文本或图像中含敏感信息，请修改后重试。"),
        ("提示「dependency 错误」？", "本机缺少 dashscope SDK，请运行 `pip install dashscope`。"),
        ("如何降低费用？", "选择 480P 而非 720P/1080P；缩短口播文本；不使用 pro 档位。"),
    ],
}

_PROMPT_GUIDE_EN: dict[str, Any] = {
    "intro": (
        "Avatar Studio output quality depends heavily on input assets and "
        "speech text. The chapters below summarise recipes, best practices, "
        "and pitfalls — click to expand."
    ),
    "mode_formulas": {
        "photo_speak": (
            "Front-facing portrait (≥ 512px, even lighting) + speech text "
            "(≤ 1000 chars) + system or cloned voice → wan2.2-s2v"
        ),
        "video_relip": (
            "≤ 30s source video (face visible ≥ 60%) + new audio/text "
            "(audio ≤ video duration) → videoretalk (lips only)"
        ),
        "video_reface": (
            "≤ 30s motion video + 1 new portrait → wan2.2-animate-mix "
            "(scene + motion preserved, face replaced). pro tier costs 2×"
        ),
        "avatar_compose": (
            "1–3 references (portrait / outfit / scene) + Chinese merge prompt "
            "(qwen-vl-max can draft it) + speech text → wan2.5-i2i-preview → wan2.2-s2v"
        ),
    },
    "best_practices": [
        "Avoid English jargon as a standalone sentence — cosyvoice-v2 may spell letters.",
        "Remove sunglasses / heavy fringe — s2v-detect will fail otherwise.",
        "For 16:9, keep head-and-shoulders ≥ 40% to avoid face cropping.",
        "Use a 5–30s clean voice sample (no music) for cloning.",
        "1080P costs ~2.5× of 720P — start with 720P unless commercial.",
    ],
    "voice_tips": [
        "Warm: longxiaochun_v2 (default), longwan_v2",
        "Newscast: longmiao_v2, longxiaoxuan_v2",
        "Bright girl: longxiaobai_v2, longxiaohui_v2",
        "Calm male: longxiaocheng_v2, longhan_v2, longhua_v2",
    ],
    "video_reface_tips": [
        "Avoid large pose swings; prefer talking-head sources.",
        "Match gender/age of the new portrait to the original lead.",
        "Re-run on the pro tier if the result feels off.",
    ],
    "compose_examples": [
        '"Apply the outfit from image 2 to person in image 1, set scene to image 3"',
        '"Keep image-1 face, blend image-2 hairstyle, output half-body shot"',
        '"Compose three images into an e-commerce banner, person centred"',
    ],
    "faq": [
        ("Task stays pending?", "Verify the API Key in Settings, or cancel & resubmit."),
        ("'Moderation failed'?", "Sensitive content in text/image; rewrite and retry."),
        ("'Dependency error'?", "dashscope SDK missing — run `pip install dashscope`."),
        ("How to cut cost?", "Pick 480P; shorten text; skip pro tier."),
    ],
}


# ─── Plugin ────────────────────────────────────────────────────────────


class Plugin(PluginBase):
    """SDK 0.7.0-compatible entry point for avatar-studio."""

    # ── lifecycle ─────────────────────────────────────────────────────

    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        self._data_dir = Path(api.get_data_dir() or Path.cwd() / ".avatar-studio")
        self._data_dir.mkdir(parents=True, exist_ok=True)

        self._tm = AvatarTaskManager(self._data_dir / "avatar_studio.db")
        self._client = AvatarDashScopeClient(read_settings=self._read_settings)
        self._poll_tasks: dict[str, asyncio.Task[Any]] = {}

        # Validate settings — C5: warn, never raise.
        cfg = self._load_settings()
        if not cfg.get("api_key"):
            api.log(
                "avatar-studio: DashScope API Key not configured — set it in "
                "Settings before submitting any task",
                level="warning",
            )

        router = APIRouter()
        # Upload preview route (issue #479).
        add_upload_preview_route(
            router,
            base_dir=self._data_dir / "uploads",
        )
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools(self._tool_definitions(), handler=self._handle_tool)

        api.spawn_task(self._async_init(), name=f"{PLUGIN_ID}:init")
        api.log("avatar-studio loaded (4 modes, 16 routes, 9 tools)")

    async def _async_init(self) -> None:
        await self._tm.init()

    async def on_unload(self) -> None:
        # Cancel every in-flight pipeline task.
        for tid, t in list(self._poll_tasks.items()):
            if not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
                except Exception as exc:  # noqa: BLE001
                    logger.warning("avatar-studio: pipeline %s cleanup error: %s", tid, exc)
        try:
            await self._tm.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("avatar-studio: tm close error: %s", exc)

    # ── settings ──────────────────────────────────────────────────────

    def _load_settings(self) -> dict[str, Any]:
        cfg = self._api.get_config() or {}
        merged: dict[str, Any] = {
            "api_key": "",
            "base_url": DASHSCOPE_BASE_URL_BJ,
            "timeout": 60.0,
            "max_retries": 2,
            "cost_threshold": DEFAULT_COST_THRESHOLD_CNY,
            "auto_archive": False,
            "retention_days": 30,
            "default_resolution": "480P",
            "default_voice": "longxiaochun_v2",
        }
        for k in list(merged):
            if k in cfg and cfg[k] not in (None, ""):
                merged[k] = cfg[k]
        # Aliases — accept both ``cost_threshold`` and ``cost_threshold_cny``
        # so the UI can use the suffixed name without breaking older
        # tooling. Same for ``timeout`` / ``timeout_sec``.
        if cfg.get("cost_threshold_cny") not in (None, ""):
            merged["cost_threshold"] = cfg["cost_threshold_cny"]
        if cfg.get("timeout_sec") not in (None, ""):
            merged["timeout"] = cfg["timeout_sec"]
        # Mirror back so the UI reads consistent names regardless of which
        # alias was used to write the value.
        merged["cost_threshold_cny"] = merged["cost_threshold"]
        merged["timeout_sec"] = merged["timeout"]
        return merged

    def _read_settings(self) -> dict[str, Any]:
        """Callable threaded into the DashScope client (Pixelle A10)."""
        return self._load_settings()

    # ── tool handler ──────────────────────────────────────────────────

    def _tool_definitions(self) -> list[dict[str, Any]]:
        common_props = {
            "prompt": {"type": "string"},
            "text": {"type": "string"},
            "voice_id": {"type": "string"},
            "resolution": {"type": "string", "enum": ["480P", "720P"]},
            "assets": {"type": "object"},
        }
        return [
            {
                # mode_id may already carry the ``avatar_`` namespace
                # (e.g. ``avatar_compose``); avoid the awkward
                # ``avatar_avatar_compose`` doubling so tool names line
                # up 1:1 with the plugin.json manifest.
                "name": m.id if m.id.startswith("avatar_") else f"avatar_{m.id}",
                "description": f"{m.label_zh} — {m.description_zh}",
                "input_schema": {
                    "type": "object",
                    "properties": common_props,
                    "required": [],
                },
            }
            for m in MODES_BY_ID.values()
        ] + [
            {
                "name": "avatar_voice_create",
                "description": "克隆一个自定义 cosyvoice-v2 音色",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "source_audio_path": {"type": "string"},
                    },
                    "required": ["label", "source_audio_path"],
                },
            },
            {
                "name": "avatar_voice_delete",
                "description": "删除自定义音色",
                "input_schema": {
                    "type": "object",
                    "properties": {"voice_id": {"type": "string"}},
                    "required": ["voice_id"],
                },
            },
            {
                "name": "avatar_figure_create",
                "description": "把一张人像照添加进形象库（自动跑 face-detect）",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "image_path": {"type": "string"},
                    },
                    "required": ["label", "image_path"],
                },
            },
            {
                "name": "avatar_figure_delete",
                "description": "从形象库删除一个人像",
                "input_schema": {
                    "type": "object",
                    "properties": {"figure_id": {"type": "string"}},
                    "required": ["figure_id"],
                },
            },
            {
                "name": "avatar_cost_preview",
                "description": "估算一次任务的费用（不实际发起任务）",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "mode": {
                            "type": "string",
                            "enum": list(MODES_BY_ID.keys()),
                        },
                        **common_props,
                    },
                    "required": ["mode"],
                },
            },
        ]

    async def _handle_tool(self, tool_name: str, args: dict[str, Any]) -> str:
        if tool_name == "avatar_cost_preview":
            preview = estimate_cost(
                args.get("mode", "photo_speak"),
                args,
                audio_duration_sec=args.get("audio_duration_sec"),
                text_chars=args.get("text_chars"),
            )
            return f"预估费用 {preview['formatted_total']}（{len(preview['items'])} 项）"

        mode_to_tool = {(m if m.startswith("avatar_") else f"avatar_{m}"): m for m in MODES_BY_ID}
        if tool_name in mode_to_tool:
            mode = mode_to_tool[tool_name]
            task = await self._create_task_internal(mode, args)
            return f"任务已创建：{task['id']}（mode={mode}）"

        if tool_name == "avatar_voice_create":
            voice_id = await self._tm.create_custom_voice(
                label=str(args.get("label", "custom")),
                source_audio_path=str(args.get("source_audio_path", "")),
                dashscope_voice_id=str(args.get("dashscope_voice_id", "")),
            )
            return f"音色已创建：{voice_id}"
        if tool_name == "avatar_voice_delete":
            ok = await self._tm.delete_custom_voice(str(args.get("voice_id", "")))
            return "ok" if ok else "not found"
        if tool_name == "avatar_figure_create":
            fig_id = await self._tm.create_figure(
                label=str(args.get("label", "figure")),
                image_path=str(args.get("image_path", "")),
                preview_url=str(args.get("preview_url", "")),
            )
            return f"形象已创建：{fig_id}"
        if tool_name == "avatar_figure_delete":
            ok = await self._tm.delete_figure(str(args.get("figure_id", "")))
            return "ok" if ok else "not found"
        return f"unknown tool: {tool_name}"

    # ── task helpers ──────────────────────────────────────────────────

    async def _create_task_internal(self, mode: str, params: dict[str, Any]) -> dict[str, Any]:
        if mode not in MODES_BY_ID:
            raise HTTPException(status_code=422, detail=f"unknown mode: {mode}")
        if not self._client.has_api_key():
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "missing_api_key",
                    "message": "DashScope API Key 未配置；请到「设置」填写后再提交任务",
                },
            )

        # Pre-flight cost preview so the API consumer (UI / tool / curl)
        # always sees the breakdown alongside the new task row.
        preview = estimate_cost(
            mode,
            params,
            audio_duration_sec=params.get("audio_duration_sec"),
            text_chars=params.get("text_chars"),
        )

        task_id = await self._tm.create_task(
            mode=mode,
            prompt=str(params.get("prompt", "")),
            params=dict(params),
            asset_paths={k: str(v) for k, v in (params.get("assets") or {}).items()},
            cost_breakdown=dict(preview),
        )

        ctx = AvatarPipelineContext(task_id=task_id, mode=mode, params=dict(params))
        ctx.cost_approved = bool(params.get("cost_approved"))
        # Spawn the pipeline; tracking handle so on_unload can cancel.
        self._poll_tasks[task_id] = self._api.spawn_task(
            self._run_one_pipeline(ctx),
            name=f"{PLUGIN_ID}:pipeline:{task_id}",
        )

        row = await self._tm.get_task(task_id)
        return row or {"id": task_id, "mode": mode, "status": "pending"}

    async def _run_one_pipeline(self, ctx: AvatarPipelineContext) -> None:
        try:
            await run_pipeline(
                ctx,
                tm=self._tm,
                client=self._client,
                emit=self._emit,
                plugin_id=PLUGIN_ID,
                base_data_dir=self._data_dir,
            )
        except Exception:
            logger.exception("avatar-studio: pipeline crashed for task %s", ctx.task_id)
        finally:
            self._poll_tasks.pop(ctx.task_id, None)

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        try:
            self._api.broadcast_ui_event(event, payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("avatar-studio: emit %s failed: %s", event, exc)

    # ── routes ────────────────────────────────────────────────────────

    def _register_routes(self, router: APIRouter) -> None:
        # Tasks ───────────────────────────────────────────────────────

        @router.post("/tasks")
        async def create_task(body: CreateTaskBody) -> dict[str, Any]:
            params = body.model_dump()
            mode = params.pop("mode")
            task = await self._create_task_internal(mode, params)
            return {"ok": True, "task": task}

        @router.get("/tasks")
        async def list_tasks(
            status: str | None = None,
            mode: str | None = None,
            limit: int = 50,
            offset: int = 0,
        ) -> dict[str, Any]:
            tasks = await self._tm.list_tasks(status=status, mode=mode, limit=limit, offset=offset)
            return {"ok": True, "tasks": tasks, "total": len(tasks)}

        @router.get("/tasks/{task_id}")
        async def get_task(task_id: str) -> dict[str, Any]:
            row = await self._tm.get_task(task_id)
            if not row:
                raise HTTPException(status_code=404, detail="task not found")
            return {"ok": True, "task": row}

        @router.delete("/tasks/{task_id}")
        async def delete_task(task_id: str) -> dict[str, Any]:
            handle = self._poll_tasks.get(task_id)
            if handle and not handle.done():
                handle.cancel()
            ok = await self._tm.delete_task(task_id)
            return {"ok": ok}

        @router.post("/tasks/{task_id}/cancel")
        async def cancel_task(task_id: str) -> dict[str, Any]:
            row = await self._tm.get_task(task_id)
            if not row:
                raise HTTPException(status_code=404, detail="task not found")
            if row.get("dashscope_id"):
                self._client.mark_cancelled(str(row["dashscope_id"]))
                await self._client.cancel_task(str(row["dashscope_id"]))
            self._client.mark_cancelled(task_id)
            handle = self._poll_tasks.get(task_id)
            if handle and not handle.done():
                handle.cancel()
            await self._tm.update_task_safe(task_id, status="cancelled")
            return {"ok": True}

        @router.post("/tasks/{task_id}/retry")
        async def retry_task(task_id: str) -> dict[str, Any]:
            row = await self._tm.get_task(task_id)
            if not row:
                raise HTTPException(status_code=404, detail="task not found")
            params = row.get("params") or {}
            mode = row.get("mode") or "photo_speak"
            task = await self._create_task_internal(mode, params)
            return {"ok": True, "task": task}

        # Cost preview ────────────────────────────────────────────────

        @router.post("/cost-preview")
        async def cost_preview(body: CostPreviewBody) -> dict[str, Any]:
            d = body.model_dump()
            mode = d.pop("mode")
            preview = estimate_cost(
                mode,
                d,
                audio_duration_sec=d.get("audio_duration_sec"),
                text_chars=d.get("text_chars"),
            )
            return {"ok": True, "preview": preview}

        # Voices ──────────────────────────────────────────────────────

        @router.get("/voices")
        async def list_voices() -> dict[str, Any]:
            from avatar_models import SYSTEM_VOICES

            sys_rows = [{**v.to_dict(), "is_system": True} for v in SYSTEM_VOICES]
            custom_rows = [{**row, "is_system": False} for row in await self._tm.list_voices()]
            return {"ok": True, "voices": sys_rows + custom_rows}

        @router.post("/voices")
        async def create_voice(body: CreateVoiceBody) -> dict[str, Any]:
            voice_id = await self._tm.create_custom_voice(**body.model_dump())
            return {"ok": True, "voice_id": voice_id}

        @router.delete("/voices/{voice_id}")
        async def delete_voice(voice_id: str) -> dict[str, Any]:
            ok = await self._tm.delete_custom_voice(voice_id)
            return {"ok": ok}

        @router.post("/voices/{voice_id}/sample")
        async def synth_sample(
            voice_id: str, text: str = "你好，欢迎使用数字人工作室"
        ) -> dict[str, Any]:
            try:
                res = await self._client.synth_voice(text=text, voice_id=voice_id)
            except VendorError as e:
                raise HTTPException(status_code=400, detail={"kind": e.kind, "message": str(e)})
            sample_dir = self._data_dir / "voice_samples"
            sample_dir.mkdir(parents=True, exist_ok=True)
            fname = f"{voice_id}_{uuid.uuid4().hex[:8]}.{res['format']}"
            (sample_dir / fname).write_bytes(res["audio_bytes"])
            url = build_preview_url(PLUGIN_ID, f"voice_samples/{fname}")
            return {"ok": True, "url": url}

        # Figures ─────────────────────────────────────────────────────

        @router.get("/figures")
        async def list_figures() -> dict[str, Any]:
            return {"ok": True, "figures": await self._tm.list_figures()}

        @router.post("/figures")
        async def create_figure(body: CreateFigureBody) -> dict[str, Any]:
            fig_id = await self._tm.create_figure(**body.model_dump())
            return {"ok": True, "figure_id": fig_id}

        @router.delete("/figures/{fig_id}")
        async def delete_figure(fig_id: str) -> dict[str, Any]:
            ok = await self._tm.delete_figure(fig_id)
            return {"ok": ok}

        # System ──────────────────────────────────────────────────────

        @router.get("/settings")
        async def get_settings() -> dict[str, Any]:
            cfg = self._load_settings()
            cfg["has_api_key"] = bool(cfg.get("api_key"))
            cfg["api_key"] = ""  # Never echo the secret back.
            return {"ok": True, "config": cfg}

        @router.put("/settings")
        async def put_settings(body: SettingsBody) -> dict[str, Any]:
            updates = {k: v for k, v in body.model_dump().items() if v is not None}
            self._api.set_config(updates)
            if "api_key" in updates:
                self._client.update_api_key(str(updates["api_key"]))
            return {"ok": True, "config": self._load_settings()}

        @router.get("/healthz")
        async def healthz() -> dict[str, Any]:
            storage_bytes = 0
            try:
                for p in self._data_dir.rglob("*"):
                    if p.is_file():
                        storage_bytes += p.stat().st_size
            except OSError:
                pass
            return {
                "ok": True,
                "plugin": PLUGIN_ID,
                "ts": time.time(),
                "has_api_key": self._client.has_api_key(),
                "api_reachable": self._client.has_api_key(),
                "in_flight": len(self._poll_tasks),
                "storage": {"bytes_used": storage_bytes, "dir": str(self._data_dir)},
            }

        @router.post("/cleanup")
        async def cleanup(body: CleanupBody) -> dict[str, Any]:
            removed = await self._tm.cleanup_expired(retention_days=body.retention_days)
            return {"ok": True, "removed": removed}

        @router.post("/ai/compose-prompt")
        async def ai_compose_prompt(body: AiComposePromptBody) -> dict[str, Any]:
            # Optional helper — uses qwen-vl-max to draft a "merge" prompt
            # for ``avatar_compose``. Returns 200 with empty prompt if the
            # ``caption_with_qwen_vl`` path is unavailable so the UI can
            # fall back to the manual textarea without surfacing an error.
            if not body.ref_images_url:
                raise HTTPException(status_code=422, detail="ref_images_url required")
            try:
                resp = await self._client.caption_with_qwen_vl(
                    image_urls=list(body.ref_images_url),
                    system_prompt=(
                        "你是一名擅长写图片融合指令的设计师。"
                        '请仅返回 JSON：{"prompt": "..."}，不要解释。'
                    ),
                    user_prompt=(
                        body.user_intent
                        or "请基于这些参考图，写一段不超过 60 字的中文指令，"
                        "用于把它们融合成一张主体人像图。"
                    ),
                )
                parsed = (resp or {}).get("parsed") or {}
                prompt = str(parsed.get("prompt") or resp.get("text") or "").strip()
                return {"ok": True, "prompt": prompt}
            except Exception as exc:  # noqa: BLE001
                logger.info("avatar-studio: ai compose prompt fell back: %s", exc)
                return {"ok": True, "prompt": "", "fallback": True}

        @router.get("/catalog")
        async def catalog() -> dict[str, Any]:
            cat = build_catalog()
            return {"ok": True, "catalog": cat.__dict__}

        @router.get("/prompt-guide")
        async def prompt_guide(locale: str = "zh") -> dict[str, Any]:
            # Static, in-process knowledge base for the "提示词指南" tab.
            # Mirrors tongyi-image's GET /prompt-guide so the React layer
            # can reuse the same `<Collapsible>` rendering loop.
            return {
                "ok": True,
                "locale": locale,
                "guide": _PROMPT_GUIDE_ZH if locale != "en" else _PROMPT_GUIDE_EN,
            }

        # Upload ──────────────────────────────────────────────────────

        @router.post("/upload")
        async def upload_file(
            file: UploadFile = File(...),
            kind: str = "image",
        ) -> dict[str, Any]:
            ext = Path(file.filename or "file").suffix.lower().lstrip(".") or "bin"
            subdir = {
                "image": "images",
                "video": "videos",
                "audio": "audios",
            }.get(kind, "other")
            uploads_dir = self._data_dir / "uploads" / subdir
            uploads_dir.mkdir(parents=True, exist_ok=True)
            fname = f"{uuid.uuid4().hex[:12]}.{ext}"
            content = await file.read()
            (uploads_dir / fname).write_bytes(content)
            rel = f"{subdir}/{fname}"
            return {
                "ok": True,
                "path": rel,
                "url": build_preview_url(PLUGIN_ID, rel),
                "size": len(content),
            }

        # Pydantic models above use ``extra="forbid"``; FastAPI then
        # auto-returns 422 with ``loc=[..., 'unknown_field']`` and
        # ``type='extra_forbidden'`` so the UI can detect Pixelle C6
        # silent-drop violations. We don't add a custom handler here
        # because ``APIRouter`` does not expose ``exception_handler`` —
        # only the top-level ``FastAPI`` app does.
