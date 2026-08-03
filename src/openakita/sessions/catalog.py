"""Lightweight random-access index for the canonical sessions.json store."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openakita.utils.atomic_io import atomic_json_write, safe_write

CATALOG_VERSION = 1


@dataclass(frozen=True)
class SessionCatalogEntry:
    session_key: str
    offset: int
    length: int
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_key": self.session_key,
            "offset": self.offset,
            "length": self.length,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, value: Any) -> SessionCatalogEntry | None:
        if not isinstance(value, dict):
            return None
        session_key = value.get("session_key")
        offset = value.get("offset")
        length = value.get("length")
        summary = value.get("summary")
        if (
            not isinstance(session_key, str)
            or not session_key
            or not isinstance(offset, int)
            or offset < 0
            or not isinstance(length, int)
            or length <= 0
            or not isinstance(summary, dict)
        ):
            return None
        return cls(session_key=session_key, offset=offset, length=length, summary=summary)


class SessionCatalogIndexWriteError(OSError):
    """The session store was replaced but its companion index was not."""

    def __init__(self, entries: dict[str, SessionCatalogEntry], cause: Exception):
        super().__init__(f"failed to write session catalog index: {cause}")
        self.entries = entries


class SessionCatalogStore:
    """Keep compact session summaries separate from full session documents.

    ``sessions.json`` remains the canonical, backwards-compatible array. The
    adjacent index records byte ranges for each array item so a single session
    can be hydrated without parsing every conversation and message at startup.
    """

    def __init__(self, sessions_file: Path):
        self.sessions_file = Path(sessions_file)
        self.index_file = self.sessions_file.with_name("sessions.index.json")

    def load(self) -> dict[str, SessionCatalogEntry] | None:
        if not self.sessions_file.exists() or not self.index_file.exists():
            return None
        try:
            with open(self.index_file, encoding="utf-8") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict) or payload.get("version") != CATALOG_VERSION:
                return None

            stat = self.sessions_file.stat()
            if payload.get("source_size") != stat.st_size:
                return None
            if payload.get("source_mtime_ns") != stat.st_mtime_ns:
                return None

            raw_entries = payload.get("sessions")
            if not isinstance(raw_entries, list):
                return None

            entries: dict[str, SessionCatalogEntry] = {}
            for raw_entry in raw_entries:
                entry = SessionCatalogEntry.from_dict(raw_entry)
                if entry is None or entry.offset + entry.length > stat.st_size:
                    return None
                if entry.session_key in entries:
                    return None
                entries[entry.session_key] = entry
            return entries
        except (OSError, ValueError, TypeError):
            return None

    def read_document(self, entry: SessionCatalogEntry) -> dict[str, Any] | None:
        try:
            with open(self.sessions_file, "rb") as handle:
                handle.seek(entry.offset)
                raw = handle.read(entry.length)
            value = json.loads(raw.decode("utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    def read_raw_document(self, entry: SessionCatalogEntry) -> str | None:
        try:
            with open(self.sessions_file, "rb") as handle:
                handle.seek(entry.offset)
                raw = handle.read(entry.length)
            return raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def write(
        self,
        documents: Iterable[tuple[str, str | dict[str, Any], dict[str, Any]]],
    ) -> dict[str, SessionCatalogEntry]:
        parts = ["["]
        byte_offset = 1
        entries: dict[str, SessionCatalogEntry] = {}

        for index, (session_key, document, summary) in enumerate(documents):
            if session_key in entries:
                raise ValueError(f"duplicate session key: {session_key}")
            raw = (
                document.strip()
                if isinstance(document, str)
                else json.dumps(document, ensure_ascii=False, separators=(",", ":"))
            )
            if not raw.startswith("{") or not raw.endswith("}"):
                raise ValueError(f"invalid session document: {session_key}")
            if index:
                parts.append(",")
                byte_offset += 1
            raw_length = len(raw.encode("utf-8"))
            entries[session_key] = SessionCatalogEntry(
                session_key=session_key,
                offset=byte_offset,
                length=raw_length,
                summary=dict(summary),
            )
            parts.append(raw)
            byte_offset += raw_length

        parts.append("]\n")
        safe_write(
            self.sessions_file,
            "".join(parts),
            backup=True,
            fsync=True,
            allow_fallback=False,
        )
        try:
            stat = self.sessions_file.stat()
            atomic_json_write(
                self.index_file,
                {
                    "version": CATALOG_VERSION,
                    "source_size": stat.st_size,
                    "source_mtime_ns": stat.st_mtime_ns,
                    "sessions": [entry.to_dict() for entry in entries.values()],
                },
                indent=None,
                fsync=True,
                allow_fallback=False,
            )
        except Exception as exc:
            raise SessionCatalogIndexWriteError(entries, exc) from exc
        return entries
