"""ppt-maker plugin entry point.

Phase 0 only wires a minimal router and tool registry. Later phases add the
project store, pipeline, table analyzer, template manager, exporter, and UI
routes while preserving this self-contained plugin shape.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from openakita.plugins.api import PluginAPI, PluginBase

from ppt_maker_inline.file_utils import dataset_dir, resolve_plugin_data_root, safe_name, unique_child
from ppt_maker_inline.upload_preview import register_upload_preview_routes
from ppt_source_loader import MissingDependencyError, SourceLoader, SourceParseError
from ppt_table_analyzer import TableAnalyzer
from ppt_task_manager import PptTaskManager


PLUGIN_ID = "ppt-maker"


class ParseSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    kind: str | None = None


class DatasetCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    name: str | None = None
    project_id: str | None = None


class Plugin(PluginBase):
    """OpenAkita plugin entry for guided PPT generation."""

    def __init__(self) -> None:
        self._api: PluginAPI | None = None
        self._data_dir: Path | None = None

    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = resolve_plugin_data_root(api.get_data_dir() or Path.cwd() / "data")
        self._data_dir = data_dir

        router = APIRouter()
        register_upload_preview_routes(router, data_dir / "uploads", prefix="/uploads")

        @router.get("/healthz")
        async def healthz() -> dict[str, Any]:
            return {
                "ok": True,
                "plugin": PLUGIN_ID,
                "phase": 1,
                "data_dir": str(data_dir),
                "db_path": str(data_dir / "ppt_maker.db"),
            }

        @router.post("/upload")
        async def upload(request: Request) -> dict[str, Any]:
            form = await request.form()
            upload = form.get("file")
            project_id = str(form.get("project_id") or "") or None
            if upload is None or not hasattr(upload, "filename") or not hasattr(upload, "read"):
                raise HTTPException(status_code=400, detail="Missing upload field: file")

            filename = safe_name(str(upload.filename or "upload.bin"))
            target = unique_child(data_dir / "uploads", filename)
            content = await upload.read()
            target.write_bytes(content)

            loader = SourceLoader()
            kind = loader.detect_kind(target)
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                source = await manager.create_source(
                    project_id=project_id,
                    kind=kind,
                    filename=filename,
                    path=str(target),
                    metadata={"size": len(content), "preview_url": f"/uploads/{target.name}"},
                )
            return {
                "ok": True,
                "source": source.model_dump(mode="json"),
                "preview_url": f"/uploads/{target.name}",
            }

        @router.post("/sources/parse")
        async def parse_source(payload: ParseSourceRequest) -> dict[str, Any]:
            loader = SourceLoader()
            try:
                parsed = await loader.parse(payload.path, kind=payload.kind)
            except MissingDependencyError as exc:
                raise HTTPException(
                    status_code=424,
                    detail={"error": str(exc), "dependency_group": exc.dependency_group},
                ) from exc
            except SourceParseError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {
                "ok": True,
                "source": {
                    "kind": parsed.kind,
                    "title": parsed.title,
                    "text": parsed.text,
                    "metadata": parsed.metadata,
                },
            }

        @router.post("/datasets")
        async def create_dataset(payload: DatasetCreateRequest) -> dict[str, Any]:
            source_path = Path(payload.path)
            if not source_path.exists() or not source_path.is_file():
                raise HTTPException(status_code=404, detail="Dataset file not found")
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                dataset = await manager.create_dataset(
                    project_id=payload.project_id,
                    name=payload.name or source_path.stem,
                    original_path=str(source_path),
                    metadata={"kind": SourceLoader().detect_kind(source_path)},
                )
            return {"ok": True, "dataset": dataset.model_dump(mode="json")}

        @router.get("/datasets")
        async def list_datasets() -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                datasets = await manager.list_datasets()
            return {"ok": True, "datasets": [item.model_dump(mode="json") for item in datasets]}

        @router.get("/datasets/{dataset_id}")
        async def get_dataset(dataset_id: str) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                dataset = await manager.get_dataset(dataset_id)
            if dataset is None:
                raise HTTPException(status_code=404, detail="Dataset not found")
            return {"ok": True, "dataset": dataset.model_dump(mode="json")}

        @router.post("/datasets/{dataset_id}/profile")
        async def profile_dataset(dataset_id: str) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                dataset = await manager.get_dataset(dataset_id)
                if dataset is None:
                    raise HTTPException(status_code=404, detail="Dataset not found")
                try:
                    analysis = TableAnalyzer().analyze_to_files(
                        dataset.original_path,
                        dataset_dir(data_dir, dataset_id),
                    )
                except MissingDependencyError as exc:
                    raise HTTPException(
                        status_code=424,
                        detail={"error": str(exc), "dependency_group": exc.dependency_group},
                    ) from exc
                except SourceParseError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                dataset = await manager.update_dataset_safe(
                    dataset_id,
                    status="profiled",
                    profile_path=analysis["paths"]["profile_path"],
                    insights_path=analysis["paths"]["insights_path"],
                    chart_specs_path=analysis["paths"]["chart_specs_path"],
                )
            return {
                "ok": True,
                "dataset": dataset.model_dump(mode="json") if dataset else None,
                "profile": analysis["profile"],
            }

        @router.post("/datasets/{dataset_id}/insights")
        async def dataset_insights(dataset_id: str) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                dataset = await manager.get_dataset(dataset_id)
            if dataset is None or not dataset.insights_path:
                raise HTTPException(status_code=404, detail="Dataset insights not found")
            insights = json.loads(Path(dataset.insights_path).read_text(encoding="utf-8"))
            return {"ok": True, "insights": insights}

        @router.post("/datasets/{dataset_id}/chart-specs")
        async def dataset_chart_specs(dataset_id: str) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                dataset = await manager.get_dataset(dataset_id)
            if dataset is None or not dataset.chart_specs_path:
                raise HTTPException(status_code=404, detail="Dataset chart specs not found")
            chart_specs = json.loads(Path(dataset.chart_specs_path).read_text(encoding="utf-8"))
            return {"ok": True, "chart_specs": chart_specs}

        api.register_api_routes(router)
        api.register_tools(_tool_definitions(), self._handle_tool)
        api.log(f"{PLUGIN_ID}: loaded")

    async def _handle_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if tool_name == "ppt_list_projects":
            return "ppt-maker project storage is available. Routes are wired in Phase 9."
        return f"{tool_name} is registered; implementation is added in later phases."

    async def on_unload(self) -> None:
        if self._api:
            self._api.log(f"{PLUGIN_ID}: unloaded")


def _tool_definitions() -> list[dict[str, Any]]:
    names = [
        ("ppt_start_project", "Start a guided PPT project."),
        ("ppt_ingest_sources", "Attach source documents to a PPT project."),
        ("ppt_ingest_table", "Attach CSV/XLSX/table data to a PPT project."),
        ("ppt_profile_table", "Profile an ingested table dataset."),
        ("ppt_generate_table_insights", "Generate table insights for a PPT project."),
        ("ppt_upload_template", "Upload a PPTX enterprise template."),
        ("ppt_diagnose_template", "Diagnose a PPTX template for brand/layout tokens."),
        ("ppt_generate_outline", "Generate a presentation outline."),
        ("ppt_confirm_outline", "Confirm or update a generated outline."),
        ("ppt_generate_design", "Generate design_spec and spec_lock."),
        ("ppt_confirm_design", "Confirm or update design settings."),
        ("ppt_generate_deck", "Generate slide IR and export a PPT deck."),
        ("ppt_revise_slide", "Revise one slide or part of a PPT project."),
        ("ppt_audit", "Audit a generated PPT project."),
        ("ppt_export", "Export a PPT project."),
        ("ppt_list_projects", "List PPT projects."),
        ("ppt_cancel", "Cancel a running PPT task."),
    ]
    return [
        {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            },
        }
        for name, desc in names
    ]

