"""
SSE（Server-Sent Events）解析工具

提供符合 SSE 规范的流式响应解析器，供 Anthropic Provider 使用。
支持带 event: 字段的具名事件和纯 data: 字段的匿名事件。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx

logger = logging.getLogger(__name__)


async def parse_sse_stream(response: httpx.Response) -> AsyncIterator[dict]:
    """解析 SSE 流式响应，yield 解析后的事件字典。

    Anthropic SSE 格式示例：
        event: message_start
        data: {"type": "message_start", "message": {...}}

        event: content_block_delta
        data: {"type": "content_block_delta", "index": 0, "delta": {...}}

        event: message_stop
        data: {"type": "message_stop"}

    Args:
        response: httpx 流式响应对象（须在 stream=True 模式下发起请求）。

    Yields:
        解析后的事件字典，每个事件至少含 "type" 字段。
    """
    current_event_type: str | None = None

    async for line in response.aiter_lines():
        line = line.rstrip("\r")

        # 空行 = 事件边界，重置 event type
        if not line:
            current_event_type = None
            continue

        if line.startswith("event:"):
            current_event_type = line[6:].strip()
            continue

        if line.startswith("data:"):
            data_str = line[5:].strip()

            # SSE 流结束标志
            if data_str == "[DONE]":
                break

            if not data_str:
                continue

            try:
                event = json.loads(data_str)
            except json.JSONDecodeError:
                logger.debug("SSE: failed to parse JSON: %r", data_str[:200])
                continue

            # 若有具名 event type 且 dict 中没有 type 字段，则注入
            if isinstance(event, dict):
                if current_event_type and "type" not in event:
                    event["type"] = current_event_type
                yield event

        elif line.startswith(":"):
            # SSE 注释行，忽略（用于心跳保活）
            continue
        else:
            logger.debug("SSE: unrecognized line: %r", line[:200])
