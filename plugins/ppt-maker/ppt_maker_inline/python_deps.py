"""Placeholder for whitelist-only optional dependency management.

Phase 11 replaces this shell with a full manager modeled after idea-research.
"""

from __future__ import annotations


OPTIONAL_DEP_GROUPS: dict[str, list[str]] = {
    "table": ["pandas", "openpyxl"],
    "export": ["python-pptx", "matplotlib"],
    "document": ["pypdf", "python-docx"],
}


def list_optional_groups() -> dict[str, list[str]]:
    return dict(OPTIONAL_DEP_GROUPS)

