"""Daily-brief renderer — Markdown + HTML outputs for the ``digests`` table.

The HTML template borrows TrendRadar's card-grid aesthetic (source
pill + rank + ago + score) but keeps a single self-contained document
so the digest can be copy-saved as a PNG via ``html2canvas`` from the
Digests tab. Visual tokens mirror ``avatar-studio`` 's CSS variables
so the rendered card looks native when iframed inside the plugin UI.

The renderer itself is pure — it takes a list of article dicts
(``FinpulseTaskManager.list_articles`` rows) and produces blobs. I/O
lives in :mod:`finpulse_pipeline` (Phase 4a entry point).
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence


# ── Data shapes ───────────────────────────────────────────────────────


@dataclass
class DigestStats:
    total_scanned: int = 0
    total_selected: int = 0
    by_source: dict[str, int] = field(default_factory=dict)
    score_bands: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_scanned": self.total_scanned,
            "total_selected": self.total_selected,
            "by_source": dict(self.by_source),
            "score_bands": dict(self.score_bands),
        }


@dataclass
class DigestContext:
    session: str  # morning | noon | evening
    lang: str = "zh"  # zh | en
    top_k: int = 20
    generated_at: str = ""
    title: str | None = None


# ── Selection ─────────────────────────────────────────────────────────


_SESSION_LABELS_ZH = {
    "morning": "财经早报",
    "noon": "财经午报",
    "evening": "财经晚报",
}

_SESSION_LABELS_EN = {
    "morning": "Morning Brief",
    "noon": "Midday Brief",
    "evening": "Evening Brief",
}


def session_label(session: str, *, lang: str = "zh") -> str:
    table = _SESSION_LABELS_ZH if lang == "zh" else _SESSION_LABELS_EN
    return table.get(session, session)


def _band(score: float | None) -> str:
    if score is None:
        return "unscored"
    if score >= 9.0:
        return "critical"
    if score >= 7.0:
        return "important"
    if score >= 5.0:
        return "routine"
    if score >= 3.0:
        return "low"
    return "noise"


def select_articles(
    articles: Sequence[dict[str, Any]], *, top_k: int = 20
) -> tuple[list[dict[str, Any]], DigestStats]:
    """Pick the top ``top_k`` articles for the digest.

    Primary key is ``ai_score`` descending (unscored items fall to the
    tail). Ties break by ``fetched_at`` desc so newer news wins within
    the same band.
    """
    stats = DigestStats()
    stats.total_scanned = len(articles)
    ranked = sorted(
        articles,
        key=lambda a: (
            a.get("ai_score") is not None,
            float(a.get("ai_score") or 0.0),
            a.get("fetched_at") or "",
        ),
        reverse=True,
    )
    selected = ranked[: max(1, min(int(top_k), 60))]
    stats.total_selected = len(selected)
    for a in selected:
        sid = a.get("source_id") or "unknown"
        stats.by_source[sid] = stats.by_source.get(sid, 0) + 1
        stats.score_bands[_band(a.get("ai_score"))] = (
            stats.score_bands.get(_band(a.get("ai_score")), 0) + 1
        )
    return selected, stats


# ── Renderers ──────────────────────────────────────────────────────────


def render_markdown(
    ctx: DigestContext, articles: Sequence[dict[str, Any]], *, stats: DigestStats
) -> str:
    """Plain-markdown digest used by the IM dispatch (the splitter
    chunks this by newline).
    """
    label = session_label(ctx.session, lang=ctx.lang)
    title = ctx.title or f"{label} · {_fmt_date(ctx.generated_at)}"
    lines: list[str] = [f"# {title}", ""]
    if not articles:
        lines.append(
            "_本时段暂无命中资讯，可稍后重试 Ingest。_"
            if ctx.lang == "zh"
            else "_No articles matched this session — retry ingest later._"
        )
        return "\n".join(lines)
    for idx, art in enumerate(articles, start=1):
        score = art.get("ai_score")
        score_text = (
            f" [{float(score):.1f}]" if isinstance(score, (int, float)) else ""
        )
        src = art.get("source_id") or "source"
        when = _fmt_time(art.get("published_at") or art.get("fetched_at"))
        title_line = art.get("title") or ""
        url = art.get("url") or ""
        lines.append(f"{idx}. [{src}]{score_text} {title_line}")
        if url:
            lines.append(f"   {url}")
        if when:
            lines.append(f"   {when}")
    lines.append("")
    lines.append(_footer(ctx.lang, stats))
    return "\n".join(lines)


def render_html(
    ctx: DigestContext, articles: Sequence[dict[str, Any]], *, stats: DigestStats
) -> str:
    """Self-contained HTML blob — safe to iframe or copy-to-PNG.

    Uses the same CSS variables as ``avatar-studio`` so the card reads
    as native inside the plugin UI. Fully inline — zero external CDN
    fetches — so rendering works offline.
    """
    label = session_label(ctx.session, lang=ctx.lang)
    title = html.escape(ctx.title or f"{label} · {_fmt_date(ctx.generated_at)}")

    cards: list[str] = []
    for idx, art in enumerate(articles, start=1):
        score = art.get("ai_score")
        score_html = ""
        if isinstance(score, (int, float)):
            band = _band(score)
            score_html = (
                f'<span class="score score-{band}">{float(score):.1f}</span>'
            )
        src = html.escape(art.get("source_id") or "source")
        ttl = html.escape(art.get("title") or "")
        url = html.escape(art.get("url") or "")
        when = html.escape(
            _fmt_time(art.get("published_at") or art.get("fetched_at")) or ""
        )
        cards.append(
            f"""
