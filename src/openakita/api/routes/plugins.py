"""Plugin management REST API."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, Field

from ...config import settings
from ...plugins import installer
from ...plugins.installer import PluginInstallError
from ...plugins.manifest import ManifestError, parse_manifest
from ...plugins.state import PluginState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


def _plugins_dir() -> Path:
    return Path(settings.project_root) / "data" / "plugins"


def _plugin_state_path() -> Path:
    return Path(settings.project_root) / "data" / "plugin_state.json"


def _get_plugin_manager(request: Request):
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        return None
    return getattr(agent, "_plugin_manager", None)


def _require_manager(request: Request):
    pm = _get_plugin_manager(request)
    if pm is None:
        raise HTTPException(
            status_code=503,
            detail="Plugin manager is not available",
        )
    return pm


class InstallBody(BaseModel):
    source: str = Field(..., min_length=1)


def _build_plugin_list(pm, plugins_dir: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    state = pm.state if pm is not None else PluginState.load(_plugin_state_path())
    failed: dict[str, str] = dict(pm.list_failed()) if pm else {}
    loaded_by_id: dict[str, dict[str, Any]] = {}
    if pm:
        for entry in pm.list_loaded():
            loaded_by_id[entry["id"]] = entry

    plugins: list[dict[str, Any]] = []
    if not plugins_dir.is_dir():
        return plugins, failed

    for child in sorted(plugins_dir.iterdir()):
        if not child.is_dir() or not (child / "plugin.json").is_file():
            continue
        try:
            manifest = parse_manifest(child)
        except ManifestError as e:
            plugins.append(
                {
                    "id": child.name,
                    "status": "invalid",
                    "error": str(e),
                },
            )
            continue

        pid = manifest.id
        enabled = state.is_enabled(pid)
        entry = state.get_entry(pid)

        if pm and pid in loaded_by_id:
            row = {**loaded_by_id[pid], "status": "loaded", "enabled": enabled}
        elif pid in failed:
            row = {
                "id": pid,
                "name": manifest.name,
                "version": manifest.version,
                "type": manifest.plugin_type,
                "category": manifest.category,
                "status": "failed",
                "error": failed[pid],
                "enabled": enabled,
            }
        elif not enabled:
            row = {
                "id": pid,
                "name": manifest.name,
                "version": manifest.version,
                "type": manifest.plugin_type,
                "category": manifest.category,
                "status": "disabled",
                "enabled": False,
                "disabled_reason": entry.disabled_reason if entry else "",
            }
        else:
            row = {
                "id": pid,
                "name": manifest.name,
                "version": manifest.version,
                "type": manifest.plugin_type,
                "category": manifest.category,
                "status": "installed",
                "enabled": True,
            }
        plugins.append(row)

    return plugins, failed


@router.get("/list")
async def list_plugins(request: Request) -> dict[str, Any]:
    pm = _get_plugin_manager(request)
    plugins_dir = _plugins_dir()
    plugins, failed = _build_plugin_list(pm, plugins_dir)
    return {"plugins": plugins, "failed": failed}


@router.post("/install")
async def install_plugin(body: InstallBody) -> dict[str, str]:
    plugins_dir = _plugins_dir()
    src = body.source.strip()
    try:
        if src.startswith(("http://", "https://")):
            plugin_id = await asyncio.to_thread(installer.install_from_url, src, plugins_dir)
        else:
            plugin_id = await asyncio.to_thread(installer.install_from_path, src, plugins_dir)
    except (PluginInstallError, ValueError, OSError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"plugin_id": plugin_id}


@router.delete("/{plugin_id}")
async def uninstall_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
    plugins_dir = _plugins_dir()
    state_path = _plugin_state_path()
    pm = _get_plugin_manager(request)
    if pm:
        await pm.unload_plugin(plugin_id)
        pm.state.remove_plugin(plugin_id)
        pm.state.save(state_path)
    else:
        state = PluginState.load(state_path)
        state.remove_plugin(plugin_id)
        state.save(state_path)

    await asyncio.to_thread(installer.uninstall, plugin_id, plugins_dir)
    return {"ok": True}


@router.post("/{plugin_id}/enable")
async def enable_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
    pm = _require_manager(request)
    await pm.enable_plugin(plugin_id)
    return {"ok": True}


@router.post("/{plugin_id}/disable")
async def disable_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
    pm = _require_manager(request)
    await pm.disable_plugin(plugin_id)
    return {"ok": True}


def _plugin_config_path(plugin_id: str) -> Path:
    return _plugins_dir() / plugin_id / "config.json"


@router.get("/{plugin_id}/config")
async def get_plugin_config(plugin_id: str) -> dict[str, Any]:
    path = _plugin_config_path(plugin_id)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Plugin config read failed for %s: %s", plugin_id, e)
        raise HTTPException(status_code=500, detail="Invalid plugin config file") from e


@router.put("/{plugin_id}/config")
async def update_plugin_config(
    plugin_id: str,
    body: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    path = _plugin_config_path(plugin_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    current: dict[str, Any] = {}
    if path.is_file():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=500, detail="Existing config is invalid JSON") from e
    current.update(body)
    path.write_text(
        json.dumps(current, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return current


@router.get("/{plugin_id}/logs")
async def get_plugin_logs(
    plugin_id: str,
    request: Request,
    lines: int = 100,
) -> dict[str, str]:
    pm = _require_manager(request)
    text = pm.get_plugin_logs(plugin_id, lines)
    return {"logs": text}


# --- Hub / Marketplace ---

PLUGIN_CATEGORIES = [
    {"slug": "channel", "name": "Chat Providers", "icon": "message-circle"},
    {"slug": "llm", "name": "AI Models", "icon": "cpu"},
    {"slug": "knowledge", "name": "Productivity", "icon": "book-open"},
    {"slug": "tool", "name": "Tools & Automation", "icon": "wrench"},
    {"slug": "memory", "name": "Memory", "icon": "brain"},
    {"slug": "hook", "name": "Hooks & Extensions", "icon": "git-branch"},
    {"slug": "skill", "name": "Skills", "icon": "star"},
    {"slug": "mcp", "name": "MCP Servers", "icon": "plug"},
]


@router.get("/hub/categories")
async def list_categories() -> list[dict]:
    return PLUGIN_CATEGORIES


@router.get("/hub/search")
async def hub_search(
    q: str = "",
    category: str = "",
) -> dict[str, Any]:
    return {
        "query": q,
        "category": category,
        "results": [],
        "total": 0,
        "message": "Plugin marketplace coming soon",
    }
