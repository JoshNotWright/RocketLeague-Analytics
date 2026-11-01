"""Score any folder of replays with the fitted models and compare players with the GC+ reference.

``build_reference`` summarises the GC+ training population (players with enough games) into
per-metric quantiles so scoring needs only the small reference JSON and the three boosters.
``score_folder`` parses replays, computes frame features and touch events, applies the models,
derives value added and saves, shrinks per-player means, and writes a player report.

The models are calibrated on GC+ ranked doubles. On other ranks the absolute probabilities
are not calibrated; percentiles against the GC+ reference still say where a player's
volume, positioning and value-added numbers sit relative to the top of the ladder.
"""
from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import polars as pl

from . import value as value_mod
from . import xg as xg_mod
from .ranks import attach_game_rank, band_of, band_range
from .shrink import shrink_table

QUANTILES = [0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95]
# per-game player metrics shown in the report: (name, source table column, higher is better?)
METRICS = [
    ("touches", "touches", True), ("shots", "shots", True), ("xg", "xg", True), ("goals", "goals", True),
    ("goals_above_xg", "goals_above_xg", True), ("mean_threat", "mean_threat", True),
    ("value_added", "value_added", True), ("shots_faced", "shots_faced", None), ("gsax", "gsax", True),
    ("avg_speed", "avg_speed", True), ("supersonic_frac", "supersonic_frac", True), ("avg_boost", "avg_boost", True),
    ("low_boost_frac", "low_boost_frac", False), ("behind_ball_frac", "behind_ball_frac", None),
    ("avg_dist_mate", "avg_dist_mate", None), ("last_man_low_boost_frac", "last_man_low_boost_frac", False),
    ("challenge_win_rate", "challenge_win_rate", True), ("kickoff_time_to_ball", "kickoff_time_to_ball", False),
    ("demos_inflicted", "demos_inflicted", True), ("demos_taken", "demos_taken", False),
    ("big_pads", "big_pads", None), ("small_pads", "small_pads", None),
]


def _parse_one(path: str):
    from .events import extract_touches
    from .frames import extract
    from .header import header_summary, parse
    replay_id = Path(path).stem
    try:
        parsed = parse(path, network=True)
        head = header_summary(parsed)
        return replay_id, head, extract(parsed, replay_id=replay_id), extract_touches(parsed, replay_id=replay_id), None
    except Exception as exc:  # noqa: BLE001
        return replay_id, None, [], [], f"{type(exc).__name__}: {str(exc)[:200]}"


def parse_folder(raw: Path, workers=None, limit: int = 0):
    paths = sorted(str(p) for p in Path(raw).glob("*.replay"))
    if limit:
        paths = paths[:limit]
    heads, frame_rows, touch_rows, failures = {}, [], [], {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(_parse_one, p) for p in paths]):
            replay_id, head, fr, tr, err = future.result()
            if err:
                failures[replay_id] = err
                continue
            heads[replay_id] = head
            frame_rows.extend(fr)
            touch_rows.extend(tr)
    return heads, pl.DataFrame(frame_rows, infer_schema_length=None), pl.DataFrame(touch_rows, infer_schema_length=None), failures


def _player_id_column(frames: pl.DataFrame) -> pl.DataFrame:
    from .events import PLATFORMS
    return frames.with_columns(
        (pl.col("frame_platform").replace_strict(PLATFORMS, default=pl.col("frame_platform").str.to_lowercase())
         + ":" + pl.col("frame_uid")).alias("player_id"))


def apply_models(touches: pl.DataFrame, models_dir: Path) -> pl.DataFrame:
    import lightgbm as lgb
    scored = touches
    for name, spec in xg_mod.MODELS.items():
        booster = lgb.Booster(model_file=str(models_dir / f"{name}.lgb.txt"))
        features = booster.feature_name()
        scored = scored.with_columns(pl.Series(name, booster.predict(xg_mod._matrix(scored, features))))
    return scored


