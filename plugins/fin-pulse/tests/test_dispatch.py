"""DispatchService red-line tests.

We stub :meth:`PluginAPI.send_message` so unit tests don't need a live
gateway. The dispatch service is the **only** place the plugin calls
out to an IM adapter — locking its behaviour down matters:

* Empty content is accepted and marked ``skipped="empty"``.
* Long content is split by :mod:`finpulse_notification.splitter`.
* Per-key cooldown drops duplicates; content-based dedupe does the
  same without an explicit key.
* An adapter exception on one chunk does **not** abort the batch.
"""

from __future__ import annotations

import asyncio
import time

from finpulse_dispatch import DispatchResult, DispatchService


class _StubAPI:
    """Captures every ``send_message`` call for inspection."""

    def __init__(self, *, fail_chunk_indices: tuple[int, ...] = ()) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.calls = 0
        self._fail_idx = set(fail_chunk_indices)

    def send_message(self, *, channel: str, chat_id: str, text: str) -> None:
        idx = self.calls
        self.calls += 1
        if idx in self._fail_idx:
            raise RuntimeError(f"simulated adapter failure @chunk {idx}")
        self.sent.append((channel, chat_id, text))


def _run(coro):
    return asyncio.run(coro)


def _ds(api: _StubAPI, **kw) -> DispatchService:
    return DispatchService(api, inter_chunk_delay=0.0, **kw)


# ── Basic send paths ─────────────────────────────────────────────────


def test_empty_content_is_no_op() -> None:
    api = _StubAPI()
    ds = _ds(api)
    res = _run(ds.send(channel="feishu", chat_id="u1", content=""))
    assert res.ok is True
    assert res.skipped == "empty"
    assert api.sent == []


def test_short_content_sends_single_chunk() -> None:
    api = _StubAPI()
    ds = _ds(api)
    res = _run(ds.send(channel="feishu", chat_id="u1", content="hello\n"))
    assert res.ok is True
    assert res.sent_chunks == 1
    assert len(api.sent) == 1
    assert api.sent[0][0] == "feishu"
    assert api.sent[0][1] == "u1"
    assert "hello" in api.sent[0][2]


def test_long_content_splits_across_chunks() -> None:
    api = _StubAPI()
    ds = _ds(api, batch_bytes={"feishu": 80})
    content = "\n".join(f"line-{i:02d}" for i in range(30)) + "\n"
    res = _run(ds.send(channel="feishu", chat_id="u1", content=content))
    assert res.ok is True
    assert res.sent_chunks >= 2
    assert len(api.sent) == res.sent_chunks
    combined = "\n".join(text for _, _, text in api.sent)
    # Every line should survive the split
    for i in range(30):
        assert f"line-{i:02d}" in combined


# ── Cooldown paths ──────────────────────────────────────────────────


def test_cooldown_key_suppresses_repeat_send() -> None:
    api = _StubAPI()
    ds = _ds(api)
    first = _run(
        ds.send(
            channel="feishu",
            chat_id="u1",
            content="hello\n",
            cooldown_key="daily:morning:2026-04-24",
            cooldown_s=60,
        )
    )
    assert first.ok is True
    assert first.sent_chunks == 1
    second = _run(
        ds.send(
            channel="feishu",
            chat_id="u1",
            content="hello\n",
            cooldown_key="daily:morning:2026-04-24",
            cooldown_s=60,
        )
    )
    assert second.skipped == "cooldown"
    assert second.sent_chunks == 0
    assert len(api.sent) == 1


def test_content_dedupe_suppresses_same_payload() -> None:
    api = _StubAPI()
    ds = _ds(api)
    _run(
        ds.send(
            channel="feishu",
            chat_id="u1",
            content="exact same content\n",
            cooldown_s=30,
            dedupe_by_content=True,
        )
    )
    res = _run(
        ds.send(
            channel="feishu",
            chat_id="u1",
            content="exact same content\n",
            cooldown_s=30,
            dedupe_by_content=True,
        )
    )
    assert res.skipped == "cooldown"
    assert len(api.sent) == 1


