import asyncio
import sys
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException

from openakita.api.routes.agents import (
    BotCreateRequest,
    _bot_apply_tasks,
    _runtime_bot_view,
    _validate_bot_credentials,
    create_bot,
)


def test_wework_ws_requires_all_credentials() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _validate_bot_credentials("wework_ws", {"bot_id": "bot-1"})

    assert exc_info.value.status_code == 400
    assert "secret" in exc_info.value.detail


@pytest.mark.asyncio
async def test_create_bot_returns_before_runtime_start_and_keeps_failed_config(monkeypatch) -> None:
    import openakita.config as config

    original_bots = config.settings.im_bots
    saves: list[list[dict]] = []
    runtime_states: list[tuple[str, str, str | None]] = []
    startup_started = asyncio.Event()
    finish_startup = asyncio.Event()

    async def fail_apply(_bot: dict) -> bool:
        startup_started.set()
        await finish_startup.wait()
        return False

    monkeypatch.setattr(config.settings, "im_bots", [], raising=False)
    monkeypatch.setattr(
        config.runtime_state, "save", lambda: saves.append(list(config.settings.im_bots))
    )
    main_stub = ModuleType("openakita.main")
    main_stub.apply_im_bot = fail_apply
    main_stub.get_im_bot_runtime_error = lambda _channel: None
    main_stub._bot_channel_name = lambda bot: f"{bot['type']}:{bot['id']}"
    main_stub._set_im_bot_runtime_state = (
        lambda channel, status, error=None: runtime_states.append((channel, status, error))
    )
    monkeypatch.setitem(sys.modules, "openakita.main", main_stub)

    try:
        response = await create_bot(
            BotCreateRequest(
                id="warehouse",
                type="wework_ws",
                credentials={"bot_id": "bot-1", "secret": "secret-1"},
            )
        )

        assert response["status"] == "accepted"
        assert config.settings.im_bots[0]["id"] == "warehouse"
        assert len(saves) == 1
        assert saves[0][0]["id"] == "warehouse"
        assert runtime_states == [("wework_ws:warehouse", "starting", None)]

        await asyncio.wait_for(startup_started.wait(), timeout=1)
        assert _bot_apply_tasks

        finish_startup.set()
        await asyncio.gather(*tuple(_bot_apply_tasks))

        assert config.settings.im_bots[0]["id"] == "warehouse"
        assert runtime_states[-1] == (
            "wework_ws:warehouse",
            "error",
            "The IM runtime is not available to start this bot",
        )
    finally:
        finish_startup.set()
        if _bot_apply_tasks:
            await asyncio.gather(*tuple(_bot_apply_tasks), return_exceptions=True)
        config.settings.im_bots = original_bots


def test_runtime_status_reports_missing_credentials() -> None:
    from openakita.channels.status import collect_effective_im_status

    settings = SimpleNamespace(
        telegram_enabled=False,
        feishu_enabled=False,
        wework_enabled=False,
        wework_ws_enabled=False,
        dingtalk_enabled=False,
        onebot_enabled=False,
        qqbot_enabled=False,
        wechat_enabled=False,
        im_bots=[
            {
                "id": "warehouse",
                "type": "wework_ws",
                "enabled": True,
                "credentials": {},
            }
        ],
    )

    status = collect_effective_im_status(settings)
    detail = next(item for item in status["details"] if item["source"] == "im_bots")

    assert detail["configured"] is False
    assert detail["missing"] == ["bot_id", "secret"]
    assert detail["runtime_status"] == "unknown"


def test_runtime_bot_view_exposes_dependency_install_state(monkeypatch) -> None:
    monkeypatch.setattr(
        "openakita.channels.runtime_status.resolve_bot_runtime_state",
        lambda _channel, _gateway=None: {
            "status": "installing_dependencies",
            "error": None,
            "progress": {"phase": "downloading", "percent": 64.0},
        },
    )

    view = _runtime_bot_view(
        {
            "id": "feishu-main",
            "type": "feishu",
            "enabled": True,
            "credentials": {"app_id": "cli_xxx", "app_secret": "secret"},
        },
        {
            "configured": True,
            "missing": [],
            "runtime_seen": False,
            "runtime_status": "unknown",
        },
    )

    assert view["runtime_status"] == "installing_dependencies"
    assert view["runtime_seen"] is True
    assert view["runtime_error"] is None
    assert view["runtime_progress"] == {"phase": "downloading", "percent": 64.0}
