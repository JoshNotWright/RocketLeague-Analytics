"""Compare frame-derived features with ballchasing's own stats for the same player-games.

This is the extractor's external check: where ballchasing publishes a comparable number,
the frame feature should agree with it closely. Touch, challenge and kickoff features have
no counterpart and are not covered here.
"""
from __future__ import annotations

import polars as pl

from .features import join_frames

PAIRS = [
    # (frame feature, ballchasing column, scale applied to the ballchasing value)
    ("avg_speed", "movement_avg_speed", 1.0),
    ("supersonic_frac", "movement_percent_supersonic_speed", 0.01),
    ("ground_frac", "movement_percent_ground", 0.01),
    ("avg_boost", "boost_avg_amount", 1.0),
    ("zero_boost_frac", "boost_percent_zero_boost", 0.01),
    ("behind_ball_frac", "positioning_percent_behind_ball", 0.01),
    ("avg_dist_ball", "positioning_avg_distance_to_ball", 1.0),
    ("avg_dist_mate", "positioning_avg_distance_to_mates", 1.0),
    ("big_pads", "boost_count_collected_big", 1.0),
    ("small_pads", "boost_count_collected_small", 1.0),
    ("demos_inflicted", "demo_inflicted", 1.0),
    ("demos_taken", "demo_taken", 1.0),
    ("active_time", "gameplay_duration", 1.0),
]


def compare(player_games: pl.DataFrame, frame_features: pl.DataFrame) -> pl.DataFrame:
    ids = frame_features["replay_id"].unique().implode()
    subset = player_games.filter(pl.col("replay_id").is_in(ids))
    joined = join_frames(subset, frame_features).filter(pl.col("active_time").is_not_null())
    rows = []
    for frame_col, bc_col, scale in PAIRS:
        if frame_col not in joined.columns or bc_col not in joined.columns:
            continue
        pair = joined.select(pl.col(frame_col).cast(pl.Float64).alias("x"),
                             (pl.col(bc_col).cast(pl.Float64) * scale).alias("y")).drop_nulls()
        if pair.height < 3:
            continue
        rows.append({
            "frame_feature": frame_col, "ballchasing_stat": bc_col, "n": pair.height,
            "mean_frame": float(pair["x"].mean()), "mean_ballchasing": float(pair["y"].mean()),
            "corr": float(pair.select(pl.corr("x", "y")).item()),
            "exact_share": float((pair["x"] == pair["y"]).mean()),
        })
    report = pl.DataFrame(rows)
    coverage = {
        "metadata_rows": subset.height, "frame_rows": frame_features.height, "joined_rows": joined.height,
        "team_agreement": float(joined.select(((pl.col("frame_team") == 0) == (pl.col("team") == "blue")).mean()).item())
        if joined.height else None,
    }
    return report, coverage


def to_markdown(report: pl.DataFrame, coverage: dict) -> str:
    lines = [f"Joined {coverage['joined_rows']} of {coverage['metadata_rows']} metadata rows "
             f"({coverage['frame_rows']} frame rows); team agreement {coverage['team_agreement']:.3f}.", "",
             "| frame feature | ballchasing stat | n | mean (frame) | mean (ballchasing) | corr | exact |",
             "|---|---|---|---|---|---|---|"]
    for row in report.iter_rows(named=True):
        lines.append(f"| {row['frame_feature']} | {row['ballchasing_stat']} | {row['n']} | {row['mean_frame']:.3f} | "
                     f"{row['mean_ballchasing']:.3f} | {row['corr']:.3f} | {row['exact_share']:.2f} |")
    return "\n".join(lines)
