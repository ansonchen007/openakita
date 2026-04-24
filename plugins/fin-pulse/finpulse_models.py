# ruff: noqa: N999
"""Static metadata constants for fin-pulse.

Pure data — no I/O, no runtime state, no host dependency — so the module
can be imported from the plugin entry, the pipeline, every fetcher, and
the unit-test harness without pulling in aiosqlite or httpx.

Sections:

* :data:`MODES` — the three canonical V1.0 pipeline modes
  (``daily_brief`` / ``hot_radar`` / ``ask_news``) plus the shared
  ``ingest`` staging mode, with display names and required params.
* :data:`ERROR_HINTS` — the nine standardised ``error_kind`` categories
  with bilingual operator hints, aligned to ``footage-gate`` /
  ``avatar-studio`` / ``subtitle-craft`` so the host task-detail panel
  renders one uniform badge.
* :data:`SOURCE_DEFS` — the eight finance-first sources plus the
  generic RSS aggregator slot and the optional NewsNow enhancer.
  ``source_id`` is the primary key used by ``articles.source_id`` and
  the ``config['source.{id}.last_ok']`` health probe keys.
* :data:`SESSIONS` — ``morning`` / ``noon`` / ``evening`` labels for the
  ``daily_brief`` mode; the :data:`DEFAULT_CRONS` map ships the
  Mon-Fri defaults surfaced in the Settings → Schedules section.
"""

from __future__ import annotations

from typing import Final

# ── Modes ────────────────────────────────────────────────────────────────


MODES: Final[dict[str, dict[str, object]]] = {
    # "ingest" is a staging helper — crawlers land articles into SQLite
    # without rendering a digest. Exposed so the UI can trigger a
    # source-only refresh without spinning up daily_brief.
    "ingest": {
        "display_zh": "抓取归一",
        "display_en": "Ingest",
        "catalog_id": "IN0",
        "default_params": {"sources": "*", "since_hours": 24},
    },
    "daily_brief": {
        "display_zh": "早午晚报",
        "display_en": "Daily Brief",
        "catalog_id": "DB1",
        "sessions": ("morning", "noon", "evening"),
        "default_params": {"session": "morning", "top_k": 20},
    },
    "hot_radar": {
        "display_zh": "热点雷达",
        "display_en": "Hot Radar",
        "catalog_id": "HR1",
        "default_params": {"min_score": 7.0, "cooldown_sec": 1800},
    },
    "ask_news": {
        "display_zh": "Agent 问询",
        "display_en": "Ask News",
        "catalog_id": "AN1",
        "default_params": {},
    },
}

MODE_IDS: Final[tuple[str, ...]] = tuple(MODES.keys())

SESSIONS: Final[tuple[str, ...]] = ("morning", "noon", "evening")

# Weekday cron defaults (Mon-Fri) for the three daily_brief sessions.
# Users can override in Settings → Schedules before calling
# ``POST /schedules``.
DEFAULT_CRONS: Final[dict[str, str]] = {
    "morning": "0 9 * * 1-5",
    "noon": "0 13 * * 1-5",
    "evening": "0 22 * * 1-5",
}


# ── Error categories ─────────────────────────────────────────────────────


ERROR_HINTS: Final[dict[str, dict[str, list[str]]]] = {
    "network": {
        "zh": ["请检查网络连接", "若使用代理请确认 NewsNow/RSS 源可达"],
        "en": [
            "Check your network connection",
            "If behind a proxy, verify NewsNow / RSS feeds are reachable",
        ],
    },
    "timeout": {
        "zh": ["请在 Settings → Sources 调低并发", "或延长 fetcher 超时阈值后重试"],
        "en": [
            "Lower the concurrency in Settings → Sources",
            "Or extend the fetcher timeout and retry",
        ],
    },
    "auth": {
        "zh": ["请检查 LLM / webhook 的 API Key", "或在 Settings 重新填写"],
        "en": [
            "Check the LLM / webhook API key",
            "Re-enter the credential in Settings",
        ],
    },
    "quota": {
        "zh": ["LLM 或源站配额超限", "可切换宿主 LLM 端点或等待重置"],
        "en": [
            "LLM or upstream quota exceeded",
            "Switch host LLM endpoint or wait for quota reset",
        ],
    },
    "rate_limit": {
        "zh": ["抓取过于频繁被限流", "请在 Settings → Sources 拉长抓取间隔"],
        "en": [
            "Source or webhook rate-limited",
            "Extend the crawl interval in Settings → Sources",
        ],
    },
    "dependency": {
        "zh": ["缺少必要依赖（PyExecJS / Node / feedparser 等）", "请按 VALIDATION.md 安装"],
        "en": [
            "Missing runtime dependency (PyExecJS / Node / feedparser, etc.)",
            "Follow VALIDATION.md for installation",
        ],
    },
    "moderation": {
        "zh": ["LLM 内容审核拒绝", "可切换 provider 或调整 prompt 后重试"],
        "en": [
            "LLM content moderation rejected the request",
            "Switch provider or adjust the prompt and retry",
        ],
    },
    "not_found": {
        "zh": ["源站 404 或游标越界", "请在 Settings → Sources 点测试连接并重置游标"],
        "en": [
            "Upstream 404 or cursor out of range",
            "Click Test in Settings → Sources and reset the cursor",
        ],
    },
    "unknown": {
        "zh": ["请复制 task_id 反馈给维护者", "或截图 Tasks 详情页 metadata"],
        "en": [
            "Report the task_id to the maintainer",
            "Or screenshot the Tasks detail-page metadata JSON",
        ],
    },
}

