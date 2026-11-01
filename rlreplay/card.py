"""Player cards: one SVG template rendered as a shareable image and as an interactive HTML page.

Inspired by percentile cards from hockey analytics. Every bar is a percentile against the
reference population for the band the player plays in; bars fade with the metric's split-half
reliability (how much of a per-player mean is a trait rather than noise), so finishing luck is
visibly quieter than movement, which persists.

    python -m rlreplay card --report reports/pastorkurt-ranked --player PastorKurt --out reports/pastorkurt-ranked

Writes ``card_<slug>.svg`` (standalone, themed by prefers-color-scheme) and ``card_<slug>.html``
(the same SVG with hover detail per bar). Only json, polars and the standard library are used.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

import polars as pl

from .ranks import band_of

# Sections and the metrics in each, in display order: (metric name, label, format).
SECTIONS = [
    ("Chance creation", [("xg", "Expected goals", "{:.2f}"), ("shots", "Shots", "{:.1f}"),
                         ("mean_threat", "Situation quality", "{:.3f}"), ("touches", "Touches", "{:.0f}")]),
    ("Finishing", [("goals", "Goals", "{:.2f}"), ("goals_above_xg", "Goals above xG", "{:+.2f}")]),
    ("Impact", [("value_added", "Value added", "{:+.2f}"), ("challenge_win_rate", "Challenge win rate", "{:.0%}")]),
    ("Defence", [("gsax", "Save quality (GSAx)", "{:+.2f}"), ("shots_faced", "Shots faced", "{:.1f}"),
                 ("demos_taken", "Demos taken", "{:.2f}"), ("demos_inflicted", "Demos inflicted", "{:.2f}")]),
    ("Pace and boost", [("supersonic_frac", "Supersonic time", "{:.0%}"), ("avg_speed", "Average speed", "{:.0f}"),
                        ("big_pads", "Big pads", "{:.1f}"), ("small_pads", "Small pads", "{:.1f}"),
                        ("avg_boost", "Average boost", "{:.0f}"), ("low_boost_frac", "Time under 20 boost", "{:.0%}")]),
    ("Positioning", [("behind_ball_frac", "Behind the ball", "{:.0%}"), ("last_man_low_boost_frac", "Last man, low boost", "{:.0%}"),
                     ("avg_dist_mate", "Distance to teammate", "{:.0f}"), ("kickoff_time_to_ball", "Kickoff time to ball", "{:.2f}s")]),
]
TREND_METRICS = [("xg", "xG / game"), ("value_added", "Value added"), ("goals_above_xg", "Goals above xG")]
TIERS = ["Unranked", "Bronze", "Bronze", "Bronze", "Silver", "Silver", "Silver", "Gold", "Gold", "Gold",
         "Platinum", "Platinum", "Platinum", "Diamond", "Diamond", "Diamond", "Champion", "Champion", "Champion",
         "Grand Champion", "Grand Champion", "Grand Champion", "Supersonic Legend"]
ROMAN = {1: "I", 2: "II", 3: "III"}

W, LEFT, RIGHT = 760, 24, 24
BAR_X, BAR_W = 250, 300  # bar track position and width
ROW_H, SECTION_GAP, HEADER_H, TREND_H, FOOTER_H = 26, 22, 118, 170, 52


def rank_name(points: float | None) -> str:
    if points is None:
        return "rank unknown"
    tier = int(points) // 4
    tier_name = TIERS[min(max(tier, 0), len(TIERS) - 1)]
    if tier_name in ("Unranked", "Supersonic Legend"):
        return tier_name
    within = ((tier - 1) % 3) + 1
    division = int(points) % 4 + 1
    return f"{tier_name} {ROMAN[within]} div {division}"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "player"


def directional_percentile(metric: dict, higher: bool | None) -> float | None:
    """Percentile in the good direction: inverted for lower-is-better metrics, as is for neutral ones."""
    p = metric.get("ref_percentile")
    if p is None:
        return None
    return 100 - p if higher is False else p


def bar_color(pct: float | None, higher: bool | None) -> str:
    """Diverging blue (good) to red (poor) around a neutral middle; neutral metrics take one hue."""
    if pct is None:
        return "var(--neutral)"
    if higher is None:
        return "var(--neutral-bar)"
    if pct >= 80:
        return "var(--good-2)"
    if pct >= 60:
        return "var(--good-1)"
    if pct > 40:
        return "var(--mid)"
    if pct > 20:
        return "var(--poor-1)"
    return "var(--poor-2)"


def load_card_data(report_dir: Path, player: str, reference_dir: Path | None = None) -> dict:
    report = json.loads((Path(report_dir) / "report.json").read_text(encoding="utf-8"))
    matches = [p for p in report["players"] if player.lower() in (p["name"] or "").lower() or player == p["player_id"]]
    if not matches:
        raise SystemExit(f"no player matching {player!r} in {report_dir}")
    entry = max(matches, key=lambda p: p["games"])
    ref_meta = {}
    reference_dir = Path(reference_dir) if reference_dir else Path(__file__).resolve().parent / "reference"
    ref_file = reference_dir / f"{entry.get('reference')}.json"
    if ref_file.exists():
        ref_meta = json.loads(ref_file.read_text(encoding="utf-8"))
    history = []
    games_path = Path(report_dir) / "player_game.parquet"
    if games_path.exists():
        games = pl.read_parquet(games_path).filter(pl.col("player_id") == entry["player_id"])
        if "date" in games.columns and games.height:
            year = pl.col("date").cast(pl.Utf8).str.slice(0, 4).alias("year")
            cols = [m for m, _ in TREND_METRICS if m in games.columns]
            by_year = (games.with_columns(year).group_by("year")
                            .agg(pl.len().alias("games"), *[pl.col(c).mean() for c in cols]).sort("year"))
            history = by_year.to_dicts()
    return {"player": entry, "reference": ref_meta, "history": history, "replays": report["replays"]}


def _esc(text) -> str:
    return html.escape(str(text), quote=True)


def render_svg(data: dict) -> str:
    p = data["player"]
    ref = data["reference"]
    metrics = p["metrics"]
    rows = sum(len(items) for _, items in SECTIONS)
    body_h = rows * ROW_H + len(SECTIONS) * SECTION_GAP
    height = HEADER_H + body_h + TREND_H + FOOTER_H
    band = p.get("band") or band_of(p.get("median_game_rank"))
    ref_label = (ref.get("band") or "all ranks")
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height}" width="{W}" height="{height}" '
           f'font-family="Inter, Segoe UI, Helvetica, Arial, sans-serif" class="rl-card" role="img" '
           f'aria-label="Player card for {_esc(p["name"])}">',
           "<style>",
           ".rl-card{--bg:#f7f6f2;--panel:#ffffff;--ink:#17191c;--ink-2:#5a5e66;--ink-3:#8a8f99;--rule:#e2e0d8;"
           "--track:#ebe9e2;--good-2:#1c5cab;--good-1:#5598e7;--mid:#b9bdc6;--poor-1:#ec835a;--poor-2:#c83a3a;"
           "--neutral-bar:#7f8894;--neutral:#c9ccd2;--accent:#1c5cab}",
           "@media (prefers-color-scheme: dark){.rl-card:not(.light){--bg:#15171a;--panel:#1d2024;--ink:#f0efec;--ink-2:#b8bcc4;"
           "--ink-3:#8a8f99;--rule:#2d3137;--track:#2a2e34;--good-2:#6da7ec;--good-1:#3987e5;--mid:#5a606b;--poor-1:#ec835a;"
           "--poor-2:#e66767;--neutral-bar:#9aa3af;--neutral:#454b55;--accent:#6da7ec}}",
           ".rl-card.dark{--bg:#15171a;--panel:#1d2024;--ink:#f0efec;--ink-2:#b8bcc4;--ink-3:#8a8f99;--rule:#2d3137;"
           "--track:#2a2e34;--good-2:#6da7ec;--good-1:#3987e5;--mid:#5a606b;--poor-1:#ec835a;--poor-2:#e66767;"
           "--neutral-bar:#9aa3af;--neutral:#454b55;--accent:#6da7ec}",
           ".t{fill:var(--ink)}.t2{fill:var(--ink-2)}.t3{fill:var(--ink-3)}.num{font-variant-numeric:tabular-nums}",
           ".bar-row:hover .track{stroke:var(--accent);stroke-width:1}",
           "</style>",
           f'<rect width="{W}" height="{height}" fill="var(--bg)"/>']
    # Header
    name = _esc(p["name"])
    rank = rank_name(p.get("median_game_rank"))
    games = p["games"]
    out += [f'<text x="{LEFT}" y="44" class="t" font-size="30" font-weight="700">{name}</text>',
            f'<text x="{LEFT}" y="68" class="t2" font-size="13">Ranked doubles · median match rank {_esc(rank)}'
            f'{" · " + _esc(band) + " band" if band else ""}</text>',
            f'<text x="{LEFT}" y="88" class="t2 num" font-size="13">{games} games · win rate {p["win_rate"]:.0%}'
            f' · compared with {ref.get("players", "?")} {_esc(ref_label)} players with {ref.get("min_games", 30)}+ games</text>',
            f'<text x="{W - RIGHT}" y="44" class="t3" font-size="11" text-anchor="end" letter-spacing="1.5">RLREPLAY PLAYER CARD</text>',
            f'<text x="{W - RIGHT}" y="60" class="t3" font-size="11" text-anchor="end">percentile vs {_esc(ref_label)} · bars fade with reliability</text>',
            f'<line x1="{LEFT}" y1="{HEADER_H - 14}" x2="{W - RIGHT}" y2="{HEADER_H - 14}" stroke="var(--rule)"/>',
            f'<text x="{BAR_X}" y="{HEADER_H - 2}" class="t3" font-size="10">0</text>',
            f'<text x="{BAR_X + BAR_W / 2}" y="{HEADER_H - 2}" class="t3" font-size="10" text-anchor="middle">50</text>',
            f'<text x="{BAR_X + BAR_W}" y="{HEADER_H - 2}" class="t3" font-size="10" text-anchor="end">100</text>']
    y = HEADER_H + 10
    for title, items in SECTIONS:
        out.append(f'<text x="{LEFT}" y="{y + 10}" class="t" font-size="12" font-weight="700" letter-spacing="1">{_esc(title.upper())}</text>')
        y += SECTION_GAP
        for metric, label, fmt in items:
            m = metrics.get(metric)
            if not m or m.get("raw") is None:
                continue
            higher = (ref.get("metrics", {}).get(metric) or {}).get("higher_is_better")
            reliability = (ref.get("metrics", {}).get(metric) or {}).get("reliability")
            pct = directional_percentile(m, higher)
            opacity = 1.0 if reliability is None else max(0.3, min(1.0, 0.3 + 0.9 * reliability))
            color = bar_color(pct, higher)
            width = 0 if pct is None else BAR_W * pct / 100
            raw_text = fmt.format(m["raw"]) if m["raw"] is not None else "-"
            pct_text = f"{pct:.0f}" if pct is not None else "-"
            noisy = reliability is not None and reliability < 0.2
            cy = y + ROW_H / 2
            out.append(f'<g class="bar-row" data-metric="{_esc(metric)}" data-label="{_esc(label)}" data-raw="{_esc(raw_text)}" '
                       f'data-shrunk="{m.get("shrunk") if m.get("shrunk") is not None else ""}" '
                       f'data-median="{m.get("ref_median") if m.get("ref_median") is not None else ""}" '
                       f'data-pct="{pct_text}" data-reliability="{reliability if reliability is not None else ""}" '
                       f'data-direction="{"higher" if higher else "lower" if higher is False else "neutral"}">')
            out.append(f'<text x="{LEFT}" y="{cy + 4}" class="t" font-size="12">{_esc(label)}</text>')
            out.append(f'<text x="{BAR_X - 10}" y="{cy + 4}" class="t2 num" font-size="11" text-anchor="end">{_esc(raw_text)}</text>')
            out.append(f'<rect class="track" x="{BAR_X}" y="{cy - 7}" width="{BAR_W}" height="14" rx="3" fill="var(--track)"/>')
            out.append(f'<line x1="{BAR_X + BAR_W / 2}" y1="{cy - 9}" x2="{BAR_X + BAR_W / 2}" y2="{cy + 9}" stroke="var(--rule)"/>')
            if width > 0:
                out.append(f'<rect x="{BAR_X}" y="{cy - 7}" width="{width:.1f}" height="14" rx="3" fill="{color}" opacity="{opacity:.2f}"/>')
            out.append(f'<text x="{BAR_X + BAR_W + 10}" y="{cy + 4}" class="t num" font-size="12" font-weight="700">{pct_text}</text>')
            if noisy:
                out.append(f'<text x="{BAR_X + BAR_W + 40}" y="{cy + 4}" class="t3" font-size="10">noise</text>')
            elif higher is None:
                out.append(f'<text x="{BAR_X + BAR_W + 40}" y="{cy + 4}" class="t3" font-size="10">style</text>')
            out.append("</g>")
            y += ROW_H
    # Trend strip: one small line per metric across years
    y += 6
    out.append(f'<line x1="{LEFT}" y1="{y}" x2="{W - RIGHT}" y2="{y}" stroke="var(--rule)"/>')
    out.append(f'<text x="{LEFT}" y="{y + 22}" class="t" font-size="12" font-weight="700" letter-spacing="1">BY YEAR</text>')
    history = [h for h in data["history"] if h.get("games", 0) >= 10]
    if history:
        panel_w = (W - LEFT - RIGHT - 2 * 16) / 3
        for i, (metric, label) in enumerate(TREND_METRICS):
            px = LEFT + i * (panel_w + 16)
            py = y + 56
            ph = TREND_H - 90
            values = [h.get(metric) for h in history]
            if any(v is None for v in values):
                continue
            lo, hi = min(values), max(values)
            span = (hi - lo) or 1.0
            n = len(values)
            pts = []
            for k, v in enumerate(values):
                x = px + (k / max(n - 1, 1)) * (panel_w - 30)
                yy = py + ph - (v - lo) / span * ph
                pts.append((x, yy))
            path = " ".join(f"{'M' if k == 0 else 'L'}{x:.1f},{yy:.1f}" for k, (x, yy) in enumerate(pts))
            out.append(f'<text x="{px}" y="{py - 8}" class="t2" font-size="11">{_esc(label)}</text>')
            out.append(f'<path d="{path}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round"/>')
            for (x, yy), h, v in zip(pts, history, values):
                out.append(f'<circle cx="{x:.1f}" cy="{yy:.1f}" r="3" fill="var(--accent)" stroke="var(--bg)" stroke-width="2"/>')
                out.append(f'<text x="{x:.1f}" y="{py + ph + 14}" class="t3 num" font-size="10" text-anchor="middle">{_esc(h["year"])}</text>')
            last_x, last_y = pts[-1]
            out.append(f'<text x="{last_x + 6:.1f}" y="{last_y + 4:.1f}" class="t num" font-size="11" font-weight="700">{values[-1]:+.2f}</text>'
                       if metric != "xg" else
                       f'<text x="{last_x + 6:.1f}" y="{last_y + 4:.1f}" class="t num" font-size="11" font-weight="700">{values[-1]:.2f}</text>')
    else:
        out.append(f'<text x="{LEFT}" y="{y + 50}" class="t3" font-size="11">Fewer than 10 games in any year; no trend shown.</text>')
    foot_y = height - 26
    out.append(f'<text x="{LEFT}" y="{foot_y}" class="t3" font-size="10">Percentiles use shrunk means (empirical Bayes); '
               f'lower-is-better metrics are shown in the good direction. xG, threat and value added are conditioned on match rank.</text>')
    out.append(f'<text x="{LEFT}" y="{foot_y + 14}" class="t3" font-size="10">Faded bars persist least from game to game for anyone '
               f'(split-half reliability); "noise" marks metrics under 0.2, "style" metrics with no good direction.</text>')
    out.append("</svg>")
    return "\n".join(out)


def render_html(data: dict, svg: str) -> str:
    p = data["player"]
    name = _esc(p["name"])
    return f"""<title>{name} player card</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap">
