"""obsidian-kb: Markdown vault search, retrieval source, and on_retrieve hook."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)


class ObsidianRetriever:
    source_name = "obsidian"

    def __init__(self, get_config: Any) -> None:
        self._get_config = get_config

    async def retrieve(self, query: str, limit: int = 5) -> list[dict]:
        cfg = self._get_config()
        vault = (cfg.get("vault_path") or "").strip()
        if not vault:
            return []
        vault_path = Path(vault)
        if not vault_path.is_dir():
            return []

        q = (query or "").strip().lower()
        if not q:
            return []

        tokens = [t for t in re.split(r"\W+", q) if len(t) > 1]
        results: list[dict] = []
        for md in sorted(vault_path.rglob("*.md")):
            try:
                text = md.read_text(encoding="utf-8", errors="ignore")
            except OSError as e:
                logger.debug("Skip %s: %s", md, e)
                continue
            hay = text.lower()
            score = 0.0
            if q in hay:
                score += 0.5
            for t in tokens[:8]:
                if t in hay:
                    score += 0.1
            if score <= 0:
                continue
            excerpt = text.strip().replace("\n", " ")[:500]
            results.append(
                {
                    "id": str(md.relative_to(vault_path)),
                    "content": f"## {md.name}\n{excerpt}",
                    "relevance": min(score, 1.0),
                }
            )
            if len(results) >= limit * 4:
                break

        ranked = sorted(results, key=lambda x: -float(x.get("relevance", 0.0)))
        return ranked[:limit]


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        retriever = ObsidianRetriever(api.get_config)
        api.register_retrieval_source(retriever)

        async def inject_knowledge(**kwargs: Any) -> str:
            query = str(
                kwargs.get("query")
                or kwargs.get("enhanced_query")
                or kwargs.get("user_query")
                or ""
            )
            chunks = await retriever.retrieve(query, limit=3)
            if not chunks:
                return ""
            lines = []
            for c in chunks:
                body = (c.get("content") or "")[:400]
                if body:
                    lines.append(body)
            if not lines:
                return ""
            return "\n<!-- obsidian-kb -->\n" + "\n\n".join(lines) + "\n"

        api.register_hook("on_retrieve", inject_knowledge)

        definitions = [
            {
                "type": "function",
                "function": {
                    "name": "obsidian_search",
                    "description": "Search Markdown notes in the configured Obsidian vault path.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of notes to return",
                            },
                        },
                        "required": ["query"],
                    },
                },
            },
        ]

        async def tool_handler(tool_name: str, params: dict) -> str:
            if tool_name != "obsidian_search":
                return ""
            query = str(params.get("query", ""))
            lim = int(params.get("limit", 5))
            results = await retriever.retrieve(query, limit=lim)
            if not results:
                return "No matches in Obsidian vault (check vault_path in plugin config)."
            parts = []
            for r in results:
                parts.append(
                    f"- [{r['id']}] relevance={r.get('relevance', 0):.2f}\n{r.get('content', '')[:400]}"
                )
            return "\n\n".join(parts)

        api.register_tools(definitions, tool_handler)

    def on_unload(self) -> None:
        pass
