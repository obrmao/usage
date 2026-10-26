# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lollapalooza <https://github.com/aqua5230>

from __future__ import annotations

import json
from pathlib import Path

from quota import quota_history


def _codexbar_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "codexbar"
    directory.mkdir(exist_ok=True)
    (directory / "codex.json").write_text(
        json.dumps(
            {
                "accounts": {
                    "acct-1": [
                        {
                            "name": "session",
                            "windowMinutes": 300,
                            "entries": [
                                {"capturedAt": "2026-10-20T00:00:00Z", "usedPercent": 10},
                                {"capturedAt": "2026-10-21T00:00:00Z", "usedPercent": 80},
                            ],
                        },
                        {
                            "name": "weekly",
                            "windowMinutes": 10080,
                            "entries": [
                                {"capturedAt": "2026-10-20T00:00:00Z", "usedPercent": 5},
                                {"capturedAt": "2026-10-21T00:00:00Z", "usedPercent": 40},
                            ],
                        },
                    ]
                },
                "preferredAccountKey": "acct-1",
                "version": 1,
            }
        )
    )
    return directory


def test_seed_from_codexbar_merges_windows_by_timestamp(tmp_path: Path) -> None:
    history = tmp_path / "quota_history.json"
    added = quota_history.seed_from_codexbar(history, _codexbar_dir(tmp_path))
    assert added == 2
    series = quota_history.payload_series(history)
    assert len(series) == 1
    assert series[0]["provider"] == "codex"
    assert [point[1] for point in series[0]["session"]] == [10.0, 80.0]
    assert [point[1] for point in series[0]["weekly"]] == [5.0, 40.0]
    # Seeding is one-shot: a second call must not duplicate samples.
    assert quota_history.seed_from_codexbar(history, _codexbar_dir(tmp_path)) == 0


def test_record_snapshot_throttles_and_prunes(tmp_path: Path) -> None:
    history = tmp_path / "quota_history.json"
    base = 1_800_000_000.0
    assert quota_history.record_snapshot(
        {"claude": {"s": 10.0, "w": 20.0}}, now=base, path=history
    )
    # Inside the throttle window: rejected.
    assert not quota_history.record_snapshot(
        {"claude": {"s": 11.0, "w": 21.0}}, now=base + 60, path=history
    )
    assert quota_history.record_snapshot(
        {"claude": {"s": 12.0, "w": None}, "grok": {"w": 30.0}},
        now=base + quota_history.MIN_INTERVAL_SECONDS + 1,
        path=history,
    )
    series = {entry["provider"]: entry for entry in quota_history.payload_series(history)}
    assert [point[1] for point in series["claude"]["session"]] == [10.0, 12.0]
    assert [point[1] for point in series["claude"]["weekly"]] == [20.0]
    assert [point[1] for point in series["grok"]["weekly"]] == [30.0]
    # A sample older than retention is pruned on the next write.
    assert quota_history.record_snapshot(
        {"claude": {"s": 1.0}},
        now=base + quota_history.RETENTION_SECONDS + 1000,
        path=history,
    )
    series = {entry["provider"]: entry for entry in quota_history.payload_series(history)}
    assert [point[1] for point in series["claude"]["session"]] == [1.0]
