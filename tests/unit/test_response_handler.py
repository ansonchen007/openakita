"""L1 Unit Tests: ResponseHandler static/utility methods."""

import pytest

from openakita.core.response_handler import (
    strip_thinking_tags,
    strip_tool_simulation_text,
    clean_llm_response,
    ResponseHandler,
    request_expects_artifact,
)


class TestStripThinkingTags:
    def test_strip_basic_thinking(self):
        text = "<thinking>I need to analyze this</thinking>Here is my answer."
        result = strip_thinking_tags(text)
        assert "<thinking>" not in result
        assert "Here is my answer" in result

    def test_no_thinking_tags(self):
        text = "Just a normal response."
        result = strip_thinking_tags(text)
        assert result == text

    def test_empty_input(self):
        assert strip_thinking_tags("") == ""


class TestStripToolSimulation:
    def test_strip_tool_sim(self):
        text = "Let me check that for you."
        result = strip_tool_simulation_text(text)
        assert isinstance(result, str)


class TestCleanLLMResponse:
    def test_clean_basic(self):
        result = clean_llm_response("  Hello, how can I help?  ")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_clean_with_thinking(self):
        text = "<thinking>plan</thinking>Here is the answer."
        result = clean_llm_response(text)
        assert "Here is the answer" in result


class TestResponseHandlerStaticMethods:
    def test_should_compile_prompt_simple(self):
        result = ResponseHandler.should_compile_prompt("你好")
        assert isinstance(result, bool)

    def test_should_compile_prompt_complex(self):
        result = ResponseHandler.should_compile_prompt(
            "帮我分析这个项目的架构，然后重构数据库层，最后写测试"
        )
        assert isinstance(result, bool)

    def test_get_last_user_request(self):
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
            {"role": "user", "content": "帮我写代码"},
        ]
        last = ResponseHandler.get_last_user_request(messages)
        assert "写代码" in last

    def test_get_last_user_request_empty(self):
        result = ResponseHandler.get_last_user_request([])
        assert isinstance(result, str)


class TestRequestExpectsArtifactPrefixGuard:
    """request_expects_artifact 必须对系统/组织合成前缀返回 False，
    避免汇总轮里命中正文中的『文件/附件/写一份』关键词被误判为需要附件交付。"""

    def test_summary_round_does_not_expect_artifact(self):
        msg = (
            "[用户指令最终汇总] 你最初接到的用户指令所触发的所有委派任务均已关闭。"
            "请基于下级各自交付的成果，向用户输出一份完整的最终汇总。"
        )
        assert request_expects_artifact(msg) is False

    def test_system_prefix_does_not_expect_artifact(self):
        assert request_expects_artifact("[系统] 请立即调用 write_file 写一份文件") is False

    def test_real_user_artifact_request_still_detected(self):
        assert request_expects_artifact("帮我写一份openakita的宣传计划") is True


class TestVerifyTaskCompletionPrefixBypass:
    """verify_task_completion 在 bypass 检查后增加的系统前缀兜底，
    保证即使上游 is_summary_round 计算失误，汇总轮也不会被误判 INCOMPLETE。"""

    @pytest.mark.asyncio
    async def test_summary_round_user_request_bypasses_verify(self):
        handler = ResponseHandler(brain=None, memory_manager=None)

        is_completed = await handler.verify_task_completion(
            user_request=(
                "[用户指令最终汇总] 你最初接到的用户指令所触发的所有委派任务均已关闭。"
            ),
            assistant_response="（任意纯文本汇总，无附件）",
            executed_tools=["read_file"],
            delivery_receipts=[],
            tool_results=[],
            conversation_id=None,
            bypass=False,
        )

        assert is_completed is True

    @pytest.mark.asyncio
    async def test_system_prefix_user_request_bypasses_verify(self):
        handler = ResponseHandler(brain=None, memory_manager=None)

        is_completed = await handler.verify_task_completion(
            user_request="[系统] 请立即继续推进 plan",
            assistant_response="OK，已继续",
            executed_tools=[],
            delivery_receipts=[],
            tool_results=[],
            conversation_id=None,
            bypass=False,
        )

        assert is_completed is True

    @pytest.mark.asyncio
    async def test_supervisor_bypass_path_still_works(self):
        """老的 supervisor bypass 路径不能被新增的兜底破坏。"""
        handler = ResponseHandler(brain=None, memory_manager=None)

        is_completed = await handler.verify_task_completion(
            user_request="帮我写一份文件",
            assistant_response="...",
            executed_tools=[],
            delivery_receipts=[],
            tool_results=[],
            conversation_id=None,
            bypass=True,
        )

        assert is_completed is True

