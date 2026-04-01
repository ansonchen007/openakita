"""
Anthropic Prompt Caching 辅助函数

提供缓存控制标记相关的工具函数：
- 系统提示缓存块构建
- 工具列表排序（提升缓存命中率）
- 工具缓存控制标记
- 消息缓存断点注入
"""

from __future__ import annotations

import copy


def build_cached_system_blocks(system: str) -> list[dict]:
    """将系统提示字符串转换为带缓存控制的 content blocks。

    Args:
        system: 系统提示文本。

    Returns:
        Anthropic messages API 接受的 system content blocks 列表。
    """
    if not system:
        return []
    return [
        {
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def sort_tools_for_cache_stability(tools: list[dict]) -> list[dict]:
    """对工具列表按名称排序，提升缓存稳定性。

    工具定义顺序固定后，Anthropic 可在跨请求间复用相同的 cache block。

    Args:
        tools: Anthropic 工具定义列表（每项包含 "name" 字段）。

    Returns:
        按 name 字段升序排列后的新列表（不修改原列表）。
    """
    return sorted(tools, key=lambda t: t.get("name", ""))


def add_tools_cache_control(tools: list[dict]) -> list[dict]:
    """在最后一个工具上添加 cache_control 标记。

    Anthropic Prompt Caching 要求在 cache breakpoint 处设置 cache_control，
    对工具列表通常标记最后一个工具，使整个工具块被缓存。

    Args:
        tools: 工具定义列表。

    Returns:
        深拷贝后、最后一个工具已添加 cache_control 的新列表。
    """
    if not tools:
        return tools
    result = copy.deepcopy(tools)
    result[-1]["cache_control"] = {"type": "ephemeral"}
    return result


def add_message_cache_breakpoints(
    messages: list[dict],
    max_breakpoints: int = 2,
) -> list[dict]:
    """在最后若干条消息的末尾 content block 上注入 cache_control 断点。

    Anthropic 最多支持 4 个 cache breakpoint，通常在最后 1-2 条消息处标记，
    可让已缓存的对话历史在下一轮请求中直接复用，降低 TTFT 和 token 费用。

    Args:
        messages: 消息字典列表（含 role 和 content 字段）。
        max_breakpoints: 最多在末尾多少条消息上添加断点，默认 2。

    Returns:
        深拷贝后已注入断点的消息列表。
    """
    if not messages or max_breakpoints <= 0:
        return messages

    result = copy.deepcopy(messages)
    # 仅在 user/assistant 消息上标记断点（跳过 tool 结果等）
    eligible_indices = [
        i for i, m in enumerate(result) if m.get("role") in ("user", "assistant")
    ]
    target_indices = eligible_indices[-max_breakpoints:]

    for idx in target_indices:
        msg = result[idx]
        content = msg.get("content")
        if isinstance(content, list) and content:
            # 在最后一个 content block 上添加 cache_control
            last_block = content[-1]
            if isinstance(last_block, dict):
                last_block["cache_control"] = {"type": "ephemeral"}
        elif isinstance(content, str):
            # 字符串内容需先转换为 block 格式才能附加 cache_control
            msg["content"] = [
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }
            ]

    return result
