"""
模型注册表

提供模型能力查询接口，供各 LLM Provider 使用：
- get_model_capabilities: 查询模型的 token 限制和缓存支持情况
- get_thinking_budget: 查询思考模式的 token 预算
"""

from __future__ import annotations

from dataclasses import dataclass

from .capabilities import infer_capabilities


@dataclass
class ModelCapabilities:
    """模型能力描述"""

    default_output_tokens: int = 8192
    max_output_tokens: int = 32768
    supports_cache: bool = False
    supports_thinking: bool = False


# Claude 模型能力表（Anthropic Prompt Caching 支持 claude-3+ 系列）
_ANTHROPIC_CACHE_MODELS = (
    "claude-3",
    "claude-sonnet",
    "claude-haiku",
    "claude-opus",
)

# 常见模型默认输出 token 上限
_DEFAULT_OUTPUT_TOKENS: dict[str, int] = {
    # Anthropic
    "claude-3-opus": 4096,
    "claude-3-sonnet": 4096,
    "claude-3-haiku": 4096,
    "claude-3-5-sonnet": 8192,
    "claude-3-5-haiku": 8192,
    "claude-3-7-sonnet": 16000,
    "claude-opus-4": 32000,
    "claude-sonnet-4": 16000,
    "claude-haiku-4": 8192,
    # OpenAI
    "gpt-4o": 16384,
    "gpt-4o-mini": 16384,
    "gpt-4-turbo": 4096,
    "gpt-4": 8192,
    "gpt-3.5-turbo": 4096,
    "o1": 32768,
    "o1-mini": 65536,
    # DeepSeek
    "deepseek-chat": 8192,
    "deepseek-reasoner": 8192,
}

# 思考模式 token 预算（不同 depth 对应不同预算）
_THINKING_BUDGETS: dict[str, dict[str, int]] = {
    "claude-3-7-sonnet": {"low": 1024, "medium": 5000, "high": 10000, "max": 20000},
    "claude-3-5-sonnet": {"low": 512, "medium": 2048, "high": 5000, "max": 10000},
    "claude-opus-4": {"low": 2048, "medium": 8192, "high": 16000, "max": 32000},
    "claude-sonnet-4": {"low": 1024, "medium": 5000, "high": 10000, "max": 20000},
}

_DEFAULT_THINKING_BUDGET = {"low": 1024, "medium": 4096, "high": 8192, "max": 16000}


def get_model_capabilities(model_id: str) -> ModelCapabilities:
    """查询模型的基础能力（token 限制 + 缓存支持）。

    Args:
        model_id: 模型 ID，如 "claude-3-5-sonnet-20241022"。

    Returns:
        ModelCapabilities 实例。
    """
    model_lower = model_id.lower()

    # 优先复用项目内置能力推断，避免重复维护能力表
    inferred_caps = infer_capabilities(model_id)

    # Prompt Caching 当前仅在 Anthropic 兼容模型上启用
    supports_cache = any(kw in model_lower for kw in _ANTHROPIC_CACHE_MODELS)
    supports_thinking = bool(inferred_caps.get("thinking", False))

    # 查找默认输出 token 数（前缀匹配）
    default_output = 8192
    for key, tokens in _DEFAULT_OUTPUT_TOKENS.items():
        if model_lower.startswith(key.lower()):
            default_output = tokens
            break

    return ModelCapabilities(
        default_output_tokens=default_output,
        max_output_tokens=max(default_output * 2, 32768),
        supports_cache=supports_cache,
        supports_thinking=supports_thinking,
    )


def get_thinking_budget(model_id: str, thinking_depth: str | None) -> int:
    """查询思考模式的 token 预算。

    Args:
        model_id: 模型 ID。
        thinking_depth: 思考深度，"low" / "medium" / "high" / "max"。

    Returns:
        thinking budget (int)，若模型/深度不支持则返回 0。
    """
    depth = (thinking_depth or "medium").lower()
    model_lower = model_id.lower()
    inferred_caps = infer_capabilities(model_id)

    if not inferred_caps.get("thinking", False):
        return 0

    for key, budgets in _THINKING_BUDGETS.items():
        if model_lower.startswith(key.lower()):
            return budgets.get(depth, budgets.get("medium", 4096))

    # 默认预算（支持 thinking 的未知模型）
    return _DEFAULT_THINKING_BUDGET.get(depth, 4096)
