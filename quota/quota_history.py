# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lollapalooza <https://github.com/aqua5230>
"""Record session/weekly quota percentages over time for the History card.

Samples live in ~/.usage/quota_history.json (CodexBar-style entries:
capturedAt + usedPercent per window). On first use the file is seeded from
CodexBar's history files when present, so the charts are not empty.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HISTORY_PATH = Path.home() / ".usage/quota_history.json"
CODEXBAR_HISTORY_DIR = (
    Path.home() / "Library/Application Support/com.steipete.codexbar/history"
)
MIN_INTERVAL_SECONDS = 300.0
RETENTION_SECONDS = 35 * 24 * 3600.0
MAX_POINTS_PER_SERIES = 400

logger = logging.getLogger(__name__)

_cache_key: tuple[float, int] | None = None
_cache_samples: list[dict[str, Any]] = []

# provider id -> (session attribute, weekly attribute) on PopoverState rows
_STATE_ROWS = (
    ("claude", "claude_session", "claude_weekly"),
    ("codex", "codex_session", "codex_weekly"),
    ("agy", "agy_session", "agy_weekly"),
    ("grok", None, "grok_weekly"),
    ("muse", "muse_session", "muse_weekly"),
)


def _now() -> float:
    return datetime.now(UTC).timestamp()


def _load(path: Path = HISTORY_PATH) -> list[dict[str, Any]]:
    global _cache_key, _cache_samples
    try:
        stat = path.stat()
    except OSError:
        return []
    key = (stat.st_mtime, stat.st_size)
    if key == _cache_key:
        return _cache_samples
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        samples = data.get("samples", []) if isinstance(data, dict) else []
    except (OSError, ValueError):
        samples = []
    _cache_key, _cache_samples = key, samples
    return samples


def _save(samples: list[dict[str, Any]], path: Path = HISTORY_PATH) -> None:
    global _cache_key
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
        ) as handle:
            temporary = handle.name
            json.dump({"version": 1, "samples": samples}, handle)
        os.replace(temporary, path)
        _cache_key = None
    except OSError:
        logger.warning("Quota history write failed", exc_info=True)


def _parse_ts(value: str) -> float | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def seed_from_codexbar(
    history_path: Path = HISTORY_PATH,
    codexbar_dir: Path = CODEXBAR_HISTORY_DIR,
) -> int:
    """Import CodexBar history files as samples. Returns sample count added."""
    if _load(history_path):
        return 0
    # (timestamp, provider) -> {"s": pct, "w": pct}
    merged: dict[tuple[float, str], dict[str, float]] = {}
    for provider in ("codex", "claude", "antigravity"):
        file = codexbar_dir / f"{provider}.json"
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        name = "agy" if provider == "antigravity" else provider
        for windows in data.get("accounts", {}).values():
            for window in windows:
                kind = "s" if window.get("name") == "session" else "w"
                for entry in window.get("entries", []):
                    ts = _parse_ts(entry.get("capturedAt", ""))
                    pct = entry.get("usedPercent")
                    if ts is None or not isinstance(pct, (int, float)):
                        continue
                    merged.setdefault((ts, name), {})[kind] = float(pct)
    samples = [
        {"t": ts, "p": {provider: kinds}}
        for (ts, provider), kinds in sorted(merged.items())
    ]
    # One sample per timestamp across providers reads better in the payload.
    by_ts: dict[float, dict[str, Any]] = {}
    for sample in samples:
        by_ts.setdefault(sample["t"], {}).update(sample["p"])
    samples = [{"t": ts, "p": p} for ts, p in sorted(by_ts.items())]
    if samples:
        _save(samples, history_path)
    return len(samples)


def record_snapshot(
    providers: dict[str, dict[str, float | None]],
    *,
    now: float | None = None,
    path: Path = HISTORY_PATH,
) -> bool:
    """Append one sample. Throttled to MIN_INTERVAL_SECONDS; prunes old data."""
    now = _now() if now is None else now
    samples = _load(path)
    if not samples:
        seed_from_codexbar(path)
        samples = _load(path)
    if samples and now - float(samples[-1].get("t", 0)) < MIN_INTERVAL_SECONDS:
        return False
    cleaned = {
        provider: {k: v for k, v in kinds.items() if v is not None}
        for provider, kinds in providers.items()
    }
    cleaned = {p: k for p, k in cleaned.items() if k}
    if not cleaned:
        return False
    samples.append({"t": now, "p": cleaned})
    cutoff = now - RETENTION_SECONDS
    samples = [s for s in samples if float(s.get("t", 0)) >= cutoff]
    _save(samples, path)
    return True


def record_from_state(state: Any) -> None:
    """Record the current PopoverState quota rows. Never raises."""
    try:
        providers: dict[str, dict[str, float | None]] = {}
        for provider, session_attr, weekly_attr in _STATE_ROWS:
            kinds: dict[str, float | None] = {}
            for kind, attr in (("s", session_attr), ("w", weekly_attr)):
                row = getattr(state, attr, None) if attr else None
                kinds[kind] = (
                    float(row.percent)
                    if row is not None and getattr(row, "available", False) and row.percent is not None
                    else None
                )
            providers[provider] = kinds
        record_snapshot(providers)
    except Exception:  # history must never break a refresh
        logger.warning("Quota history record failed", exc_info=True)


def _downsample(points: list[list[float]]) -> list[list[float]]:
    if len(points) <= MAX_POINTS_PER_SERIES:
        return points
    step = len(points) / MAX_POINTS_PER_SERIES
    return [points[int(i * step)] for i in range(MAX_POINTS_PER_SERIES)]


def payload_series(path: Path = HISTORY_PATH) -> list[dict[str, Any]]:
    """Series for the panel payload: [{provider, session:[[t,pct]], weekly:[]}]."""
    samples = _load(path)
    if not samples:
        seed_from_codexbar(path)
        samples = _load(path)
    series: dict[str, dict[str, list[list[float]]]] = {}
    for sample in samples:
        ts = float(sample.get("t", 0))
        for provider, kinds in sample.get("p", {}).items():
            entry = series.setdefault(provider, {"session": [], "weekly": []})
            if "s" in kinds:
                entry["session"].append([ts, float(kinds["s"])])
            if "w" in kinds:
                entry["weekly"].append([ts, float(kinds["w"])])
    return [
        {
            "provider": provider,
            "session": _downsample(kinds["session"]),
            "weekly": _downsample(kinds["weekly"]),
        }
        for provider, kinds in sorted(series.items())
        if kinds["session"] or kinds["weekly"]
    ]
