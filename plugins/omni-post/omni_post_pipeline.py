"""omni-post publish pipeline — glues engine, adapters, retries, MDRM.

High-level flow per task::

    for attempt in range(max_retries + 1):
        try:
            outcome = await engine.run_task(...)
        except OmniPostError as e:
            await _record_failure(task, e); break
        if outcome.success:
            await _record_success(task, outcome)
            break
        if not _is_retryable(outcome.error_kind):
            break
        if attempt >= fail_threshold:
            ctx.auto_submit = False   # degrade to half-auto
        await asyncio.sleep(backoff(attempt))

Every terminal status write:

  1. Updates the ``tasks`` row (``status`` / ``error_kind`` / ...),
  2. Appends an ``asset_publish_history`` record,
  3. Publishes a ``publish_receipt`` asset on the host Asset Bus when
     the task succeeded (opt-in via ``api.publish_asset``),
  4. Emits a ``task_update`` UI event so the frontend can redraw,
  5. Writes a memory node + causal edges when brain.access is granted.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any

from omni_post_engine_pw import PlaywrightEngine, build_adapter
from omni_post_models import (
    ERROR_HINTS,
    PLATFORMS_BY_ID,
    ErrorKind,
    OmniPostError,
)

logger = logging.getLogger("openakita.plugins.omni-post")


_RETRYABLE_KINDS = {
    ErrorKind.NETWORK.value,
    ErrorKind.TIMEOUT.value,
    ErrorKind.RATE_LIMIT.value,
    ErrorKind.RATE_LIMITED_BY_PLATFORM.value,
}

_PLATFORM_BREAKING_KINDS = {
    ErrorKind.PLATFORM_BREAKING_CHANGE.value,
    ErrorKind.DEPENDENCY.value,
}


@dataclass
class PipelineDeps:
    """Everything the pipeline reads but does not own the lifecycle of.

    Using a plain dataclass (not DI framework) keeps test setup trivial
    and gives us a single documented seam for stubbing.
    """

    task_manager: Any  # OmniPostTaskManager
    cookie_pool: Any  # CookiePool
    engine: PlaywrightEngine
    selectors_dir: Path
    screenshot_dir: Path
    settings: dict[str, Any]
    api: Any | None = None  # PluginAPI (optional — tests run without host)


async def run_publish_task(deps: PipelineDeps, task_id: str) -> dict[str, Any]:
    """Entry point called from HTTP / Tool / scheduler.

    Returns the final task row (dict) the caller can echo back.
    """

    task = await deps.task_manager.get_task(task_id)
    if task is None:
        raise OmniPostError(ErrorKind.NOT_FOUND, f"task {task_id} not found")

    account = await deps.task_manager.get_account(task["account_id"])
    if account is None:
        await _terminal_failure(
            deps,
            task,
            ErrorKind.NOT_FOUND,
            f"account {task['account_id']} not found",
        )
        return await deps.task_manager.get_task(task_id) or task

    asset_info = None
    if task.get("asset_id"):
        asset_info = await deps.task_manager.get_asset(task["asset_id"])
        if asset_info is None:
            await _terminal_failure(
                deps,
                task,
                ErrorKind.NOT_FOUND,
                f"asset {task['asset_id']} not found",
            )
            return await deps.task_manager.get_task(task_id) or task

    await _broadcast(deps, "task_update", {"task_id": task_id, "status": "running"})
    await deps.task_manager.update_task_safe(
        task_id,
        {"status": "running", "started_at": _now_iso()},
    )

    try:
        cookies_plaintext = deps.cookie_pool.open(account["cookie_cipher"])
    except Exception as e:  # noqa: BLE001
        logger.warning("cookie decryption failed for %s: %s", account["id"], e)
        await _terminal_failure(
            deps,
            task,
            ErrorKind.COOKIE_EXPIRED,
            "cookie decryption failed; re-import this account",
        )
        return await deps.task_manager.get_task(task_id) or task

    try:
        adapter = build_adapter(task["platform"], deps.selectors_dir)
    except FileNotFoundError as e:
        await _terminal_failure(
            deps,
            task,
            ErrorKind.PLATFORM_BREAKING_CHANGE,
            f"missing selector bundle: {e}",
        )
        return await deps.task_manager.get_task(task_id) or task
    except OmniPostError as e:
        await _terminal_failure(deps, task, e.kind, str(e))
        return await deps.task_manager.get_task(task_id) or task

    max_retries = int(deps.settings.get("retry_max_attempts", 3))
    fail_threshold = int(deps.settings.get("auto_submit_fail_threshold", 3))
    backoff_base = float(deps.settings.get("retry_backoff_base", 2.0))

    auto_submit = True
    last_outcome = None
    for attempt in range(max_retries + 1):
        engine_payload = {
            "id": task_id,
            "platform": task["platform"],
            "payload": task.get("payload") or _safe_json(task.get("payload_json")),
        }
        try:
            outcome = await deps.engine.run_task(
                adapter=adapter,
                task=engine_payload,
                account=account,
                cookies_plaintext=cookies_plaintext,
                asset_path=(asset_info or {}).get("storage_path", ""),
                cover_path=None,
                settings={**deps.settings, "auto_submit": auto_submit},
            )
        except OmniPostError as e:
            await _terminal_failure(deps, task, e.kind, str(e))
            return await deps.task_manager.get_task(task_id) or task

        last_outcome = outcome
        if outcome.success:
            await _terminal_success(deps, task, outcome, asset_info)
            return await deps.task_manager.get_task(task_id) or task

        err_kind = outcome.error_kind or ErrorKind.UNKNOWN.value

        # Cookie expired / platform change / moderation: no retry, surface.
        if (
            err_kind
            in (
                ErrorKind.COOKIE_EXPIRED.value,
                ErrorKind.CONTENT_MODERATED.value,
                ErrorKind.MODERATION.value,
                ErrorKind.AUTH.value,
            )
            or err_kind in _PLATFORM_BREAKING_KINDS
        ):
            await _terminal_failure(
                deps,
                task,
                ErrorKind(err_kind),
                outcome.error_message,
                retries=attempt,
                screenshots=outcome.screenshots,
            )
            return await deps.task_manager.get_task(task_id) or task

        if err_kind not in _RETRYABLE_KINDS:
            # Unknown kinds get one retry just in case, then surface.
            if attempt >= max_retries:
                await _terminal_failure(
                    deps,
                    task,
                    ErrorKind(err_kind)
                    if err_kind in {k.value for k in ErrorKind}
                    else ErrorKind.UNKNOWN,
                    outcome.error_message,
                    retries=attempt,
                    screenshots=outcome.screenshots,
                )
                return await deps.task_manager.get_task(task_id) or task

        await deps.task_manager.update_task_safe(
            task_id,
            {
                "retry_count": attempt + 1,
                "error_kind": err_kind,
                "error_hint_i18n": ERROR_HINTS.get(err_kind, ERROR_HINTS["unknown"]),
            },
        )
        await _broadcast(
            deps,
            "task_retry",
            {
                "task_id": task_id,
                "attempt": attempt + 1,
                "max_retries": max_retries,
                "error_kind": err_kind,
            },
        )

        # Half-auto degradation (issue #198): after N failures, stop
        # auto-submitting so the next run leaves the browser open for
        # the human to push the last button.
        if attempt + 1 >= fail_threshold:
            auto_submit = False

        sleep_s = _jittered_backoff(backoff_base, attempt)
        await asyncio.sleep(sleep_s)

    # If we fell through the loop without terminal-success, record the
    # last outcome's error.
    if last_outcome is not None:
        err_kind = last_outcome.error_kind or ErrorKind.UNKNOWN.value
        await _terminal_failure(
            deps,
            task,
            ErrorKind(err_kind) if err_kind in {k.value for k in ErrorKind} else ErrorKind.UNKNOWN,
            last_outcome.error_message,
            retries=max_retries,
            screenshots=last_outcome.screenshots,
        )
    return await deps.task_manager.get_task(task_id) or task


# ── Terminal status helpers ─────────────────────────────────────────


async def _terminal_success(
    deps: PipelineDeps, task: dict[str, Any], outcome, asset_info: dict | None
) -> None:
    now = _now_iso()
    await deps.task_manager.update_task_safe(
        task["id"],
        {
            "status": "succeeded",
            "finished_at": now,
            "result_url": outcome.published_url,
            "screenshot_path": (outcome.screenshots[-1] if outcome.screenshots else None),
            "error_kind": None,
            "error_hint_i18n": None,
        },
    )
    if asset_info:
        await deps.task_manager.record_publish_history(
            asset_id=asset_info["id"],
            task_id=task["id"],
            platform=task["platform"],
            account_id=task["account_id"],
            status="succeeded",
            published_url=outcome.published_url,
            screenshot_path=(outcome.screenshots[-1] if outcome.screenshots else None),
        )
    await deps.task_manager.update_account_safe(
        task["account_id"],
        {"last_published_at": now, "health_status": "ok", "last_health_check": now},
    )
    await _publish_receipt_asset(deps, task, outcome, asset_info)
    await _broadcast(
        deps,
        "task_update",
        {
            "task_id": task["id"],
            "status": "succeeded",
            "result_url": outcome.published_url,
        },
    )
    await _write_publish_memory(deps, task, outcome, asset_info, success=True)


async def _terminal_failure(
    deps: PipelineDeps,
    task: dict[str, Any],
    kind: ErrorKind,
    message: str,
    *,
    retries: int = 0,
    screenshots: list[str] | None = None,
) -> None:
    now = _now_iso()
    hint = ERROR_HINTS.get(kind.value, ERROR_HINTS["unknown"])
    await deps.task_manager.update_task_safe(
        task["id"],
        {
            "status": "failed",
            "finished_at": now,
            "error_kind": kind.value,
            "error_hint_i18n": hint,
            "retry_count": retries,
            "screenshot_path": (screenshots[-1] if screenshots else None),
        },
    )
    if task.get("asset_id"):
        await deps.task_manager.record_publish_history(
            asset_id=task["asset_id"],
            task_id=task["id"],
            platform=task["platform"],
            account_id=task["account_id"],
            status="failed",
            screenshot_path=(screenshots[-1] if screenshots else None),
        )
    if kind is ErrorKind.COOKIE_EXPIRED:
        await deps.task_manager.update_account_safe(
            task["account_id"],
            {"health_status": "cookie_expired", "last_health_check": now},
        )
    await _broadcast(
        deps,
        "task_update",
        {
            "task_id": task["id"],
            "status": "failed",
            "error_kind": kind.value,
            "error_message": message,
            "error_hint_i18n": hint,
        },
    )
    await _write_publish_memory(deps, task, None, None, success=False, error=kind.value)


# ── Asset Bus + MDRM helpers ────────────────────────────────────────


async def _publish_receipt_asset(
    deps: PipelineDeps, task: dict, outcome, asset_info: dict | None
) -> None:
    api = deps.api
    if api is None or not hasattr(api, "publish_asset"):
        return
    try:
        metadata = {
            "platform": task["platform"],
            "account_id": task["account_id"],
            "asset_id": task.get("asset_id"),
            "published_url": outcome.published_url,
            "published_at": _now_iso(),
            "task_id": task["id"],
            "engine": task.get("engine"),
        }
        await api.publish_asset(
            asset_kind="publish_receipt",
            preview_url=outcome.published_url,
            metadata=metadata,
            shared_with=["*"],
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("omni-post: publish_asset failed: %s", e)


async def _write_publish_memory(
    deps: PipelineDeps,
    task: dict,
    outcome,
    asset_info: dict | None,
    *,
    success: bool,
    error: str | None = None,
) -> None:
    api = deps.api
    if api is None or not hasattr(api, "write_memory"):
        return
    try:
        from datetime import datetime

        iso_now = _now_iso()
        hour_bucket = datetime.now().strftime("%H")
        weekday = datetime.now().strftime("%a")
        node = {
            "type": "publish_event",
            "platform": task["platform"],
            "account_id": task["account_id"],
            "asset_id": task.get("asset_id"),
            "success": success,
            "error": error,
            "hour_bucket": hour_bucket,
            "weekday": weekday,
            "occurred_at": iso_now,
            "task_id": task["id"],
        }
        await api.write_memory(node)  # type: ignore[attr-defined]
    except Exception as e:  # noqa: BLE001
        logger.debug("omni-post: memory write skipped: %s", e)


# ── Quota + scheduling helpers ──────────────────────────────────────


async def check_account_quota(deps: PipelineDeps, account_id: str) -> dict[str, Any]:
    """Return a breakdown of the account's remaining daily/weekly/monthly cap.

    Called by the HTTP layer *before* inserting a task row so we can
    refuse a publish that would exceed the quota.
    """

    account = await deps.task_manager.get_account(account_id)
    if account is None:
        raise OmniPostError(ErrorKind.NOT_FOUND, f"account {account_id} not found")

    from datetime import datetime, timedelta

    now = datetime.now(UTC)
    day = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    week = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    month = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    day_n = await deps.task_manager.count_account_published_since(account_id, day)
    week_n = await deps.task_manager.count_account_published_since(account_id, week)
    month_n = await deps.task_manager.count_account_published_since(account_id, month)
    return {
        "daily": {"used": day_n, "limit": account["daily_limit"]},
        "weekly": {"used": week_n, "limit": account["weekly_limit"]},
        "monthly": {"used": month_n, "limit": account["monthly_limit"]},
    }


def platform_display_name(platform_id: str, locale: str = "zh") -> str:
    spec = PLATFORMS_BY_ID.get(platform_id)
    if spec is None:
        return platform_id
    return spec.display_name_zh if locale.startswith("zh") else spec.display_name_en


# ── Local utilities ─────────────────────────────────────────────────


async def _broadcast(deps: PipelineDeps, event_type: str, data: dict) -> None:
    api = deps.api
    if api is None:
        return
    try:
        api.broadcast_ui_event(event_type, data)
    except Exception as e:  # noqa: BLE001
        logger.debug("broadcast_ui_event(%s) failed: %s", event_type, e)


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_json(raw: Any) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}


def _jittered_backoff(base: float, attempt: int) -> float:
    """Exponential backoff with full jitter (AWS recipe)."""

    cap = 60.0
    window = min(cap, base * (2**attempt))
    return random.uniform(0.0, window)


__all__ = [
    "PipelineDeps",
    "check_account_quota",
    "platform_display_name",
    "run_publish_task",
]