def per_game_table(frames: pl.DataFrame, touches_scored: pl.DataFrame, heads: dict) -> pl.DataFrame:
    """One row per player-game with frame features, xG/threat/value and save numbers."""
    valued = value_mod.add_value(touches_scored)
    xg_game = (valued.group_by("replay_id", "player_id")
                     .agg(pl.col("shot").sum().alias("shots"),
                          pl.col("scored").sum().alias("goals"), pl.col("touch_xg").sum().alias("xg"),
                          pl.col("threat").mean().alias("mean_threat"), pl.col("value_added").sum().alias("value_added"))
                     .with_columns((pl.col("goals") - pl.col("xg")).alias("goals_above_xg")))
    faced = value_mod.saves(valued)
    save_game = (faced.group_by("replay_id", pl.col("defender_id").alias("player_id"))
                      .agg(pl.len().alias("shots_faced"), pl.col("gsax").sum()))
    dates = pl.DataFrame({"replay_id": list(heads), "date": [h["date"] for h in heads.values()],
                          "team0_score": [h["team0_score"] for h in heads.values()],
                          "team1_score": [h["team1_score"] for h in heads.values()]})
    table = (_player_id_column(frames).join(xg_game, on=["replay_id", "player_id"], how="left")
                                       .join(save_game, on=["replay_id", "player_id"], how="left")
                                       .join(dates, on="replay_id", how="left"))
    return table.with_columns(pl.col("shots_faced").fill_null(0), pl.col("gsax").fill_null(0.0),
                              (pl.col("team0_score") > pl.col("team1_score")).eq(pl.col("frame_team") == 0).alias("won"))


def build_reference(player_game_xg: pl.DataFrame, player_game_value: pl.DataFrame, player_game_saves: pl.DataFrame,
                    frame_features: pl.DataFrame, *, min_games: int = 30, table: pl.DataFrame | None = None,
                    band: str | None = None) -> dict:
    """Quantiles of per-player means over players with at least ``min_games`` games.

    With ``table`` (player-game rows carrying ``rank_points``) and ``band``, only games whose
    ``game_rank`` falls in that band count, and the reference records the band so ``score``
    can pick it for players who play there.
    """
    frames = _player_id_column(frame_features)
    if band:
        if table is None:
            raise ValueError("a band reference needs the player-game table with rank_points")
        lo, hi = band_range(band)
        frames = attach_game_rank(frames, table).filter(pl.col("game_rank").is_between(lo, hi)).drop("game_rank", "rank_band")
    game = (frames.join(player_game_xg.select("replay_id", "player_id", "touches", "shots", "goals", "xg", "goals_above_xg", "mean_threat"),
                        on=["replay_id", "player_id"], how="left")
                  .join(player_game_value.select("replay_id", "player_id", "value_added"), on=["replay_id", "player_id"], how="left")
                  .join(player_game_saves.select("replay_id", "player_id", "shots_faced", "gsax"), on=["replay_id", "player_id"], how="left")
                  .with_columns(pl.col("shots_faced").fill_null(0), pl.col("gsax").fill_null(0.0)))
    means = game.group_by("player_id").agg(pl.len().alias("games"), *[pl.col(c).mean().alias(c) for _, c, _ in METRICS if c in game.columns])
    means = means.filter(pl.col("games") >= min_games)
    # Shrinkage priors (population mean, between- and within-player variance) from the same players.
    priors = shrink_table(game.filter(pl.col("player_id").is_in(means["player_id"].implode())),
                          [c for _, c, _ in METRICS], min_games=min_games)
    prior_by_col = {row["statistic"]: row for row in
                    priors.unique(subset=["statistic"]).select("statistic", "population_mean", "between_var", "within_var_pooled").iter_rows(named=True)}
    reference = {"min_games": min_games, "players": means.height, "games": game.height, "metrics": {},
                 "band": band, "rank_points": list(band_range(band)) if band else None}
    # Split-half reliability per metric over the same players: how much of a per-player mean is
    # a trait rather than noise. Cards fade the bars of metrics that do not persist.
    eligible = game.filter(pl.col("player_id").is_in(means["player_id"].implode()))
    if "played_at" not in eligible.columns:
        eligible = eligible.with_columns(pl.lit(0).alias("played_at"))
    for name, col, higher in METRICS:
        if col not in means.columns:
            continue
        values = means[col].drop_nulls().to_numpy()
        if len(values) == 0:
            continue
        prior = prior_by_col.get(col, {})
        try:
            reliability = xg_mod.split_half(eligible.filter(pl.col(col).is_not_null()), col, min_games=min_games)["split_half_corr"]
        except Exception:  # noqa: BLE001 - a metric with too few rows simply has no reliability figure
            reliability = None
        reference["metrics"][name] = {"higher_is_better": higher, "mean": float(values.mean()),
                                      "reliability": None if reliability is None or reliability != reliability else float(reliability),
                                      "quantiles": {str(q): float(np.quantile(values, q)) for q in QUANTILES},
                                      "sorted": [float(v) for v in np.sort(values)],
                                      "prior": {"mean": prior.get("population_mean"), "between_var": prior.get("between_var"),
                                                "within_var": prior.get("within_var_pooled")}}
    return reference


