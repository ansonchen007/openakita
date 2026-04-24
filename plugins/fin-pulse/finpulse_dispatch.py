# ruff: noqa: N999
"""Thin wrapper over :meth:`PluginAPI.send_message`.

fin-pulse **never** talks to Feishu / DingTalk / WeWork / Telegram SDKs
directly — the host already ships 7+ IM adapters behind one unified
gateway and only ``channel.send`` is needed. This module therefore
adds exactly three things on top of ``api.send_message``:

1. **Line-boundary batching** (:mod:`finpulse_notification.splitter`)
   so a 25 KB daily brief doesn't get truncated by host adapters that
   otherwise pass the payload through verbatim.
2. **Per-key cooldown** — the same ``cooldown_key`` cannot fire twice
   within ``cooldown_s`` seconds. Digests key on ``daily:{session}:{YYYY-MM-DD}``;
   radar hits key on ``radar:{sha256(text)[:8]}``.
3. **Inter-chunk pacing** (``inter_chunk_delay``) so a 6-chunk radar
   push doesn't trip rate limits on wework / telegram.

No platform-specific payload construction. No webhooks. No SDK imports.
The host adapter is responsible for translating plain text into the
native card / markdown shape for each IM.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from finpulse_notification import DEFAULT_BATCH_BYTES, split_by_lines

logger = logging.getLogger(__name__)


@dataclass
class DispatchResult:
    """Outcome of a single :meth:`DispatchService.send` call."""

    ok: bool
    channel: str
    chat_id: str
    sent_chunks: int = 0
    skipped: str | None = None  # "cooldown" | "empty" | "dedup"
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "channel": self.channel,
            "chat_id": self.chat_id,
            "sent_chunks": self.sent_chunks,
            "skipped": self.skipped,
            "errors": list(self.errors),
        }


def _content_key(channel: str, text: str) -> str:
    """Stable short hash used when ``dedupe_by_content`` is requested."""
    h = hashlib.sha256(f"{channel}::{text}".encode("utf-8")).hexdigest()
    return h[:12]


class DispatchService:
    """One instance per plugin load. Keeps in-memory cooldown state —
    the plugin lifetime is short-lived enough that persisting to SQLite
    only adds I/O without buying real protection.
    """

    def __init__(
        self,
        api: Any,
        *,
        batch_bytes: dict[str, int] | None = None,
        inter_chunk_delay: float = 0.3,
    ) -> None:
        self._api = api
        self._batch_bytes: dict[str, int] = dict(DEFAULT_BATCH_BYTES)
        if batch_bytes:
            self._batch_bytes.update(batch_bytes)
        self._inter_chunk_delay = max(0.0, float(inter_chunk_delay))
        self._cooldown: dict[str, float] = {}

    # ── send / broadcast ─────────────────────────────────────────────

    async def send(
        self,
        *,
        channel: str,
        chat_id: str,
        content: str,
        cooldown_key: str | None = None,
        cooldown_s: float = 0.0,
        dedupe_by_content: bool = False,
        header: str = "",
    ) -> DispatchResult:
        """Push ``content`` to one ``(channel, chat_id)`` target.

        * ``cooldown_key`` + ``cooldown_s`` — if the last successful
          dispatch with the same key was less than ``cooldown_s``
          seconds ago, the call is dropped with ``skipped="cooldown"``.
        * ``dedupe_by_content=True`` additionally short-circuits when
          the exact same text was pushed to the same channel within
          the cooldown window (useful for radar repeat-fire guards).
        * ``header`` is prepended to every follow-up chunk to make
          mid-stream batches self-identify.
        """
        result = DispatchResult(ok=False, channel=channel, chat_id=chat_id)

        text = content or ""
        if not text.strip():
            result.skipped = "empty"
            result.ok = True
            return result

        now = time.time()
        effective_keys: list[str] = []
        if cooldown_key:
            effective_keys.append(cooldown_key)
        if dedupe_by_content:
            effective_keys.append(_content_key(channel, text))
        for k in effective_keys:
            last = self._cooldown.get(k)
            if last is not None and cooldown_s > 0 and now - last < cooldown_s:
                result.skipped = "cooldown"
                result.ok = True
                return result

        max_bytes = self._batch_bytes.get(channel, self._batch_bytes["default"])
        try:
            chunks = split_by_lines(
                text, footer="", max_bytes=max_bytes, base_header=header
            )
        except ValueError as exc:
            logger.warning("splitter rejected payload for %s: %s", channel, exc)
            result.errors.append(f"splitter:{exc}")
            return result

        if not chunks:
            result.skipped = "empty"
            result.ok = True
            return result

        sent = 0
        for i, chunk in enumerate(chunks):
            try:
                # ``api.send_message`` is fire-and-forget on the host
                # side — it schedules a task on the running loop. We
                # still await a tiny sleep between chunks to stay
                # adapter-rate-friendly.
                self._api.send_message(
                    channel=channel, chat_id=chat_id, text=chunk
                )
                sent += 1
            except Exception as exc:  # noqa: BLE001 — defensive boundary
                logger.warning(
                    "dispatch chunk %d/%d failed on %s: %s",
                    i + 1,
                    len(chunks),
                    channel,
                    exc,
                )
                result.errors.append(str(exc))
                continue
            if i < len(chunks) - 1 and self._inter_chunk_delay > 0:
                try:
                    await asyncio.sleep(self._inter_chunk_delay)
                except asyncio.CancelledError:
                    raise

        result.sent_chunks = sent
        result.ok = sent > 0

        if result.ok:
            for k in effective_keys:
                self._cooldown[k] = now
        return result

    async def broadcast(
        self,
        *,
        targets: list[dict[str, str]],
        content: str,
        cooldown_key: str | None = None,
        cooldown_s: float = 0.0,
        dedupe_by_content: bool = False,
        header: str = "",
    ) -> list[DispatchResult]:
        """Fan ``content`` out to multiple targets in order. Each entry
        must carry at least ``channel`` and ``chat_id``; a missing pair
        surfaces as a ``DispatchResult`` with ``errors=["missing_target"]``
        so callers can log the bad entry without aborting the batch.
        """
        results: list[DispatchResult] = []
        for target in targets:
            channel = (target.get("channel") or "").strip()
            chat_id = (target.get("chat_id") or "").strip()
            if not channel or not chat_id:
                results.append(
                    DispatchResult(
                        ok=False,
                        channel=channel or "",
                        chat_id=chat_id or "",
                        errors=["missing_target"],
                    )
                )
                continue
            res = await self.send(
                channel=channel,
                chat_id=chat_id,
                content=content,
                cooldown_key=cooldown_key,
                cooldown_s=cooldown_s,
                dedupe_by_content=dedupe_by_content,
                header=header,
            )
            results.append(res)
        return results

    # ── cooldown controls ────────────────────────────────────────────

    def clear_cooldown(self, key: str | None = None) -> None:
        """Reset either a single cooldown key or the entire map.

        Used by unit tests and by the Settings → Schedules 「立即再推」
        button which bypasses the daily-digest cooldown.
        """
        if key is None:
            self._cooldown.clear()
        else:
            self._cooldown.pop(key, None)

    def cooldown_snapshot(self) -> dict[str, float]:
        """Read-only view of the cooldown map — useful for ``/health``
        and manual debugging from the UI.
        """
        return dict(self._cooldown)


__all__ = ["DispatchResult", "DispatchService"]
