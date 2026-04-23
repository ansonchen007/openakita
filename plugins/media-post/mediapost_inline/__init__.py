# ruff: noqa: N999  (parent dir name "media-post" is fixed by plugin id)
"""Vendored helpers for ``media-post`` (Phase 4 routes use these).

The two modules here are 1:1 copies of ``clip_sense_inline.upload_preview``
and ``clip_sense_inline.storage_stats``. They are vendored — not
imported from a sibling plugin — because v1.0 red-line §13 forbids
cross-plugin code imports (each first-class plugin owns its inline
copy of the SDK contrib helpers it needs).
"""