def shrink_with_prior(reference: dict, name: str, raw_mean: float, games: int) -> tuple[float | None, float | None]:
    """Shrink a player's raw mean toward the GC+ population using the reference priors."""
    prior = (reference["metrics"].get(name) or {}).get("prior") or {}
    mu, tau2, sigma2 = prior.get("mean"), prior.get("between_var"), prior.get("within_var")
    if raw_mean is None or mu is None or tau2 is None or sigma2 is None or games <= 0:
        return raw_mean, None
    factor = tau2 / (tau2 + sigma2 / games) if (tau2 + sigma2 / games) > 0 else 0.0
    return mu + factor * (raw_mean - mu), factor


def load_references(path: Path) -> list[dict]:
    """One reference JSON, or every ``*.json`` in a directory (band references plus a fallback)."""
    path = Path(path)
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    refs = []
    for file in files:
        ref = json.loads(file.read_text())
        ref["source"] = file.stem
        refs.append(ref)
    if not refs:
        raise SystemExit(f"no reference JSON found at {path}")
    return refs


def pick_reference(references: list[dict], game_rank: float | None) -> dict:
    """The band reference containing ``game_rank``; otherwise the first reference without a band."""
    band = band_of(game_rank)
    for ref in references:
        if band and ref.get("band") == band:
            return ref
    for ref in references:
        if not ref.get("band"):
            return ref
    return references[0]


def percentile(reference: dict, name: str, value: float) -> float | None:
    entry = reference["metrics"].get(name)
    if entry is None or value is None:
        return None
    arr = np.asarray(entry["sorted"])
    return float(np.searchsorted(arr, value, side="right") / len(arr) * 100)


def load_game_ranks(meta_path: Path | None, raw: Path) -> pl.DataFrame | None:
    """Player-game rows (with ``rank_points``) from a ballchasing metadata file next to the replays."""
    from .meta import load_player_games
    path = Path(meta_path) if meta_path else Path(raw) / "metadata.json"
    if not path.exists():
        return None
    return load_player_games(path)