<style>
/* Layout: one card centred on a quiet ground, the SVG scales to width; hover detail in a fixed panel below. */
:root {{ --bg:#efede6; --fg:#17191c; --fg-2:#5a5e66; --panel:#ffffff; --rule:#e2e0d8; --accent:#1c5cab; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#101214; --fg:#f0efec; --fg-2:#b8bcc4; --panel:#1d2024; --rule:#2d3137; --accent:#6da7ec; color-scheme: dark }} }}
:root[data-theme="dark"] {{ --bg:#101214; --fg:#f0efec; --fg-2:#b8bcc4; --panel:#1d2024; --rule:#2d3137; --accent:#6da7ec; color-scheme: dark }}
body {{ background: var(--bg); color: var(--fg); font-family: Inter, "Segoe UI", Helvetica, Arial, sans-serif;
       padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); padding-block: 24px; }}
.wrap {{ max-width: 800px; margin: 0 auto; display: grid; gap: 16px; }}
.card {{ background: var(--panel); border: 1px solid var(--rule); border-radius: 8px; overflow: hidden; }}
.card svg {{ display: block; width: 100%; height: auto; }}
.detail {{ font-size: 14px; color: var(--fg-2); min-height: 3.2em; line-height: 1.5; }}
.detail b {{ color: var(--fg); }}
.bar-row {{ cursor: default; }}
.note {{ font-size: 13px; color: var(--fg-2); max-width: 65ch; line-height: 1.5; }}
</style>
<div class="wrap">
  <div class="card">{svg}</div>
  <div class="detail" id="detail" aria-live="polite">Hover or tap a bar for the raw value, the shrunk value, the reference median and the metric's reliability.</div>
  <p class="note">Each bar is a percentile against players in the same rank band with 30+ games. Lower-is-better metrics are shown in the good direction. Faded bars are metrics that barely persist from game to game for anyone, so a high or low number there is mostly luck. "style" marks metrics with no good direction.</p>
</div>
<script>
(function () {{
  var detail = document.getElementById('detail');
  var rows = document.querySelectorAll('.bar-row');
  function show(row) {{
    var d = row.dataset;
    var rel = d.reliability ? Number(d.reliability).toFixed(2) : 'unknown';
    var med = d.median ? Number(d.median).toFixed(3) : '-';
    var shr = d.shrunk ? Number(d.shrunk).toFixed(3) : '-';
    var dir = d.direction === 'higher' ? 'higher is better' : d.direction === 'lower' ? 'lower is better (percentile shown in the good direction)' : 'no good direction';
    detail.innerHTML = '<b>' + d.label + '</b>: raw ' + d.raw + ' per game, shrunk ' + shr + ', reference median ' + med +
      '. Percentile <b>' + d.pct + '</b> (' + dir + '). Split-half reliability ' + rel + '.';
  }}
  rows.forEach(function (row) {{
    row.addEventListener('mouseenter', function () {{ show(row); }});
    row.addEventListener('click', function () {{ show(row); }});
  }});
}})();
</script>
"""


def write_card(report_dir: Path, player: str, out_dir: Path, *, reference_dir: Path | None = None) -> dict:
    data = load_card_data(report_dir, player, reference_dir)
    svg = render_svg(data)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"card_{slug(data['player']['name'] or data['player']['player_id'])}"
    (out_dir / f"{stem}.svg").write_text(svg, encoding="utf-8")
    (out_dir / f"{stem}.html").write_text(render_html(data, svg), encoding="utf-8")
    return {"svg": str(out_dir / f"{stem}.svg"), "html": str(out_dir / f"{stem}.html"), "player": data["player"]["name"]}
