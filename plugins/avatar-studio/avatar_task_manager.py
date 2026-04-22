"""avatar-studio task manager — pure ``aiosqlite`` CRUD, zero inheritance.

Three tables, no foreign keys (we keep cross-table cleanup explicit):

- ``tasks``    — one row per generation job.
- ``voices``   — system voices are NOT persisted; this table only holds
                 user-cloned cosyvoice-v2 voices so the system catalog stays
                 a pure code constant (see ``avatar_models.SYSTEM_VOICES``).
- ``figures``  — uploaded portrait images (with ``wan2.2-s2v-detect`` cache),
                 selectable from CreateTab as a one-click figure.

Pixelle anti-patterns avoided
-----------------------------
- C1 in-memory task store → here we use SQLite WAL on disk.
- C7 implicit env-var paths → caller hands us an absolute ``db_path``
  derived from ``api.get_data_dir()``; we never read ENV.

The ``update_task_safe`` whitelist is the only path that mutates a task row;
``id`` / ``created_at`` are non-writable.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from types import TracebackType
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id                  TEXT PRIMARY KEY,
    mode                TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    prompt              TEXT NOT NULL DEFAULT '',
    params_json         TEXT NOT NULL DEFAULT '{}',
    dashscope_id        TEXT,
    dashscope_endpoint  TEXT,
    asset_paths_json    TEXT NOT NULL DEFAULT '{}',
    output_path         TEXT,
    output_url          TEXT,
    cost_breakdown_json TEXT,
    error_kind          TEXT,
    error_message       TEXT,
    error_hints_json    TEXT,
    audio_duration_sec  REAL,
    video_duration_sec  REAL,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    completed_at        REAL
);

CREATE TABLE IF NOT EXISTS voices (
    id                  TEXT PRIMARY KEY,
    label               TEXT NOT NULL,
    sample_url          TEXT,
    is_system           INTEGER NOT NULL DEFAULT 0,
    source_audio_path   TEXT,
    dashscope_voice_id  TEXT,
    language            TEXT NOT NULL DEFAULT 'zh-CN',
    gender              TEXT NOT NULL DEFAULT 'unknown',
    created_at          REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS figures (
    id                  TEXT PRIMARY KEY,
    label               TEXT NOT NULL,
    image_path          TEXT NOT NULL,
    preview_url         TEXT NOT NULL,
    detect_pass         INTEGER NOT NULL DEFAULT 0,
    detect_humanoid     INTEGER NOT NULL DEFAULT 0,
    detect_message      TEXT,
    created_at          REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status     ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_mode       ON tasks(mode);
CREATE INDEX IF NOT EXISTS idx_tasks_created    ON tasks(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_voices_system    ON voices(is_system);
CREATE INDEX IF NOT EXISTS idx_figures_created  ON figures(created_at DESC);
"""


# Whitelist for ``update_task_safe`` — a strict allow-list to prevent SQL
# injection via untrusted column names AND to forbid mutating ``id`` /
# ``created_at`` after creation.
_TASK_WRITABLE: frozenset[str] = frozenset(
    {
        "status",
        "dashscope_id",
        "dashscope_endpoint",
        "asset_paths_json",
        "output_path",
        "output_url",
        "cost_breakdown_json",
        "error_kind",
        "error_message",
        "error_hints_json",
        "audio_duration_sec",
        "video_duration_sec",
        "completed_at",
    }
)


_TASK_STATUSES: frozenset[str] = frozenset(
    {"pending", "running", "succeeded", "failed", "cancelled"}
)


def _now() -> float:
    return time.time()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out: dict[str, Any] = dict(row)
    for k in ("params_json", "asset_paths_json", "cost_breakdown_json", "error_hints_json"):
        if k in out and isinstance(out[k], str) and out[k]:
            try:
                out[k.removesuffix("_json")] = json.loads(out[k])
            except (ValueError, TypeError):
                pass
    return out