def score_folder(raw: Path, out: Path, *, models_dir: Path, reference_path: Path, workers=None, limit: int = 0,
                 focus: str | None = None, meta_path: Path | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    heads, frames, touches, failures = parse_folder(raw, workers=workers, limit=limit)
    if frames.height == 0:
        raise SystemExit(f"no replays parsed from {raw} ({len(failures)} failures)")
    meta_table = load_game_ranks(meta_path, raw)
    if meta_table is not None:
        touches = attach_game_rank(touches, meta_table)
    scored = apply_models(touches, models_dir)
    table = per_game_table(frames, scored, heads)
    if meta_table is not None:
        table = attach_game_rank(table, meta_table)
    table.write_parquet(out / "player_game.parquet")
    scored.write_parquet(out / "touches_scored.parquet")
    references = load_references(reference_path)
    columns = [c for _, c, _ in METRICS if c in table.columns]
    rank_aggs = [pl.col("game_rank").median().alias("median_game_rank")] if "game_rank" in table.columns else []
    players = (table.group_by("player_id").agg(pl.len().alias("games"), pl.col("frame_player_name").last().alias("name"),
                                              pl.col("won").mean().alias("win_rate"), *rank_aggs,
                                              *[pl.col(c).mean().alias(c) for c in columns])
                    .sort("games", descending=True))
    report = {"replays": len(heads), "failures": failures, "ranks_known": meta_table is not None,
              "references": [{"source": r["source"], "band": r.get("band"), "players": r.get("players")} for r in references],
              "players": []}
    for row in players.iter_rows(named=True):
        game_rank = row.get("median_game_rank")
        reference = pick_reference(references, game_rank)
        entry = {"player_id": row["player_id"], "name": row["name"], "games": row["games"], "win_rate": row["win_rate"],
                 "median_game_rank": game_rank, "band": band_of(game_rank), "reference": reference["source"],
                 "reference_band": reference.get("band"), "metrics": {}}
        for name, col, _ in METRICS:
            if col not in columns:
                continue
            # Shrink toward the reference population with its variances, so a handful of games cannot
            # produce an extreme number; percentiles are taken on the shrunk mean.
            shrunk_mean, factor = shrink_with_prior(reference, name, row[col], row["games"])
            entry["metrics"][name] = {"raw": row[col], "shrunk": shrunk_mean, "shrink_factor": factor,
                                      "ref_percentile": percentile(reference, name, shrunk_mean),
                                      "ref_median": reference["metrics"].get(name, {}).get("quantiles", {}).get("0.5")}
        report["players"].append(entry)
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    text = to_markdown(report, focus=focus)
    (out / "report.md").write_text(text, encoding="utf-8")
    report["markdown"] = text
    return report


def to_markdown(report: dict, *, focus: str | None = None, max_players: int = 6) -> str:
    refs = ", ".join(f"{r['source']} ({r['band'] or 'all ranks'}, {r['players']} players)" for r in report.get("references", []))
    lines = [f"Scored {report['replays']} replays ({len(report['failures'])} failed to parse). "
             "Each player is compared with the reference population for the band they played in "
             f"(median match rank); references: {refs}. Shrunk values pull each player's mean toward that "
             "population in proportion to how noisy the statistic is and how few games were seen; percentiles are "
             "of the shrunk value. "
             + ("Match ranks came from the metadata next to the replays, so xG and threat are conditioned on the level "
                "of play." if report.get("ranks_known") else
                "No metadata.json was found next to the replays, so match ranks are unknown: the models fall back to "
                "their default rank path and the all-ranks reference is used."), ""]
    players = report["players"]
    if focus:
        players = [p for p in players if focus.lower() in (p["name"] or "").lower() or focus in p["player_id"]] + \
                  [p for p in players if not (focus.lower() in (p["name"] or "").lower() or focus in p["player_id"])]
    for p in players[:max_players]:
        rank = f", median match rank {p['median_game_rank']:.1f} ({p['band']})" if p.get("median_game_rank") is not None else ""
        ref = p.get("reference_band") or "all ranks"
        lines += [f"## {p['name']} ({p['player_id']}), {p['games']} games, win rate {p['win_rate']:.2f}{rank}; "
                  f"reference: {p.get('reference')} ({ref})", "",
                  f"| metric (per game) | raw | shrunk | {ref} median | {ref} percentile |", "|---|---|---|---|---|"]
        for name, m in p["metrics"].items():
            if m["raw"] is None:
                continue
            pct = f"{m['ref_percentile']:.0f}" if m["ref_percentile"] is not None else "-"
            med = f"{m['ref_median']:.3f}" if m["ref_median"] is not None else "-"
            lines.append(f"| {name} | {m['raw']:.3f} | {m['shrunk']:.3f} | {med} | {pct} |")
        lines.append("")
    if len(players) > max_players:
        lines.append(f"... {len(players) - max_players} more players in report.json")
    return "\n".join(lines)
