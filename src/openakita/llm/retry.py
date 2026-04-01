"""
重试延迟计算

提供指数退避 + jitter 的重试延迟计算函数，供 LLM 客户端使用。
"""

from __future__ import annotations

import random


_BASE_DELAY_MS = 1000
_MAX_DELAY_MS = 60_000


def calculate_retry_delay(
    attempt: int,
    retry_after_seconds: float | None = None,
) -> float:
    """计算重试延迟（毫秒）。

    策略：
    1. 若响应头中含 Retry-After，以其为准（加小 jitter）。
    2. 否则使用指数退避：base * 2^attempt，带 ±25% jitter，上限 60s。

    Args:
        attempt: 当前重试次数（从 0 开始）。
        retry_after_seconds: 从 Retry-After 响应头解析的秒数（可选）。

    Returns:
        建议等待的毫秒数（float）。
    """
    if retry_after_seconds is not None and retry_after_seconds > 0:
        # 遵守服务端的重试建议，额外加 0-500ms jitter 避免惊群
        jitter = random.uniform(0, 500)
        return retry_after_seconds * 1000 + jitter

    # 指数退避：1s, 2s, 4s, 8s, ... 上限 60s
    exp_delay = _BASE_DELAY_MS * (2 ** attempt)
    capped = min(exp_delay, _MAX_DELAY_MS)

    # ±25% jitter，防止多客户端同时重试造成惊群
    jitter_factor = random.uniform(0.75, 1.25)
    return capped * jitter_factor