class AvatarTaskManager:
    """SQLite-backed CRUD for tasks / voices / figures.

    Lifecycle:

        tm = AvatarTaskManager(db_path)
        async with tm:                # opens DB + creates schema
            await tm.create_task(...)

    Or call ``await tm.init()`` / ``await tm.close()`` manually.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def __aenter__(self) -> AvatarTaskManager:
        await self.init()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def init(self) -> None:
        if self._db is not None:
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(SCHEMA_SQL)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            try:
                await self._db.close()
            finally:
                self._db = None

    @property
    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("AvatarTaskManager.init() must be called first")
        return self._db

    # ── Tasks ──────────────────────────────────────────────────────────

    async def create_task(
        self,
        *,
        mode: str,
        prompt: str = "",
        params: dict[str, Any] | None = None,
        asset_paths: dict[str, str] | None = None,
        cost_breakdown: dict[str, Any] | None = None,
    ) -> str:
        """Insert a new task row and return its id."""
        task_id = _new_id("task")
        now = _now()
        await self._conn.execute(
            """
            INSERT INTO tasks (
                id, mode, status, prompt, params_json, asset_paths_json,
                cost_breakdown_json, created_at, updated_at
            ) VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                mode,
                prompt,
                json.dumps(params or {}, ensure_ascii=False),
                json.dumps(asset_paths or {}, ensure_ascii=False),
                json.dumps(cost_breakdown, ensure_ascii=False) if cost_breakdown else None,
                now,
                now,
            ),
        )
        await self._conn.commit()
        return task_id

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        async with self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)) as cur:
            return _row_to_dict(await cur.fetchone())

    async def list_tasks(
        self,
        *,
        status: str | None = None,
        mode: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        binds: list[Any] = []
        if status:
            clauses.append("status = ?")
            binds.append(status)
        if mode:
            clauses.append("mode = ?")
            binds.append(mode)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        # ``ROWID DESC`` is a tiebreaker for equal ``created_at`` (Windows
        # ``time.time()`` granularity is ~15ms and rapid inserts can collide).
        sql = f"SELECT * FROM tasks {where} ORDER BY created_at DESC, ROWID DESC LIMIT ? OFFSET ?"
        binds.extend([max(1, min(200, limit)), max(0, offset)])
        async with self._conn.execute(sql, tuple(binds)) as cur:
            rows = await cur.fetchall()
        return [d for d in (_row_to_dict(r) for r in rows) if d is not None]

    async def update_task_safe(self, task_id: str, /, **updates: Any) -> bool:
        """Update writable columns only. Returns True iff a row was changed.

        - Unknown / read-only columns raise ``ValueError`` (loud failure
          beats silent corruption — Pixelle C6 — and prevents accidental
          mutation of ``id`` / ``created_at``).
        - ``status`` value is validated against ``_TASK_STATUSES``.
        - dict / list values are auto-encoded to JSON for ``*_json`` columns.
        - ``updated_at`` is bumped automatically.
        """
        if not updates:
            return False

        bad = set(updates) - _TASK_WRITABLE
        if bad:
            raise ValueError(
                f"non-writable column(s) for tasks: {sorted(bad)}; "
                f"writable={sorted(_TASK_WRITABLE)}",
            )
        if "status" in updates and updates["status"] not in _TASK_STATUSES:
            raise ValueError(
                f"invalid status {updates['status']!r}; allowed={sorted(_TASK_STATUSES)}",
            )

        cols = list(updates)
        binds: list[Any] = []
        for c in cols:
            v = updates[c]
            if c.endswith("_json") and not isinstance(v, (str, type(None))):
                v = json.dumps(v, ensure_ascii=False)
            binds.append(v)
        binds.append(_now())
        binds.append(task_id)
        sql = f"UPDATE tasks SET {', '.join(f'{c} = ?' for c in cols)}, updated_at = ? WHERE id = ?"
        cursor = await self._conn.execute(sql, tuple(binds))
        await self._conn.commit()
        return cursor.rowcount > 0

    async def delete_task(self, task_id: str) -> bool:
        cur = await self._conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        await self._conn.commit()
        return cur.rowcount > 0

    async def cleanup_expired(self, *, retention_days: int = 30) -> int:
        """Delete tasks older than the retention window. Returns rows removed."""
        cutoff = _now() - max(0, retention_days) * 86400
        cur = await self._conn.execute(
            "DELETE FROM tasks WHERE created_at < ? AND status IN ('succeeded','failed','cancelled')",
            (cutoff,),
        )
        await self._conn.commit()
        return cur.rowcount

    # ── Voices (cloned only — system voices live in code) ─────────────

    async def list_voices(self) -> list[dict[str, Any]]:
        async with self._conn.execute(
            "SELECT * FROM voices WHERE is_system = 0 ORDER BY created_at DESC"
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def create_custom_voice(
        self,
        *,
        label: str,
        source_audio_path: str,
        dashscope_voice_id: str,
        sample_url: str | None = None,
        language: str = "zh-CN",
        gender: str = "unknown",
    ) -> str:
        voice_id = _new_id("vc")
        await self._conn.execute(
            """
            INSERT INTO voices (
                id, label, sample_url, is_system, source_audio_path,
                dashscope_voice_id, language, gender, created_at
            ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?)
            """,
            (
                voice_id,
                label,
                sample_url,
                source_audio_path,
                dashscope_voice_id,
                language,
                gender,
                _now(),
            ),
        )
        await self._conn.commit()
        return voice_id

    async def delete_custom_voice(self, voice_id: str) -> bool:
        cur = await self._conn.execute(
            "DELETE FROM voices WHERE id = ? AND is_system = 0", (voice_id,)
        )
        await self._conn.commit()
        return cur.rowcount > 0

    # ── Figures ───────────────────────────────────────────────────────

    async def list_figures(self) -> list[dict[str, Any]]:
        async with self._conn.execute("SELECT * FROM figures ORDER BY created_at DESC") as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def create_figure(
        self,
        *,
        label: str,
        image_path: str,
        preview_url: str,
        detect_pass: bool = False,
        detect_humanoid: bool = False,
        detect_message: str | None = None,
    ) -> str:
        fig_id = _new_id("fig")
        await self._conn.execute(
            """
            INSERT INTO figures (
                id, label, image_path, preview_url, detect_pass,
                detect_humanoid, detect_message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fig_id,
                label,
                image_path,
                preview_url,
                1 if detect_pass else 0,
                1 if detect_humanoid else 0,
                detect_message,
                _now(),
            ),
        )
        await self._conn.commit()
        return fig_id

    async def delete_figure(self, fig_id: str) -> bool:
        cur = await self._conn.execute("DELETE FROM figures WHERE id = ?", (fig_id,))
        await self._conn.commit()
        return cur.rowcount > 0

    # ── Bulk helpers ──────────────────────────────────────────────────

    async def count(self, table: str = "tasks", *, status: str | None = None) -> int:
        if table not in {"tasks", "voices", "figures"}:
            raise ValueError(f"unknown table {table!r}")
        if status and table != "tasks":
            raise ValueError("status filter only applies to tasks")
        if status:
            sql = "SELECT COUNT(*) FROM tasks WHERE status = ?"
            binds: tuple[Any, ...] = (status,)
        else:
            sql = f"SELECT COUNT(*) FROM {table}"
            binds = ()
        async with self._conn.execute(sql, binds) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def find_pending_dashscope_ids(self) -> Iterable[tuple[str, str, str]]:
        """Return ``(task_id, dashscope_id, dashscope_endpoint)`` for in-flight tasks.

        Used by the on_load polling loop to resume tracking after restart.
        """
        async with self._conn.execute(
            """
            SELECT id, dashscope_id, dashscope_endpoint
            FROM tasks
            WHERE status = 'running' AND dashscope_id IS NOT NULL
            """
        ) as cur:
            rows = await cur.fetchall()
        return [(r["id"], r["dashscope_id"], r["dashscope_endpoint"] or "") for r in rows]
