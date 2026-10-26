# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lollapalooza <https://github.com/aqua5230>

from __future__ import annotations

import time
from pathlib import Path

from ui import history_report

NOW = time.time()
SERIES = [
    {
        "provider": "codex",
        "session": [[NOW - 86400, 10.0], [NOW - 3600, 80.0], [NOW, 18.0]],
        "weekly": [[NOW - 86400, 5.0], [NOW, 50.0]],
    },
    {
        "provider": "claude",
        "session": [[NOW - 3600, 100.0], [NOW, 7.0]],
        "weekly": [[NOW, 27.0]],
    },
]


def test_generate_history_html_contains_charts_and_stats() -> None:
    html = history_report.generate_history_html(SERIES)
    assert "Quota history" in html
    assert "Codex" in html and "Claude" in html
    assert html.count("<polyline") >= 3
    assert "80%" in html  # codex session peak in table/chart data
    # Claude (order 0) must render before Codex (order 1).
    assert html.index("Claude") < html.index("Codex")


def test_save_history_report_writes_html(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "quota.quota_history.payload_series", lambda path=None: SERIES
    )
    out = tmp_path / "history.html"
    saved = history_report.save_history_report("html", out_path=str(out))
    assert saved == str(out)
    assert "Quota history" in out.read_text(encoding="utf-8")


def test_save_history_report_pdf_falls_back_without_chrome(
    tmp_path: Path, monkeypatch  # type: ignore[no-untyped-def]
) -> None:
    monkeypatch.setattr(
        "quota.quota_history.payload_series", lambda path=None: SERIES
    )
    monkeypatch.setattr(history_report, "_chrome_binary", lambda: None)
    monkeypatch.setattr(history_report.sys, "platform", "linux")
    out = tmp_path / "history.html"
    saved = history_report.save_history_report("pdf", out_path=str(out))
    assert saved == str(out)
    assert out.exists()
