"""Build the modelling table: game context, strictly-past rolling features, future targets.

All rolling features for a player-game use only that player's *earlier* games
(``shift(1)`` before the rolling mean), so nothing from the game being predicted
or from the future leaks into its features.
"""
from __future__ import annotations

from datetime import timedelta

import polars as pl

from .meta import SUMMARY_COLUMNS

FRAME_COLUMNS = [
    "avg_speed", "supersonic_frac", "ground_frac", "avg_boost", "low_boost_frac", "zero_boost_frac",
    "behind_ball_frac", "avg_dist_ball", "avg_dist_mate", "last_man_frac", "last_man_low_boost_frac",
    "commit_dist_mate", "touch_rate", "challenge_rate", "challenge_win_rate", "kickoffs",
    "kickoff_time_to_ball", "kickoff_win_rate", "demos_inflicted", "demos_taken", "big_pads", "small_pads",
]
CONTEXT_COLUMNS = ["rank_points", "team_rank_mean", "opp_rank_mean", "rank_diff", "n_prev_games",
                   "days_since_prev"]


def add_game_context(df: pl.DataFrame) -> pl.DataFrame:
    """Team/opponent mean rank for every player-game (rank unknown stays null)."""
    team = (df.group_by("replay_id", "team")
              .agg(pl.col("rank_points").mean().alias("team_rank_mean")))
    opp = team.rename({"team": "opp_team", "team_rank_mean": "opp_rank_mean"}).with_columns(
        pl.when(pl.col("opp_team") == "blue").then(pl.lit("orange")).otherwise(pl.lit("blue")).alias("team")
    ).drop("opp_team")
    out = df.join(team, on=["replay_id", "team"], how="left").join(opp, on=["replay_id", "team"], how="left")
    return out.with_columns((pl.col("team_rank_mean") - pl.col("opp_rank_mean")).alias("rank_diff"))


def add_rolling(df: pl.DataFrame, columns, *, window: int = 10, prefix: str | None = None) -> pl.DataFrame:
    """Per player, mean of each column over the previous ``window`` games (current game excluded)."""
    prefix = prefix if prefix is not None else f"r{window}_"
    df = df.sort("player_id", "played_at", "replay_id")
    exprs = [
        pl.col(c).shift(1).rolling_mean(window_size=window, min_samples=1).over("player_id").alias(prefix + c)
        for c in columns if c in df.columns
    ]
    return df.with_columns(exprs)


def add_history_counts(df: pl.DataFrame) -> pl.DataFrame:
    df = df.sort("player_id", "played_at", "replay_id")
    return df.with_columns(
        pl.int_range(pl.len()).over("player_id").alias("n_prev_games"),
        ((pl.col("played_at") - pl.col("played_at").shift(1).over("player_id"))
         .dt.total_seconds() / 86400.0).alias("days_since_prev"),
        pl.col("won").cast(pl.Float64).shift(1).cum_sum().over("player_id").alias("_wins_prev"),
    ).with_columns(
        (pl.col("_wins_prev") / pl.col("n_prev_games")).alias("prev_win_rate")
    ).drop("_wins_prev")


def add_future_rank(df: pl.DataFrame, *, days: int = 30) -> pl.DataFrame:
    """Rank at the player's first game at least ``days`` later; ``rank_change`` = future - now."""
    future = (df.filter(pl.col("rank_points").is_not_null())
                .select("player_id", pl.col("played_at").alias("future_at"),
                        pl.col("rank_points").alias("future_rank_points"))
                .sort("future_at"))
    probe = df.with_columns((pl.col("played_at") + timedelta(days=days)).alias("_horizon")).sort("_horizon")
    joined = probe.join_asof(future, left_on="_horizon", right_on="future_at", by="player_id",
                             strategy="forward")
    return joined.with_columns(
        (pl.col("future_rank_points") - pl.col("rank_points")).alias("rank_change")
    ).drop("_horizon")


def join_frames(df: pl.DataFrame, frame_features: pl.DataFrame) -> pl.DataFrame:
    """Attach frame rows by (replay_id, player_id); fall back to (replay_id, name) for the rest.

    Console players (PlayStation, Xbox) carry a numeric id in the replay but a display name
    in ballchasing's metadata, so the id join misses them and the name join catches them.
    """
    value_cols = [c for c in frame_features.columns if c not in ("replay_id", "player_id", "frame_player_name")]
    by_id = frame_features.select("replay_id", "player_id", *value_cols)
    first = df.join(by_id, on=["replay_id", "player_id"], how="left")
    missing = first.filter(pl.col("active_time").is_null()).select(df.columns)
    hit = first.filter(pl.col("active_time").is_not_null())
    matched_keys = hit.select("replay_id", "player_id")
    by_name = (frame_features.join(matched_keys, on=["replay_id", "player_id"], how="anti")
                             .select("replay_id", pl.col("frame_player_name").alias("player_name"), *value_cols)
                             .unique(subset=["replay_id", "player_name"], keep="none"))
    second = missing.join(by_name, on=["replay_id", "player_name"], how="left")
    return (pl.concat([hit, second.select(hit.columns)], how="vertical")
              .unique(subset=["replay_id", "player_id"], keep="first", maintain_order=True))


def build_table(player_games: pl.DataFrame, frame_features: pl.DataFrame | None = None, *,
                window: int = 10, playlist: str | None = "ranked-doubles") -> pl.DataFrame:
    df = player_games
    if playlist:
        df = df.filter(pl.col("playlist") == playlist)
    if frame_features is not None:
        df = join_frames(df, frame_features)
    df = add_game_context(df)
    df = add_history_counts(df)
    df = add_rolling(df, SUMMARY_COLUMNS + ["won", "goal_diff"], window=window)
    if frame_features is not None:
        df = add_rolling(df, FRAME_COLUMNS, window=window)
    df = add_future_rank(df)
    return df.sort("played_at", "replay_id", "team", "slot")


def rolling_frame_coverage(df: pl.DataFrame, *, window: int = 10) -> pl.DataFrame:
    """How many of the previous ``window`` games had frame features (for subset-aware filtering)."""
    df = df.sort("player_id", "played_at", "replay_id")
    return df.with_columns(
        pl.col("active_time").is_not_null().cast(pl.Int32).shift(1)
          .rolling_sum(window_size=window, min_samples=1).over("player_id").alias("n_prev_frame_games")
    )
