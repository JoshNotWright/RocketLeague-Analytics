"""Per-touch value added and save quality, derived from the scored touch table.

Value added (a possession-value / expected-threat style credit):

    V(touch)      = threat - threat_against        (net 10 s scoring chance for the touching team)
    V_after       = +1 / -1 if a goal follows before the next touch,
                    0 if a kickoff happens before the next touch,
                    else the next touch's V seen from this team's side
    value_added   = V_after - V(touch)

Summed per player it says how much a player's touches moved the game toward their goal and
away from their own, independent of who finally scored.

Save quality: every shot (opponent touch flagged as a shot) is "faced". If it did not
score and a defender touched the ball within SAVE_WINDOW seconds, that defender saved it.
If it scored, the touch is charged to the defender who got a touch on it (failed save) or
else to the defender nearest their goal. Goals prevented above expected (GSAx) per defender
is sum(xG faced) - goals conceded.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import polars as pl

from .xg import split_half

SAVE_WINDOW = 3.0


def add_value(touches: pl.DataFrame) -> pl.DataFrame:
    df = touches.sort("replay_id", "time").with_columns(
        (pl.col("threat") - pl.col("threat_against")).alias("v_now"))
    nxt = {c: pl.col(c).shift(-1).over("replay_id") for c in ("v_now", "team", "time", "time_since_kickoff")}
    goal_before_next = (pl.col("time_to_next_goal").is_not_null()
                        & (pl.col("time_to_next_touch").is_null() | (pl.col("time_to_next_goal") <= pl.col("time_to_next_touch"))))
    kickoff_between = nxt["time_since_kickoff"] < (nxt["time"] - pl.col("time"))
    v_after = (pl.when(pl.col("scored")).then(1.0)              # the scoring touch
                 .when(pl.col("failed_save")).then(-1.0)          # touched it, still went in
                 .when(goal_before_next)
                 .then(pl.when(pl.col("next_goal_team")).then(1.0).otherwise(-1.0))
                 .when(nxt["v_now"].is_null() | kickoff_between).then(0.0)
                 .when(nxt["team"] == pl.col("team")).then(nxt["v_now"])
                 .otherwise(-nxt["v_now"]))
    return df.with_columns(v_after.alias("v_after")).with_columns(
        (pl.col("v_after") - pl.col("v_now")).alias("value_added"))


def saves(touches: pl.DataFrame) -> pl.DataFrame:
    """One row per shot faced: defender credited, xG of the shot, whether it was conceded."""
    df = touches.sort("replay_id", "time").with_columns(
        pl.col("player_id").shift(-1).over("replay_id").alias("next_player_id"),
        pl.col("team").shift(-1).over("replay_id").alias("next_team"),
        pl.col("time").shift(-1).over("replay_id").alias("next_time"),
        pl.col("failed_save").shift(-1).over("replay_id").alias("next_failed_save"),
    )
    shots = df.filter(pl.col("shot"))
    defender_touched = (pl.col("next_team") != pl.col("team")) & ((pl.col("next_time") - pl.col("time")) <= SAVE_WINDOW)
    credited = (pl.when(pl.col("scored") & pl.col("next_failed_save").fill_null(False)).then(pl.col("next_player_id"))
                  .when(pl.col("scored")).then(pl.col("opp_goalie_id"))
                  .when(defender_touched).then(pl.col("next_player_id"))
                  .otherwise(None))
    context = [c for c in ("played_at", "out_of_time") if c in shots.columns]
    return (shots.with_columns(credited.alias("defender_id"))
                 .filter(pl.col("defender_id").is_not_null())
                 .select("replay_id", *context, "time", pl.col("player_id").alias("shooter_id"),
                         "defender_id", pl.col("touch_xg").alias("xg_faced"), pl.col("scored").alias("conceded"))
                 .with_columns((pl.col("xg_faced") - pl.col("conceded").cast(pl.Float64)).alias("gsax")))


def run(touches_scored: pl.DataFrame, out: Path, *, min_games: int = 30) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    valued = add_value(touches_scored)
    valued.write_parquet(out / "touches_valued.parquet")
    per_game = (valued.group_by("replay_id", "player_id", "player_name", "team", "played_at", "out_of_time")
                      .agg(pl.len().alias("touches"), pl.col("value_added").sum().alias("value_added"),
                           pl.col("value_added").mean().alias("value_per_touch"),
                           pl.col("v_now").mean().alias("mean_v_now")))
    per_game.write_parquet(out / "player_game_value.parquet")
    faced = saves(valued)
    faced.write_parquet(out / "shots_faced.parquet")
    def_game = (faced.group_by("replay_id", pl.col("defender_id").alias("player_id"), "played_at", "out_of_time")
                     .agg(pl.len().alias("shots_faced"), pl.col("xg_faced").sum(), pl.col("conceded").sum(),
                          pl.col("gsax").sum()))
    def_game.write_parquet(out / "player_game_saves.parquet")

    players = (per_game.group_by("player_id", "player_name")
                       .agg(pl.len().alias("games"), pl.col("touches").sum(), pl.col("value_added").sum(),
                            pl.col("mean_v_now").mean())
                       .with_columns((pl.col("value_added") / pl.col("games")).alias("value_per_game"),
                                     (pl.col("value_added") / pl.col("touches")).alias("value_per_touch")))
    defenders = (def_game.group_by("player_id")
                         .agg(pl.len().alias("games_with_shots"), pl.col("shots_faced").sum(), pl.col("xg_faced").sum(),
                              pl.col("conceded").sum(), pl.col("gsax").sum())
                         .with_columns((pl.col("gsax") / pl.col("shots_faced")).alias("gsax_per_shot"),
                                       (pl.col("conceded") / pl.col("xg_faced")).alias("concede_ratio")))
    players = players.join(defenders, on="player_id", how="left").sort("games", descending=True)
    players.write_parquet(out / "player_value.parquet")

    # Team-level sanity: summed value added per team-game against that team's goal differential.
    team_game = (valued.group_by("replay_id", "team").agg(pl.col("value_added").sum().alias("team_value"),
                                                           pl.col("scored").sum().cast(pl.Int64).alias("team_goals")))
    opp = team_game.select("replay_id", (1 - pl.col("team")).alias("team"), pl.col("team_goals").alias("opp_goals"))
    team_game = team_game.join(opp, on=["replay_id", "team"]).with_columns((pl.col("team_goals") - pl.col("opp_goals")).alias("goal_diff"))
    report = {
        "touches": valued.height, "shots_faced": faced.height,
        "mean_value_added": float(valued["value_added"].mean()),
        "corr_team_value_vs_goal_diff": float(team_game.select(pl.corr("team_value", "goal_diff")).item()),
        "save_rate": float(1 - faced["conceded"].mean()),
        "mean_xg_faced": float(faced["xg_faced"].mean()),
        "reliability": {
            "value_per_game": split_half(per_game, "value_added", min_games=min_games),
            "value_per_touch": split_half(per_game, "value_added", min_games=min_games, weight="touches"),
            "mean_v_now": split_half(per_game, "mean_v_now", min_games=min_games),
            "touches_per_game": split_half(per_game, "touches", min_games=min_games),
            "gsax_per_game": split_half(def_game, "gsax", min_games=min_games),
            "gsax_per_shot_faced": split_half(def_game, "gsax", min_games=min_games, weight="shots_faced"),
            "shots_faced_per_game": split_half(def_game, "shots_faced", min_games=min_games),
            "xg_faced_per_shot": split_half(def_game, "xg_faced", min_games=min_games, weight="shots_faced"),
        },
    }
    # Dispersion of GSAx against binomial noise, for defenders with enough shots faced.
    noise = (faced.with_columns((pl.col("xg_faced") * (1 - pl.col("xg_faced"))).alias("var"))
                  .group_by("defender_id").agg(pl.col("gsax").sum(), pl.col("var").sum(), pl.len().alias("n"))
                  .filter(pl.col("n") >= 100))
    z = (noise["gsax"] / noise["var"].sqrt()).to_numpy()
    report["gsax_z_variance_ratio"] = float(np.var(z)) if len(z) else float("nan")
    report["gsax_defenders"] = int(len(z))
    report["markdown"] = to_markdown(report, players, min_games)
    (out / "results.json").write_text(json.dumps({k: v for k, v in report.items() if k != "markdown"}, indent=1, default=str))
    (out / "results.md").write_text(report["markdown"], encoding="utf-8")
    return report


def to_markdown(report: dict, players: pl.DataFrame, min_games: int) -> str:
    r = report["reliability"]
    lines = [f"{report['touches']} touches valued; mean value added per touch {report['mean_value_added']:+.4f} "
             f"(should sit near zero). Summed team value per game correlates "
             f"{report['corr_team_value_vs_goal_diff']:.3f} with goal differential.",
             f"{report['shots_faced']} shots faced by a credited defender; save rate {report['save_rate']:.3f}, "
             f"mean xG faced {report['mean_xg_faced']:.3f}. GSAx z-variance ratio across "
             f"{report['gsax_defenders']} defenders with 100+ shots faced: {report['gsax_z_variance_ratio']:.2f} (1.00 = noise).", "",
             "## Split-half reliability (odd vs even games)", "", "| statistic | players | correlation |", "|---|---|---|"]
    for key, value in r.items():
        lines.append(f"| {key} | {value['players']} | {value['split_half_corr']:.3f} |")
    eligible = players.filter(pl.col("games") >= min_games)
    lines += ["", f"## Value added per game, players with {min_games}+ games", "",
              "| player | games | touches | value added / game | value / touch | GSAx | shots faced |", "|---|---|---|---|---|---|---|"]
    ordered = eligible.sort("value_per_game", descending=True)
    rows = list(ordered.head(8).iter_rows(named=True)) + [None] + list(ordered.tail(4).iter_rows(named=True))
    for row in rows:
        if row is None:
            lines.append("| ... | | | | | | |")
            continue
        gsax = f"{row['gsax']:+.1f}" if row["gsax"] is not None else "-"
        faced = row["shots_faced"] if row["shots_faced"] is not None else "-"
        lines.append(f"| {row['player_name']} | {row['games']} | {row['touches']} | {row['value_per_game']:+.3f} | "
                     f"{row['value_per_touch']:+.4f} | {gsax} | {faced} |")
    return "\n".join(lines)
