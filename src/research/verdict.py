"""The shared score/label vocabulary and the write path.

chart / fundamental / news / judge all score a symbol on [-1, 1], map that to
the same 5-level label, and write to data/analysis/{kind}/{symbol}/{date}.json.
Keeping the thresholds and the write path here is what stops the four agents
from drifting apart — the Judge fuses their labels, so they must mean the same
thing.
"""
from __future__ import annotations

import json

from ..core.paths import analysis_path


def clamp_score(score: float) -> float:
    return max(-1.0, min(1.0, float(score)))


def label_from_score(score: float) -> str:
    """5-level label. Thresholds -0.5 / -0.1 / +0.1 / +0.5."""
    s = clamp_score(score)
    if s < -0.5:
        return "strong_sell"
    if s < -0.1:
        return "sell"
    if s <= 0.1:
        return "neutral"
    if s <= 0.5:
        return "buy"
    return "strong_buy"


def clamp_confidence(value, default: float = 0.5) -> float:
    """Model-supplied confidence → [0, 1]; `default` when unparseable."""
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def persist(kind: str, symbol_id: str, date: str, result: dict) -> dict:
    """Write a verdict to data/analysis/{kind}/{symbol}/{YYYYMMDD}.json."""
    path = analysis_path(kind, symbol_id, date.replace("-", ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8")
    return result
