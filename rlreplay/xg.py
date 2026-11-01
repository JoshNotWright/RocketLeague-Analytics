"""Expected goals from touch events.

Two models, both LightGBM with fixed hyperparameters and a time split by replay date:

* **touch xG** (`scored`): probability that this touch is the one that scores, given the
  full state including the ball's velocity after the touch. Summed per player it is xG.
* **threat** (`team_goal_within_10s`): probability the touching team scores within ten
  seconds, given only the state before the touch. It values the situation, not the touch.

Player analytics are derived from the held-out predictions and from applying the fitted
models to every touch (out-of-time predictions are flagged so they can be preferred).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import polars as pl

from .model import _utc, binary_metrics, importances

STATE_FEATURES = [
    "ball_x", "ball_y", "ball_z", "pre_vx", "pre_vy", "pre_vz", "pre_speed",
    "dist_goal", "dist_own_goal", "goal_angle_width",
    "me_z", "me_speed", "me_boost", "me_on_ground", "me_dist_own_goal",
    "n_mates", "mate_min_dist", "mates_ahead", "mate_min_dist_own_goal",
    "n_opps", "opp_min_dist_ball", "opp_min_dist_goal", "opps_between", "opps_behind_ball", "opp_nearest_boost",
    "seconds_remaining", "time_since_kickoff", "score_diff", "team_size",
]
POST_FEATURES = ["post_vx", "post_vy", "post_vz", "post_speed", "post_toward_goal", "post_angle_off_goal",
                 "proj_x", "proj_z", "proj_t", "shot"]
MODELS = {
    "touch_xg": {"target": "scored", "features": STATE_FEATURES + POST_FEATURES},
    "threat": {"target": "team_goal_within_10s", "features": STATE_FEATURES},
    "threat_against": {"target": "opp_goal_within_10s", "features": STATE_FEATURES},
}
PARAMS = {"objective": "binary", "learning_rate": 0.05, "num_leaves": 63, "min_data_in_leaf": 200,
          "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0,
          "verbose": -1, "seed": 7, "num_threads": 0}


def _matrix(df, features):
    return df.select([pl.col(f).cast(pl.Float64) for f in features]).to_numpy()


def calibration(y, p, bins=10) -> list[dict]:
    order = np.argsort(p)
    chunks = np.array_split(order, bins)
    return [{"bin": i, "n": int(len(c)), "mean_pred": float(p[c].mean()), "mean_actual": float(y[c].mean())}
            for i, c in enumerate(chunks) if len(c)]


def attach_dates(touches: pl.DataFrame, table: pl.DataFrame) -> pl.DataFrame:
    dates = table.select("replay_id", "played_at").unique(subset=["replay_id"])
    return touches.join(dates, on="replay_id", how="left")


def fit(train, valid, features, target):
    import lightgbm as lgb
    dtrain = lgb.Dataset(_matrix(train, features), label=train[target].cast(pl.Float64).to_numpy(),
                         feature_name=features, free_raw_data=False)
    dvalid = lgb.Dataset(_matrix(valid, features), label=valid[target].cast(pl.Float64).to_numpy(), reference=dtrain)
    return lgb.train(PARAMS, dtrain, num_boost_round=4000, valid_sets=[dvalid],
                     callbacks=[lgb.early_stopping(100, verbose=False)])


def run(touches: pl.DataFrame, table: pl.DataFrame, out: Path, *, train_end: str, valid_end: str) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    df = attach_dates(touches, table).filter(pl.col("played_at").is_not_null() & pl.col("player_id").is_not_null())
    a, b = _utc(train_end), _utc(valid_end)
    train = df.filter(pl.col("played_at") < a)
    valid = df.filter((pl.col("played_at") >= a) & (pl.col("played_at") < b))
    test = df.filter(pl.col("played_at") >= b)
    report = {"rows": {"train": train.height, "valid": valid.height, "test": test.height},
              "train_end": train_end, "valid_end": valid_end, "models": {}}
    scored = df
    for name, spec in MODELS.items():
        features = [f for f in spec["features"] if f in df.columns]
        target = spec["target"]
        booster = fit(train, valid, features, target)
        y = test[target].cast(pl.Float64).to_numpy()
        p = booster.predict(_matrix(test, features), num_iteration=booster.best_iteration)
        prior = float(train[target].cast(pl.Float64).mean())
        entry = {
            "target": target, "n_features": len(features), "best_iteration": int(booster.best_iteration),
            "base_rate_train": prior,
            "constant": binary_metrics(y, np.full(len(y), prior)),
            "test": binary_metrics(y, p),
            "calibration": calibration(y, p),
            "importances": importances(booster, features, top=20),
        }
        if name == "touch_xg":
            shots = test.filter(pl.col("shot")) if "shot" in test.columns else None
            if shots is not None and shots.height:
                ys = shots[target].cast(pl.Float64).to_numpy()
                ps = booster.predict(_matrix(shots, features), num_iteration=booster.best_iteration)
                entry["test_shots_only"] = binary_metrics(ys, ps)
        report["models"][name] = entry
        booster.save_model(str(out / f"{name}.lgb.txt"))
        scored = scored.with_columns(pl.Series(name, booster.predict(_matrix(scored, features),
                                                                     num_iteration=booster.best_iteration)))
    scored = scored.with_columns((pl.col("played_at") >= b).alias("out_of_time"))
    scored.write_parquet(out / "touches_scored.parquet")

    per_game = (scored.group_by("replay_id", "player_id", "player_name", "team", "played_at", "out_of_time")
                      .agg(pl.len().alias("touches"), pl.col("shot").sum().alias("shots"),
                           pl.col("scored").sum().alias("goals"), pl.col("touch_xg").sum().alias("xg"),
                           (pl.col("touch_xg") * (1 - pl.col("touch_xg"))).sum().alias("xg_var"),
                           pl.col("threat").mean().alias("mean_threat"),
                           pl.col("touch_xg").filter(pl.col("shot")).sum().alias("xg_on_shots"))
                      .with_columns((pl.col("goals") - pl.col("xg")).alias("goals_above_xg")))
    per_game.write_parquet(out / "player_game_xg.parquet")
    players = (per_game.group_by("player_id", "player_name")
                       .agg(pl.len().alias("games"), pl.col("touches").sum(), pl.col("shots").sum(),
                            pl.col("goals").sum(), pl.col("xg").sum(), pl.col("xg_var").sum(),
                            pl.col("goals_above_xg").sum(), pl.col("mean_threat").mean())
                       .with_columns((pl.col("xg") / pl.col("shots")).alias("xg_per_shot"),
                                     (pl.col("goals") / pl.col("xg")).alias("conversion_ratio"),
                                     (pl.col("goals_above_xg") / pl.col("xg_var").sqrt()).alias("z_goals_above_xg"))
                       .sort("games", descending=True))
    players.write_parquet(out / "player_xg.parquet")
    report["skill_checks"] = skill_checks(per_game, players, min_games=30)

    # Sanity: per player-game xG against actual goals and against ballchasing's goals/shots.
    bc = table.select("replay_id", "player_id", pl.col("core_goals").alias("bc_goals"), pl.col("core_shots").alias("bc_shots"))
    joined = per_game.join(bc, on=["replay_id", "player_id"], how="inner")
    report["player_game_checks"] = {
        "rows": joined.height,
        "corr_xg_vs_goals": float(joined.select(pl.corr("xg", "goals")).item()),
        "corr_goals_vs_bc_goals": float(joined.select(pl.corr("goals", "bc_goals")).item()),
        "corr_shots_vs_bc_shots": float(joined.select(pl.corr("shots", "bc_shots")).item()),
        "mean_shots": float(joined["shots"].mean()), "mean_bc_shots": float(joined["bc_shots"].mean()),
        "mean_goals": float(joined["goals"].mean()), "mean_bc_goals": float(joined["bc_goals"].mean()),
        "mean_xg": float(joined["xg"].mean()),
    }
    report["markdown"] = to_markdown(report, players)
    (out / "results.json").write_text(json.dumps({k: v for k, v in report.items() if k != "markdown"}, indent=1, default=str))
    (out / "results.md").write_text(report["markdown"], encoding="utf-8")
    return report


def split_half(per_game: pl.DataFrame, column: str, *, min_games: int, weight: str | None = None) -> dict:
    """Correlation of a per-player statistic between odd and even games (does it persist?)."""
    ordered = per_game.sort("player_id", "played_at", "replay_id").with_columns(
        (pl.int_range(pl.len()).over("player_id") % 2).alias("half"))
    if weight:
        agg = (pl.col(column).sum() / pl.col(weight).sum()).alias("value")
    else:
        agg = pl.col(column).mean().alias("value")
    halves = ordered.group_by("player_id", "half").agg(agg, pl.len().alias("n"))
    wide = (halves.filter(pl.col("half") == 0).select("player_id", pl.col("value").alias("a"), pl.col("n").alias("na"))
                  .join(halves.filter(pl.col("half") == 1).select("player_id", pl.col("value").alias("b"), pl.col("n").alias("nb")),
                        on="player_id")
                  .filter((pl.col("na") + pl.col("nb")) >= min_games).drop_nulls())
    corr = float(wide.select(pl.corr("a", "b")).item()) if wide.height > 2 else float("nan")
    return {"players": wide.height, "split_half_corr": corr}


def skill_checks(per_game: pl.DataFrame, players: pl.DataFrame, *, min_games: int) -> dict:
    """Is goals-above-xG more than binomial noise, and does anything persist across games?"""
    eligible = players.filter(pl.col("games") >= min_games)
    z = eligible["z_goals_above_xg"].drop_nulls().to_numpy()
    return {
        "min_games": min_games, "players": int(eligible.height),
        # Under "no finishing skill" z ~ N(0, 1): the variance ratio should be about 1.
        "z_variance_ratio": float(np.var(z)) if len(z) else float("nan"),
        "share_abs_z_over_2": float(np.mean(np.abs(z) > 2)) if len(z) else float("nan"),
        "goals_above_xg_per_game": split_half(per_game, "goals_above_xg", min_games=min_games),
        "xg_per_game": split_half(per_game, "xg", min_games=min_games),
        "xg_per_shot": split_half(per_game.filter(pl.col("shots") > 0), "xg_on_shots", min_games=min_games, weight="shots"),
        "mean_threat": split_half(per_game, "mean_threat", min_games=min_games),
        "shots_per_game": split_half(per_game, "shots", min_games=min_games),
    }


def to_markdown(report: dict, players: pl.DataFrame) -> str:
    r = report["rows"]
    lines = [f"Touches: train {r['train']} / valid {r['valid']} / test {r['test']} "
             f"(fit < {report['train_end']}, early-stop to {report['valid_end']}, test after).", ""]
    for name, e in report["models"].items():
        lines += [f"## {name} (target `{e['target']}`, {e['n_features']} features, {e['best_iteration']} rounds)", "",
                  "| model | log loss | AUC | Brier |", "|---|---|---|---|",
                  f"| constant (base rate {e['base_rate_train']:.4f}) | {e['constant']['log_loss']:.4f} | 0.5000 | {e['constant']['brier']:.4f} |",
                  f"| LightGBM | {e['test']['log_loss']:.4f} | {e['test']['auc']:.4f} | {e['test']['brier']:.4f} |"]
        if "test_shots_only" in e:
            s = e["test_shots_only"]
            lines.append(f"| LightGBM, shots only (n={s['n']}) | {s['log_loss']:.4f} | {s['auc']:.4f} | {s['brier']:.4f} |")
        lines += ["", "Calibration (test deciles, mean predicted vs actual):", "",
                  "| decile | n | predicted | actual |", "|---|---|---|---|"]
        lines += [f"| {c['bin']} | {c['n']} | {c['mean_pred']:.4f} | {c['mean_actual']:.4f} |" for c in e["calibration"]]
        lines += ["", "Top features by gain: " + ", ".join(f"{i['feature']} ({i['gain_share']:.2f})" for i in e["importances"][:10]), ""]
    c = report["player_game_checks"]
    lines += ["## Per player-game checks", "",
              f"{c['rows']} player-games joined to ballchasing. corr(xG, goals) = {c['corr_xg_vs_goals']:.3f}; "
              f"corr(our goals, ballchasing goals) = {c['corr_goals_vs_bc_goals']:.3f}; "
              f"corr(our shots, ballchasing shots) = {c['corr_shots_vs_bc_shots']:.3f}.",
              f"Means per player-game: shots {c['mean_shots']:.2f} (ballchasing {c['mean_bc_shots']:.2f}), "
              f"goals {c['mean_goals']:.2f} (ballchasing {c['mean_bc_goals']:.2f}), xG {c['mean_xg']:.2f}.", ""]
    s = report.get("skill_checks")
    if s:
        lines += ["## Is any of it skill?", "",
                  f"{s['players']} players with {s['min_games']}+ games. Variance of z = (goals - xG) / sqrt(sum p(1-p)) "
                  f"is {s['z_variance_ratio']:.2f} (1.00 means pure binomial noise); {s['share_abs_z_over_2']:.0%} of players "
                  f"have |z| > 2 (5% expected by chance).", "",
                  "Split-half reliability (odd vs even games, per player):", "",
                  "| statistic | players | correlation |", "|---|---|---|"]
        for key in ("goals_above_xg_per_game", "xg_per_game", "xg_per_shot", "mean_threat", "shots_per_game"):
            lines.append(f"| {key} | {s[key]['players']} | {s[key]['split_half_corr']:.3f} |")
        lines.append("")
    top = players.filter(pl.col("games") >= 30).sort("goals_above_xg", descending=True)
    lines += ["## Players with 30+ games, by goals above xG", "",
              "| player | games | shots | goals | xG | goals - xG | z | xG/shot |", "|---|---|---|---|---|---|---|---|"]
    for row in top.head(10).iter_rows(named=True):
        lines.append(f"| {row['player_name']} | {row['games']} | {row['shots']} | {row['goals']} | {row['xg']:.1f} | "
                     f"{row['goals_above_xg']:+.1f} | {row['z_goals_above_xg']:+.2f} | {row['xg_per_shot']:.3f} |")
    lines.append("| ... | | | | | | | |")
    for row in top.tail(5).iter_rows(named=True):
        lines.append(f"| {row['player_name']} | {row['games']} | {row['shots']} | {row['goals']} | {row['xg']:.1f} | "
                     f"{row['goals_above_xg']:+.1f} | {row['z_goals_above_xg']:+.2f} | {row['xg_per_shot']:.3f} |")
    return "\n".join(lines)
