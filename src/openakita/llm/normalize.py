"""
LLM 消息规范化管线

在发送到 API 之前，将内部消息格式规范化：
- 过滤空消息
- 合并连续同角色消息（部分 API 要求严格 user/assistant 交替）
"""

from __future__ import annotations


def normalize_messages_for_api(messages: list[dict]) -> list[dict]:
    """规范化消息列表，使其符合 LLM API 要求。

    Args:
        messages: 消息字典列表，每个至少包含 role 和 content 字段。

    Returns:
        规范化后的消息字典列表。
    """
    if not messages:
        return []

    result: list[dict] = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content")

        # 跳过内容为空的非 tool 消息
        if content is None and role not in ("tool",):
            continue
        if isinstance(content, str) and not content.strip() and role not in ("tool",):
            continue

        if result and result[-1]["role"] == role:
            # 合并连续同角色消息
            prev = result[-1]
            prev_content = prev["content"]
            if isinstance(prev_content, str) and isinstance(content, str):
                prev["content"] = prev_content + "\n" + content
            elif isinstance(prev_content, list) and isinstance(content, list):
                prev["content"] = prev_content + content
            elif isinstance(prev_content, list) and isinstance(content, str):
                prev["content"] = prev_content + [{"type": "text", "text": content}]
            elif isinstance(prev_content, str) and isinstance(content, list):
                prev["content"] = [{"type": "text", "text": prev_content}] + content
            else:
                result.append(dict(msg))
        else:
            result.append(dict(msg))

    return result
