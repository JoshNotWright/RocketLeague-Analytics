"""Rank bands on the ``rank_points`` scale from :func:`rlreplay.meta.rank_points`.

A replay's ``game_rank`` is the mean rank points of the players ballchasing tagged with a
rank in that match (both teams, so it describes the opposition as much as the player). The
band of a game or a player is read off that number. Models take ``game_rank`` as a feature,
so a touch's xG is conditioned on the level of goalkeeping it faced, and references are built
per band so a Diamond player is compared with Diamond players.
"""
from __future__ import annotations

import polars as pl

# (name, lowest rank points, highest rank points); tier*4 + division - 1, see meta.rank_points.
BANDS = [
    ("platinum_or_below", 0, 51),
    ("diamond", 52, 63),
    ("champion", 64, 75),
    ("gc", 76, 88),  # GC1 div1 .. SSL
]


def band_of(points: float | None) -> str | None:
    if points is None:
        return None
    for name, lo, hi in BANDS:
        if lo <= points < hi + 1:  # a mean rank of 63.9 is still a Diamond game
            return name
    return None


def band_range(name: str) -> tuple[int, int]:
    for band, lo, hi in BANDS:
        if band == name:
            return lo, hi
    raise KeyError(f"unknown band {name!r}; known: {[b for b, _, _ in BANDS]}")


def game_ranks(table: pl.DataFrame) -> pl.DataFrame:
    """One row per replay: ``game_rank`` (mean rank points over ranked players) and ``rank_band``."""
    if "rank_points" not in table.columns:
        return pl.DataFrame({"replay_id": pl.Series([], dtype=pl.Utf8), "game_rank": pl.Series([], dtype=pl.Float64),
                             "rank_band": pl.Series([], dtype=pl.Utf8)})
    ranks = (table.group_by("replay_id")
                  .agg(pl.col("rank_points").cast(pl.Float64).mean().alias("game_rank")))
    return ranks.with_columns(pl.col("game_rank").map_elements(band_of, return_dtype=pl.Utf8).alias("rank_band"))


def attach_game_rank(df: pl.DataFrame, table: pl.DataFrame) -> pl.DataFrame:
    """Join ``game_rank`` and ``rank_band`` onto any frame keyed by ``replay_id``."""
    ranks = game_ranks(table)
    if ranks.height == 0:
        return df.with_columns(pl.lit(None, dtype=pl.Float64).alias("game_rank"), pl.lit(None, dtype=pl.Utf8).alias("rank_band"))
    return df.drop([c for c in ("game_rank", "rank_band") if c in df.columns]).join(ranks, on="replay_id", how="left")