def test_expired_cooldown_allows_resend() -> None:
    api = _StubAPI()
    ds = _ds(api)
    _run(
        ds.send(
            channel="feishu",
            chat_id="u1",
            content="hi\n",
            cooldown_key="k1",
            cooldown_s=0.01,
        )
    )
    time.sleep(0.02)
    res = _run(
        ds.send(
            channel="feishu",
            chat_id="u1",
            content="hi\n",
            cooldown_key="k1",
            cooldown_s=0.01,
        )
    )
    assert res.ok is True
    assert res.skipped is None
    assert len(api.sent) == 2


def test_clear_cooldown_wipes_state() -> None:
    api = _StubAPI()
    ds = _ds(api)
    _run(
        ds.send(
            channel="feishu",
            chat_id="u1",
            content="hi\n",
            cooldown_key="k1",
            cooldown_s=60,
        )
    )
    assert ds.cooldown_snapshot().get("k1") is not None
    ds.clear_cooldown("k1")
    assert ds.cooldown_snapshot().get("k1") is None
    res = _run(
        ds.send(
            channel="feishu",
            chat_id="u1",
            content="hi\n",
            cooldown_key="k1",
            cooldown_s=60,
        )
    )
    assert res.ok is True
    assert len(api.sent) == 2


# ── Failure paths ───────────────────────────────────────────────────


def test_partial_adapter_failure_keeps_remaining_chunks() -> None:
    # Fail the first chunk only — subsequent chunks should still go.
    api = _StubAPI(fail_chunk_indices=(0,))
    ds = _ds(api, batch_bytes={"feishu": 80})
    content = "\n".join(f"line-{i:02d}" for i in range(30)) + "\n"
    res = _run(ds.send(channel="feishu", chat_id="u1", content=content))
    assert res.sent_chunks < res.sent_chunks + len(res.errors)  # errors captured
    assert res.errors, "expected at least one recorded adapter error"
    assert len(api.sent) >= 1


def test_full_failure_does_not_update_cooldown() -> None:
    # Force every send to fail.
    api = _StubAPI(fail_chunk_indices=tuple(range(100)))
    ds = _ds(api)
    res = _run(
        ds.send(
            channel="feishu",
            chat_id="u1",
            content="hello\n",
            cooldown_key="k1",
            cooldown_s=60,
        )
    )
    assert res.ok is False
    assert "k1" not in ds.cooldown_snapshot(), (
        "cooldown must not be stamped when no chunks were delivered"
    )


# ── broadcast() ─────────────────────────────────────────────────────


def test_broadcast_preserves_target_order() -> None:
    api = _StubAPI()
    ds = _ds(api)
    targets = [
        {"channel": "feishu", "chat_id": "u1"},
        {"channel": "dingtalk", "chat_id": "u2"},
        {"channel": "telegram", "chat_id": "u3"},
    ]
    results = _run(ds.broadcast(targets=targets, content="hi\n"))
    assert [r.channel for r in results] == ["feishu", "dingtalk", "telegram"]
    assert all(isinstance(r, DispatchResult) for r in results)
    assert [s[0] for s in api.sent] == ["feishu", "dingtalk", "telegram"]


def test_broadcast_skips_entries_missing_target_fields() -> None:
    api = _StubAPI()
    ds = _ds(api)
    targets = [
        {"channel": "feishu", "chat_id": ""},
        {"channel": "", "chat_id": "u1"},
        {"channel": "feishu", "chat_id": "u2"},
    ]
    results = _run(ds.broadcast(targets=targets, content="hi\n"))
    assert results[0].errors == ["missing_target"]
    assert results[1].errors == ["missing_target"]
    assert results[2].ok is True
    assert len(api.sent) == 1
