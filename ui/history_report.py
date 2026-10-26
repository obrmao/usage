# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lollapalooza <https://github.com/aqua5230>
"""Export quota history (session/weekly %) as a standalone HTML or PDF file."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROVIDERS = {
    "claude": ("Claude", "#e8590c"),
    "codex": ("Codex", "#1098ad"),
    "agy": ("Antigravity", "#66a80f"),
    "grok": ("Grok", "#868e96"),
    "muse": ("Muse", "#868e96"),
}
CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)


def _chart(points_s: list, points_w: list, color: str, start: float, end: float) -> str:
    width, height, left, top, bottom = 860, 220, 44, 14, 182
    span = max(1.0, end - start)

    def x(ts: float) -> float:
        return left + (ts - start) / span * (width - left - 12)

    def y(v: float) -> float:
        return top + (1 - max(0.0, min(100.0, v)) / 100) * (bottom - top)

    def line(points: list, dashed: bool) -> str:
        if len(points) < 2:
            return ""
        pts = " ".join(f"{x(t):.1f},{y(v):.1f}" for t, v in points)
        dash = ' stroke-dasharray="6 5" opacity="0.7"' if dashed else ""
        return f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="{1.6 if dashed else 2.2}"{dash} stroke-linejoin="round"/>'

    area = ""
    if len(points_s) >= 2:
        path = " L".join(f"{x(t):.1f},{y(v):.1f}" for t, v in points_s)
        area = f'<path d="M{path} L{x(points_s[-1][0]):.1f},{bottom} L{x(points_s[0][0]):.1f},{bottom} Z" fill="{color}" opacity="0.08"/>'
    grid = "".join(
        f'<line x1="{left}" y1="{y(v)}" x2="{width - 12}" y2="{y(v)}" stroke="#dee2e6"/>'
        f'<text x="{left - 6}" y="{y(v) + 4}" text-anchor="end" font-size="11" fill="#868e96">{v}%</text>'
        for v in (0, 25, 50, 75, 100)
    )

    def label(ts: float) -> str:
        return datetime.fromtimestamp(ts, UTC).strftime("%d %b")

    ticks = "".join(
        f'<text x="{x(ts):.0f}" y="206" text-anchor="middle" font-size="11" fill="#868e96">{label(ts)}</text>'
        for ts in (start, start + span / 2, end)
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">'
        f"{grid}{area}{line(points_w, True)}{line(points_s, False)}{ticks}</svg>"
    )


def _stats(points: list, cutoff: float) -> tuple[float | None, float | None]:
    recent = [v for t, v in points if t >= cutoff]
    if not points:
        return None, None
    return points[-1][1], (max(recent) if recent else None)


def generate_history_html(series: list[dict[str, Any]], *, days: int = 30) -> str:
    """Render the quota history page. `series` is quota_history.payload_series()."""
    now = datetime.now(UTC).timestamp()
    start, cutoff7 = now - days * 86400, now - 7 * 86400
    order = {key: index for index, key in enumerate(PROVIDERS)}
    cards, rows = [], []
    for entry in sorted(series, key=lambda e: order.get(e["provider"], 99)):
        name, color = PROVIDERS.get(entry["provider"], (entry["provider"], "#868e96"))
        session = [p for p in entry["session"] if p[0] >= start]
        weekly = [p for p in entry["weekly"] if p[0] >= start]
        now_s, peak_s = _stats(session, cutoff7)
        now_w, peak_w = _stats(weekly, cutoff7)
        fmt = lambda v: "–" if v is None else f"{v:.0f}%"  # noqa: E731
        rows.append(
            f"<tr><td>{name}</td><td>{fmt(now_s)}</td><td>{fmt(peak_s)}</td>"
            f"<td>{fmt(now_w)}</td><td>{fmt(peak_w)}</td></tr>"
        )
        cards.append(
            f'<section class="card"><h2><span style="color:{color}">●</span> {name}</h2>'
            f'<p class="sub">Session now {fmt(now_s)} · peak 7d {fmt(peak_s)} &nbsp;|&nbsp; '
            f"Weekly now {fmt(now_w)} · peak 7d {fmt(peak_w)}</p>"
            f"{_chart(session, weekly, color, start, now)}</section>"
        )
    generated = datetime.now(UTC).strftime("%d %b %Y %H:%M UTC")
    return f"""<!doctype html><meta charset="utf-8"><title>Quota history</title>
<style>body{{font-family:-apple-system,"Segoe UI",sans-serif;background:#f8f9fa;color:#212529;max-width:920px;margin:24px auto;padding:0 16px}}h1{{font-size:22px}}.card{{background:#fff;border:1px solid #dee2e6;border-radius:12px;padding:16px 18px;margin:14px 0}}h2{{font-size:16px;margin:0 0 2px}}.sub{{color:#868e96;font-size:12.5px;margin:0 0 6px}}table{{border-collapse:collapse;background:#fff;border:1px solid #dee2e6;border-radius:12px;overflow:hidden}}th,td{{padding:8px 14px;text-align:right;font-size:13.5px}}th:first-child,td:first-child{{text-align:left}}th{{background:#f1f3f5}}</style>
<h1>Quota history — session (5h) &amp; weekly % used, last {days} days</h1>
<p class="sub">Generated {generated} · solid: session · dashed: weekly · Source: ~/.usage/quota_history.json</p>
<table><tr><th>Provider</th><th>Session now</th><th>Session peak 7d</th><th>Weekly now</th><th>Weekly peak 7d</th></tr>{''.join(rows)}</table>
{''.join(cards)}"""


def _chrome_binary() -> str | None:
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def save_history_report(fmt: str = "html", out_path: str | None = None) -> str:
    """Write the history export and open it. Returns the display path.

    PDF uses a local Chrome/Chromium headless print; without one it falls
    back to writing (and opening) the HTML file instead.
    """
    from quota import quota_history

    html = generate_history_html(quota_history.payload_series())
    reports_dir = Path.home() / ".usage-reports"
    reports_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    html_path = Path(out_path) if out_path else reports_dir / f"quota-history-{stamp}.html"
    html_path.write_text(html, encoding="utf-8")
    html_path.chmod(0o600)

    target = html_path
    if fmt == "pdf":
        chrome = _chrome_binary()
        if chrome is not None:
            pdf_path = html_path.with_suffix(".pdf")
            result = subprocess.run(
                [chrome, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                 f"--print-to-pdf={pdf_path}", html_path.resolve().as_uri()],
                check=False, capture_output=True, timeout=60,
            )
            if result.returncode == 0 and pdf_path.exists():
                target = pdf_path
    if sys.platform == "darwin":
        subprocess.run(["/usr/bin/open", str(target.resolve())], check=False,
                       stdin=subprocess.DEVNULL)
    return f"~/.usage-reports/{target.name}" if out_path is None else str(target)
