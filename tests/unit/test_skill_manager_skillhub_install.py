from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_agent_skill_manager_installs_skillhub_detail_url_through_shared_installer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openakita.agent.skill_manager import SkillManager
    from openakita.config import settings
    from openakita.setup_center import bridge

    openakita_root = tmp_path / "openakita-home"
    workspace = openakita_root / "workspaces" / "default"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("OPENAKITA_ROOT", str(openakita_root))
    monkeypatch.setattr(settings, "project_root", workspace, raising=False)

    installed_sources: list[str] = []

    def fake_install(
        workspace_dir: str,
        source: str,
        *,
        category=None,
        emit_result: bool = True,
    ) -> Path:
        assert Path(workspace_dir) == workspace
        assert category is None
        assert emit_result is False
        installed_sources.append(source)
        target = workspace / "skills" / "demo-skill"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text(
            "---\nname: demo-skill\ndescription: demo\n---\n# Demo\n",
            encoding="utf-8",
        )
        return target

    monkeypatch.setattr(bridge, "install_skill", fake_install)

    loader = MagicMock()
    loader.load_skill.return_value = True
    catalog = MagicMock()
    catalog.generate_catalog.return_value = "catalog"
    manager = SkillManager(MagicMock(), loader, catalog, MagicMock())

    result = await manager.install_skill(
        "https://skillhub.cn/skills/community_demo/demo-skill?version=1.2.3"
    )

    assert installed_sources == ["skillhub:@community_demo/demo-skill?version=1.2.3"]
    loader.load_skill.assert_called_once_with(workspace / "skills" / "demo-skill", force=True)
    assert manager.catalog_text == "catalog"
    assert "SkillHub 安装成功" in result
    assert "@community_demo/demo-skill" in result
    assert "1.2.3" in result


@pytest.mark.asyncio
async def test_agent_skill_manager_rejects_malformed_skillhub_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openakita.agent.skill_manager import SkillManager
    from openakita.config import settings

    openakita_root = tmp_path / "openakita-home"
    workspace = openakita_root / "workspaces" / "default"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("OPENAKITA_ROOT", str(openakita_root))
    monkeypatch.setattr(settings, "project_root", workspace, raising=False)

    manager = SkillManager(MagicMock(), MagicMock(), MagicMock(), MagicMock())
    result = await manager.install_skill("https://skillhub.cn/not-a-skill")

    payload = json.loads(result)
    assert payload["details"]["failure_class"] == "skillhub_invalid_source"