<article class="card">
  <header>
    <span class="rank">#{idx}</span>
    <span class="source">{src}</span>
    {score_html}
    <span class="when">{when}</span>
  </header>
  <a class="title" href="{url}" target="_blank" rel="noopener">{ttl}</a>
</article>"""
        )

    cards_html = "\n".join(cards) or '<p class="empty">暂无命中资讯</p>'
    stats_html = _render_stats_block(stats, lang=ctx.lang)

    return _HTML_TEMPLATE.format(
        title=title, cards=cards_html, stats=stats_html
    )


def _render_stats_block(stats: DigestStats, *, lang: str = "zh") -> str:
    zh = lang == "zh"
    total_label = "候选 / 选中" if zh else "Scanned / Selected"
    sources_label = "数据源" if zh else "Sources"
    bands_label = "评分分布" if zh else "Score bands"
    sources = "".join(
        f'<span class="pill">{html.escape(k)}: {v}</span>'
        for k, v in stats.by_source.items()
    )
    bands = "".join(
        f'<span class="pill pill-{html.escape(k)}">{html.escape(k)}: {v}</span>'
        for k, v in stats.score_bands.items()
    )
    return (
        '<div class="stats">'
        f'<div><strong>{total_label}:</strong> '
        f"{stats.total_scanned} / {stats.total_selected}</div>"
        f'<div><strong>{sources_label}:</strong> {sources or "—"}</div>'
        f'<div><strong>{bands_label}:</strong> {bands or "—"}</div>'
        "</div>"
    )


# ── Entry point ───────────────────────────────────────────────────────


def build_daily_brief(
    articles: Sequence[dict[str, Any]],
    *,
    session: str,
    top_k: int = 20,
    lang: str = "zh",
    generated_at: str | None = None,
    title: str | None = None,
) -> tuple[str, str, DigestStats]:
    """Return ``(markdown, html, stats)`` for a daily-brief digest."""
    ctx = DigestContext(
        session=session,
        lang=lang,
        top_k=top_k,
        generated_at=generated_at or _utcnow_iso(),
        title=title,
    )
    selected, stats = select_articles(articles, top_k=top_k)
    md = render_markdown(ctx, selected, stats=stats)
    hb = render_html(ctx, selected, stats=stats)
    return md, hb, stats


# ── Helpers ───────────────────────────────────────────────────────────


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return _utcnow_iso()[:10]
    return iso[:10]


def _fmt_time(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        return iso.replace("T", " ").replace("Z", "").strip()[:16]
    except Exception:  # noqa: BLE001
        return iso


def _footer(lang: str, stats: DigestStats) -> str:
    zh = lang == "zh"
    if zh:
        return (
            f"— 共 {stats.total_selected} 条精选（{stats.total_scanned} 候选），"
            "由 fin-pulse 财经脉动生成"
        )
    return (
        f"— {stats.total_selected} selected ({stats.total_scanned} scanned) "
        "by fin-pulse"
    )


_HTML_TEMPLATE = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
:root {{
  --bg: #ffffff;
  --bg-card: #f8fafc;
  --text: #0f172a;
  --muted: #64748b;
  --primary: #1e3a8a;
  --accent: #14b8a6;
  --radius: 12px;
}}
[data-theme="dark"], body[data-theme="dark"] {{
  --bg: #0f172a;
  --bg-card: #1e293b;
  --text: #f8fafc;
  --muted: #94a3b8;
  --primary: #60a5fa;
}}
body {{
  margin: 0; padding: 24px;
  font-family: -apple-system,Segoe UI,Roboto,"PingFang SC","Microsoft YaHei",sans-serif;
  background: var(--bg); color: var(--text);
}}
h1 {{ margin: 0 0 12px; font-size: 20px; color: var(--primary); }}
.stats {{
  display: flex; flex-wrap: wrap; gap: 12px;
  background: var(--bg-card); border-radius: var(--radius);
  padding: 12px 16px; margin-bottom: 16px; font-size: 13px;
  color: var(--muted);
}}
.stats strong {{ color: var(--text); font-weight: 600; }}
.pill {{
  display: inline-block; padding: 2px 8px;
  border-radius: 999px; background: rgba(30,58,138,0.08); color: var(--primary);
  font-size: 12px; margin-right: 4px;
}}
.pill-critical {{ background: rgba(220,38,38,0.12); color: #dc2626; }}
.pill-important {{ background: rgba(234,88,12,0.12); color: #ea580c; }}
.pill-routine {{ background: rgba(14,165,233,0.12); color: #0284c7; }}
.pill-low {{ background: rgba(100,116,139,0.12); color: var(--muted); }}
.pill-noise {{ background: rgba(100,116,139,0.08); color: var(--muted); }}
.card {{
  background: var(--bg-card); border-radius: var(--radius);
  padding: 12px 16px; margin-bottom: 10px;
}}
.card header {{
  display: flex; align-items: center; gap: 8px;
  font-size: 12px; color: var(--muted); margin-bottom: 4px;
}}
.rank {{ color: var(--primary); font-weight: 600; }}
.source {{
  background: rgba(20,184,166,0.12); color: var(--accent);
  padding: 1px 6px; border-radius: 8px; font-weight: 500;
}}
.score {{ padding: 1px 6px; border-radius: 8px; font-weight: 600; }}
.score-critical {{ background: #dc2626; color: white; }}
.score-important {{ background: #ea580c; color: white; }}
.score-routine {{ background: #0284c7; color: white; }}
.score-low {{ background: rgba(100,116,139,0.2); color: var(--muted); }}
.score-noise {{ background: rgba(100,116,139,0.12); color: var(--muted); }}
.when {{ margin-left: auto; font-size: 11px; }}
.title {{
  display: block; color: var(--text); text-decoration: none;
  font-weight: 500; font-size: 15px; line-height: 1.5;
}}
.title:hover {{ color: var(--primary); text-decoration: underline; }}
.empty {{ color: var(--muted); font-style: italic; }}
</style>
</head>
<body>
<h1>{title}</h1>
{stats}
<section class="cards">
{cards}
</section>
</body>
</html>"""


__all__ = [
    "DigestContext",
    "DigestStats",
    "build_daily_brief",
    "render_html",
    "render_markdown",
    "select_articles",
    "session_label",
]
