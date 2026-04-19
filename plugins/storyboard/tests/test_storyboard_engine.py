"""Tests for storyboard 5-level parser & self-check."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

from storyboard_engine import (  # noqa: E402
    Shot, Storyboard,
    parse_storyboard_llm_output, self_check,
)


# ── parser ────────────────────────────────────────────────────────────


def test_parser_level1_clean_json() -> None:
    text = """{
      "title": "Test",
      "target_duration_sec": 10,
      "shots": [
        {"index": 1, "duration_sec": 5, "visual": "A"},
        {"index": 2, "duration_sec": 5, "visual": "B"}
      ]
    }"""
    sb = parse_storyboard_llm_output(text)
    assert sb.title == "Test"
    assert len(sb.shots) == 2
    assert sb.shots[0].visual == "A"


def test_parser_level2_fenced_json() -> None:
    text = "Sure!\n```json\n{\"title\":\"X\",\"target_duration_sec\":5,\"shots\":[{\"index\":1,\"duration_sec\":5,\"visual\":\"V\"}]}\n```\nDone!"
    sb = parse_storyboard_llm_output(text)
    assert sb.title == "X"
    assert sb.shots[0].visual == "V"


def test_parser_level3_embedded_json() -> None:
    text = "Here you go: {\"title\":\"Y\",\"shots\":[{\"index\":1,\"duration_sec\":3,\"visual\":\"hello\"}]} bye"
    sb = parse_storyboard_llm_output(text)
    assert sb.title == "Y"


def test_parser_level4_numbered_list() -> None:
    text = """1. 镜头一：主角推门进入
    2. 镜头二：镜头跟随到桌前
    3. 镜头三：特写键盘上的猫
    """
    sb = parse_storyboard_llm_output(text, fallback_duration=30)
    assert len(sb.shots) == 3
    assert sb.shots[0].visual.startswith("镜头一")


def test_parser_level5_total_garbage() -> None:
    text = "Sorry, I can't help with that."
    sb = parse_storyboard_llm_output(text, fallback_title="X", fallback_duration=10)
    assert len(sb.shots) == 1
    assert "fallback" in sb.style_notes.lower()


def test_parser_empty_input() -> None:
    sb = parse_storyboard_llm_output("", fallback_title="Z", fallback_duration=5)
    assert sb.title == "Z"
    assert len(sb.shots) == 1


# ── self-check ────────────────────────────────────────────────────────


def test_self_check_passes_balanced() -> None:
    sb = Storyboard(title="T", target_duration_sec=30, shots=[
        Shot(index=i, duration_sec=5, visual=f"shot {i}") for i in range(1, 7)
    ])
    out = self_check(sb)
    assert out.ok
    assert out.duration_match.startswith("✓")
    assert out.distribution_balance.startswith("✓")
    assert out.minimum_count.startswith("✓")
    assert out.suggestions == []


def test_self_check_flags_clustered_shots() -> None:
    sb = Storyboard(title="T", target_duration_sec=30, shots=[
        Shot(index=1, duration_sec=20, visual="A"),
        Shot(index=2, duration_sec=5, visual="B"),
        Shot(index=3, duration_sec=5, visual="C"),
    ])
    out = self_check(sb)
    assert "⚠" in out.distribution_balance
    assert any("分布" in s for s in out.suggestions)


def test_self_check_flags_too_few_shots() -> None:
    sb = Storyboard(title="T", target_duration_sec=30, shots=[
        Shot(index=1, duration_sec=15, visual="A"),
        Shot(index=2, duration_sec=15, visual="B"),
    ])
    out = self_check(sb)
    assert "⚠" in out.minimum_count
