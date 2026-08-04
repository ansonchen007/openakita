from __future__ import annotations

import json

import pytest
from word_brain_helper import WordBrainHelper


class FakeLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    async def complete(self, **kwargs):
        self.prompts.append(kwargs["prompt"])
        return type("Completion", (), {"text": self.response})()


class FakeAPI:
    def __init__(self, llm=None, granted: bool = True, endpoint: str = "") -> None:
        self.llm = llm
        self.granted = granted
        self.endpoint = endpoint

    def has_permission(self, name: str) -> bool:
        assert name == "brain.access"
        return self.granted

    def get_llm(self):
        return self.llm

    def get_config(self):
        return {"word_maker_settings": {"llm_endpoint": self.endpoint}}


@pytest.mark.asyncio
async def test_clarify_requirements_uses_brain_json() -> None:
    payload = {
        "doc_type": "meeting_minutes",
        "questions": ["会议时间是什么？"],
        "assumptions": ["使用正式语气"],
        "next_action": "generate_outline",
    }
    llm = FakeLLM("```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```")
    helper = WordBrainHelper(FakeAPI(llm, endpoint="primary"))

    result = await helper.clarify_requirements(requirement="生成会议纪要")

    assert result.ok is True
    assert result.used_brain is True
    assert result.data["doc_type"] == "meeting_minutes"
    assert "Return ONLY valid JSON" in llm.prompts[0]


@pytest.mark.asyncio
async def test_generate_outline_falls_back_without_permission() -> None:
    helper = WordBrainHelper(FakeAPI(FakeLLM("{}"), granted=False))

    result = await helper.generate_outline(requirement="生成报告", doc_type="research_report")

    assert result.ok is False
    assert result.used_brain is False
    assert result.data["sections"]
    assert "brain.access" in result.error


@pytest.mark.asyncio
async def test_invalid_brain_json_returns_schema_error() -> None:
    helper = WordBrainHelper(FakeAPI(FakeLLM('{"title": "Only title"}')))

    result = await helper.generate_outline(requirement="生成报告", doc_type="research_report")

    assert result.ok is False
    assert result.used_brain is True
    assert "missing keys" in result.error
    assert result.data["title"] == "文档初稿"


@pytest.mark.asyncio
async def test_extract_fields_schema() -> None:
    payload = {"fields": {"company": "OpenAkita"}, "missing": [], "confidence": "high"}
    helper = WordBrainHelper(FakeAPI(FakeLLM(json.dumps(payload, ensure_ascii=False))))

    result = await helper.extract_fields(
        template_vars=["company"],
        requirement="填写公司字段",
        sources_text="OpenAkita",
    )

    assert result.ok is True
    assert result.data["fields"]["company"] == "OpenAkita"