ERROR_KINDS: Final[tuple[str, ...]] = tuple(ERROR_HINTS.keys())


# ── Data sources ─────────────────────────────────────────────────────────
#
# Eight prime finance sources ship enabled by default; the generic RSS
# aggregator is on by default with an empty feed list; NewsNow is off by
# default and surfaces a Settings wizard (§11.7 of the plan).


SOURCE_DEFS: Final[dict[str, dict[str, object]]] = {
    "wallstreetcn": {
        "display_zh": "华尔街见闻",
        "display_en": "WallStreet CN",
        "kind": "rss_first",
        "default_enabled": True,
        "homepage": "https://wallstreetcn.com/",
    },
    "cls": {
        "display_zh": "财联社电报",
        "display_en": "CLS Telegram",
        "kind": "api",
        "default_enabled": True,
        "homepage": "https://www.cls.cn/telegraph",
    },
    "xueqiu": {
        "display_zh": "雪球热帖",
        "display_en": "XueQiu Hot",
        "kind": "rss",
        "default_enabled": True,
        "homepage": "https://xueqiu.com/hots/rss",
    },
    "eastmoney": {
        "display_zh": "东方财富快讯",
        "display_en": "EastMoney News",
        "kind": "api",
        "default_enabled": True,
        "homepage": "https://www.eastmoney.com/",
    },
    "pbc_omo": {
        "display_zh": "央行公开市场",
        "display_en": "PBC OMO",
        "kind": "html_execjs",
        "default_enabled": True,
        "homepage": "http://www.pbc.gov.cn/",
    },
    "nbs": {
        "display_zh": "国家统计局",
        "display_en": "NBS of China",
        "kind": "rss_first",
        "default_enabled": True,
        "homepage": "https://www.stats.gov.cn/",
    },
    "fed_fomc": {
        "display_zh": "美联储 FOMC",
        "display_en": "Fed FOMC",
        "kind": "calendar_html",
        "default_enabled": True,
        "homepage": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    },
    "sec_edgar": {
        "display_zh": "美国 SEC EDGAR",
        "display_en": "SEC EDGAR",
        "kind": "rss",
        "default_enabled": True,
        "homepage": "https://www.sec.gov/cgi-bin/browse-edgar",
    },
    "rss_generic": {
        "display_zh": "自定义 RSS",
        "display_en": "Custom RSS",
        "kind": "rss",
        "default_enabled": True,
        "homepage": "",
    },
    "newsnow": {
        "display_zh": "NewsNow 聚合",
        "display_en": "NewsNow Aggregator",
        "kind": "newsnow",
        # Public aggregator is on by default; the pipeline enforces a
        # 5-minute floor so we never hammer the volunteer-run node.
        "default_enabled": True,
        "homepage": "https://github.com/ourongxing/newsnow",
    },
}

SOURCE_IDS: Final[tuple[str, ...]] = tuple(SOURCE_DEFS.keys())


# ── Scoring scale (Horizon-style 0-10) ───────────────────────────────────
#
# Surfaced in the ai prompt (Phase 3) and the /articles default filter.

SCORE_THRESHOLDS: Final[dict[str, float]] = {
    "critical": 9.0,  # central-bank rate decisions / regulatory surprises
    "important": 7.0,  # major macro data, prime earnings
    "routine": 5.0,  # sector reports, ordinary announcements
    "low": 3.0,  # general tech / entertainment
    "noise": 0.0,  # ads, fluff
}


__all__ = [
    "DEFAULT_CRONS",
    "ERROR_HINTS",
    "ERROR_KINDS",
    "MODE_IDS",
    "MODES",
    "SCORE_THRESHOLDS",
    "SESSIONS",
    "SOURCE_DEFS",
    "SOURCE_IDS",
]
