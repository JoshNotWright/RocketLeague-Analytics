"""Empirical-Bayes shrinkage for per-player means of noisy per-game statistics.

For a statistic observed once per game, a player's raw mean over n games is pulled toward
the population mean by the factor tau^2 / (tau^2 + sigma^2 / n), where sigma^2 is the
within-player game-to-game variance (pooled) and tau^2 the between-player variance of
true means, estimated by the method of moments:

    tau^2 = max(var(raw means) - mean(sigma^2 / n), 0)

With tau^2 = 0 every player is shrunk to the population mean, which is the right answer for a
statistic with no split-half reliability. The reliability of a player's mean is the same
factor, so it is reported alongside.
"""
from __future__ import annotations

import polars as pl


def shrink_means(per_game: pl.DataFrame, column: str, *, by: str = "player_id", min_games: int = 2) -> pl.DataFrame:
    """Return one row per player with raw mean, games, shrunk mean and the shrink factor for ``column``."""
    stats = (per_game.filter(pl.col(column).is_not_null())
                     .group_by(by)
                     .agg(pl.len().alias("games"), pl.col(column).mean().alias("raw_mean"),
                          pl.col(column).var(ddof=1).alias("within_var"))
                     .filter(pl.col("games") >= min_games))
    if stats.height == 0:
        return stats.with_columns(pl.lit(column).alias("statistic"), pl.lit(0.0).alias("shrink_factor"),
                                  pl.col("raw_mean").alias("shrunk_mean"), pl.lit(None, pl.Float64).alias("population_mean"),
                                  pl.lit(0.0).alias("between_var"), pl.lit(0.0).alias("within_var_pooled"))
    # Pooled within-player variance weighted by degrees of freedom, so one erratic player cannot dominate.
    pooled = float(((stats["within_var"] * (stats["games"] - 1)).sum()) / float((stats["games"] - 1).sum()))
    mu = float((stats["raw_mean"] * stats["games"]).sum() / stats["games"].sum())
    noise_in_means = float((pooled / stats["games"]).mean())
    # Between-player variance needs several players to estimate; with fewer, assume none (full shrinkage).
    tau2 = max(float(stats["raw_mean"].var(ddof=1)) - noise_in_means, 0.0) if stats.height >= 3 else 0.0
    factor = tau2 / (tau2 + pooled / pl.col("games")) if tau2 > 0 else pl.lit(0.0)
    return (stats.with_columns(factor.alias("shrink_factor"))
                 .with_columns((mu + pl.col("shrink_factor") * (pl.col("raw_mean") - mu)).alias("shrunk_mean"),
                               pl.lit(column).alias("statistic"), pl.lit(mu).alias("population_mean"),
                               pl.lit(tau2).alias("between_var"), pl.lit(pooled).alias("within_var_pooled")))


def shrink_table(per_game: pl.DataFrame, columns, *, by: str = "player_id", min_games: int = 2) -> pl.DataFrame:
    """Long table of shrunk means for several statistics."""
    cols = [by, "statistic", "games", "raw_mean", "shrunk_mean", "shrink_factor",
            "population_mean", "between_var", "within_var_pooled"]
    parts = [shrink_means(per_game, c, by=by, min_games=min_games).select(cols)
             for c in columns if c in per_game.columns]
    if not parts:
        return pl.DataFrame(schema={c: pl.Float64 for c in cols} | {by: pl.Utf8, "statistic": pl.Utf8, "games": pl.UInt32})
    return pl.concat(parts, how="vertical_relaxed")
