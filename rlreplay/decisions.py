"""Frame-level decision dataset for doubles: what does a player do next from a given state?

``build_rows`` subsamples a replay's active frames at ``hz`` and emits one row per
(frame, player) with the full 2v2 state in that player's attacking frame plus a label for
what the player did over the next ``horizon`` seconds:

    challenge   touched the ball
    big_pad     boost jumped to (near) full without touching the ball
    retreat     moved toward own goal by more than RETREAT_UU and did none of the above
    advance     moved toward the opponent goal by more than RETREAT_UU and did none of the above
    hold        none of the above (shadowing, waiting, small adjustments)

Labels come only from that player's own future trajectory, so a model fitted on them is a
behavioural-cloning policy of the population it was trained on: at GC+, "what would a GC+
player do here". The log-likelihood of a player's actual choices under that policy is a
decision-similarity score, not a value judgement; combine it with threat to see whether
deviations helped or hurt.
"""
from __future__ import annotations

import math

from .events import GOAL_Y, player_key
from .stream import iter_snapshots

ACTIONS = ["challenge", "big_pad", "retreat", "advance", "hold"]
STATE_FEATURES = (
    ["seconds_remaining", "time_since_kickoff", "ball_x", "ball_y", "ball_z", "ball_vx", "ball_vy", "ball_vz",
     "ball_dist_goal", "ball_dist_own_goal", "me_closer_than_mate", "me_deeper_than_mate"]
    + [f"{p}_{f}" for p in ("me", "mate", "opp1", "opp2")
       for f in ("x", "y", "z", "vx", "vy", "vz", "speed", "boost", "dist_ball", "ahead_of_ball")]
    + ["closing_speed", "time_to_ball"]
)
RANK_FEATURES = ["me_rank", "game_rank"]  # joined at fit time from the player-game table (meta.rank_points)
GC_RANK = 80.0  # GC2 div 1 on the rank-points scale: the counterfactual "what would a GC player do here"
RETREAT_UU = 1000.0
TOUCH_RADIUS = 350.0


def attach_ranks(lazy, table):
    """Join the player's own rank points (``me_rank``) and the match mean (``game_rank``) onto decision rows."""
    import polars as pl

    if "rank_points" not in table.columns:
        return lazy.with_columns(pl.lit(None, dtype=pl.Float64).alias("me_rank"), pl.lit(None, dtype=pl.Float64).alias("game_rank"))
    own = (table.select("replay_id", "player_id", pl.col("rank_points").cast(pl.Float64).alias("me_rank"))
                .unique(subset=["replay_id", "player_id"]))
    game = table.group_by("replay_id").agg(pl.col("rank_points").cast(pl.Float64).mean().alias("game_rank"))
    return lazy.join(own.lazy(), on=["replay_id", "player_id"], how="left").join(game.lazy(), on="replay_id", how="left")


def _flip(v, sign):
    return (v[0] * sign, v[1] * sign, v[2])


def _dist(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def _norm(v):
    return math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)


def _car_block(prefix, loc, vel, boost, ball):
    speed = _norm(vel)
    return {f"{prefix}_x": loc[0], f"{prefix}_y": loc[1], f"{prefix}_z": loc[2],
            f"{prefix}_vx": vel[0], f"{prefix}_vy": vel[1], f"{prefix}_vz": vel[2], f"{prefix}_speed": speed,
            f"{prefix}_boost": boost, f"{prefix}_dist_ball": _dist(loc, ball),
            f"{prefix}_ahead_of_ball": loc[1] > ball[1]}


def build_rows(replay_json: dict, *, replay_id: str | None = None, hz: float = 5.0, horizon: float = 1.0) -> list[dict]:
    """Rows for a doubles replay; returns [] for other team sizes."""
    if replay_json["properties"].get("TeamSize") != 2:
        return []
    snaps = [s for s in iter_snapshots(replay_json)]
    # Index of per-player trajectories for the future lookups: time -> (loc, boost) per pri.
    traj = {}
    touch_times = {}  # pri -> sorted list of touch times
    for s in snaps:
        if not s.elapsed_active or s.ball_loc is None:
            continue
        for pri, car in s.cars.items():
            traj.setdefault(pri, []).append((s.time, car.loc, car.boost))
        if s.ball_hit and s.cars:
            pri, car = min(s.cars.items(), key=lambda kv: _dist(kv[1].loc, s.ball_loc))
            if _dist(car.loc, s.ball_loc) <= TOUCH_RADIUS:
                touch_times.setdefault(pri, []).append(s.time)

    import bisect
    def future_state(pri, t_end):
        points = traj.get(pri, [])
        times = [p[0] for p in points]
        i = bisect.bisect_right(times, t_end) - 1
        return points[i] if i >= 0 and points[i][0] > t_end - 0.5 else None

    def touched_within(pri, t0, t1):
        times = touch_times.get(pri, [])
        i = bisect.bisect_right(times, t0)
        return i < len(times) and times[i] <= t1

    rows = []
    next_sample = None
    step = 1.0 / hz
    for s in snaps:
        if not s.elapsed_active or s.ball_loc is None or len(s.cars) != 4:
            continue
        if next_sample is not None and s.time < next_sample:
            continue
        next_sample = s.time + step
        teams = {}
        for pri, car in s.cars.items():
            if car.player.team is None:
                teams = None
                break
            teams.setdefault(car.player.team, []).append((pri, car))
        if not teams or any(len(v) != 2 for v in teams.values()):
            continue
        for pri, car in s.cars.items():
            team = car.player.team
            sign = 1.0 if team == 0 else -1.0
            ball = _flip(s.ball_loc, sign)
            ball_v = _flip(s.ball_vel or (0.0, 0.0, 0.0), sign)
            me_loc, me_vel = _flip(car.loc, sign), _flip(car.vel, sign)
            mate = [c for p, c in teams[team] if p != pri][0]
            opps = sorted((c for _, c in teams[1 - team]), key=lambda c: _dist(_flip(c.loc, sign), ball))
            row = {"replay_id": replay_id, "time": s.time, "player_id": player_key(car.player),
                   "player_name": car.player.name, "team": team, "seconds_remaining": s.seconds_remaining,
                   "time_since_kickoff": (s.time - s.round_start) if s.round_start is not None else None,
                   "ball_x": ball[0], "ball_y": ball[1], "ball_z": ball[2],
                   "ball_vx": ball_v[0], "ball_vy": ball_v[1], "ball_vz": ball_v[2],
                   "ball_dist_goal": _dist(ball, (0.0, GOAL_Y, 300.0)), "ball_dist_own_goal": _dist(ball, (0.0, -GOAL_Y, 300.0))}
            row.update(_car_block("me", me_loc, me_vel, car.boost, ball))
            row.update(_car_block("mate", _flip(mate.loc, sign), _flip(mate.vel, sign), mate.boost, ball))
            for k, o in enumerate(opps, 1):
                row.update(_car_block(f"opp{k}", _flip(o.loc, sign), _flip(o.vel, sign), o.boost, ball))
            row["me_closer_than_mate"] = row["me_dist_ball"] < row["mate_dist_ball"]
            row["me_deeper_than_mate"] = me_loc[1] < row["mate_y"]
            # Momentum context: how fast the player is already closing on the ball, and whether
            # the next-horizon outcome is already decided by current motion. Rows where it is
            # are kept (for context) but flagged so a policy can be fitted on decision points only.
            to_ball = (ball[0] - me_loc[0], ball[1] - me_loc[1], ball[2] - me_loc[2])
            dist = _norm(to_ball) or 1.0
            closing = ((me_vel[0] - ball_v[0]) * to_ball[0] + (me_vel[1] - ball_v[1]) * to_ball[1]
                       + (me_vel[2] - ball_v[2]) * to_ball[2]) / dist
            row["closing_speed"] = closing
            row["time_to_ball"] = dist / closing if closing > 1.0 else None
            committed_to_ball = row["time_to_ball"] is not None and row["time_to_ball"] < horizon * 0.5
            committed_y = abs(me_vel[1]) * horizon > RETREAT_UU * 1.5
            row["decision_point"] = not (committed_to_ball or committed_y)
            # label from the player's own future
            t1 = s.time + horizon
            fut = future_state(pri, t1)
            if fut is None:
                continue
            _, fut_loc, fut_boost = fut
            fut_loc = _flip(fut_loc, sign)
            if touched_within(pri, s.time, t1):
                action = "challenge"
            elif car.boost is not None and fut_boost is not None and fut_boost >= 99 and car.boost < 85:
                action = "big_pad"
            elif fut_loc[1] - me_loc[1] < -RETREAT_UU:
                action = "retreat"
            elif fut_loc[1] - me_loc[1] > RETREAT_UU:
                action = "advance"
            else:
                action = "hold"
            row["action"] = action
            row["action_id"] = ACTIONS.index(action)
            rows.append(row)
    return rows


# ----------------------------------------------------------------------------- fitting

PARAMS = {"objective": "multiclass", "num_class": len(ACTIONS), "learning_rate": 0.05, "num_leaves": 127,
          "min_data_in_leaf": 500, "feature_fraction": 0.8, "bagging_fraction": 0.5, "bagging_freq": 1,
          "lambda_l2": 1.0, "verbose": -1, "seed": 7, "num_threads": 0}


def fit_policy(frames_dir, table, out, *, train_end: str, valid_end: str, sample_rows: int = 3_000_000, seed: int = 7,
               decision_points_only: bool = False, rank_features: bool = True, gc_rank: float = GC_RANK) -> dict:
    """Fit the behavioural policy on a date split and score per-player decision similarity.

    With ``decision_points_only`` rows whose outcome is already determined by momentum (closing
    on the ball within half the horizon, or already moving fast along the pitch) are dropped,
    so the model predicts choices rather than continuations.

    With ``rank_features`` the player's own rank and the match's mean rank (from ``table``) are
    model inputs, so one policy spans the ladder. Held-out rows are then also scored under the
    counterfactual "same state, GC player" (both ranks set to ``gc_rank``): the GC gap per
    player-game is how much less likely that policy finds the player's actual choices than the
    policy at their own rank, and the action matrix says where the two disagree.
    """
    import json
    from pathlib import Path

    import lightgbm as lgb
    import numpy as np
    import polars as pl

    from .model import _utc

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    dates = table.select("replay_id", "played_at").unique(subset=["replay_id"])
    dirs = [frames_dir] if isinstance(frames_dir, (str, Path)) else list(frames_dir)
    lazy = pl.scan_parquet([str(Path(d) / "*.parquet") for d in dirs]).join(dates.lazy(), on="replay_id", how="inner")
    if rank_features:
        lazy = attach_ranks(lazy, table)
    if decision_points_only:
        lazy = lazy.filter(pl.col("decision_point"))
    a, b = _utc(train_end), _utc(valid_end)
    total = int(lazy.select(pl.len()).collect().item())
    frac = min(1.0, sample_rows / max(total, 1))
    if frac < 1.0:
        key = pl.struct(["replay_id", "time", "player_id"]).hash(seed) % 1000
        df = lazy.filter(key < int(frac * 1000)).collect()
    else:
        df = lazy.collect()
    train = df.filter(pl.col("played_at") < a)
    valid = df.filter((pl.col("played_at") >= a) & (pl.col("played_at") < b))
    test = df.filter(pl.col("played_at") >= b)
    features = [f for f in STATE_FEATURES if f in df.columns]
    if rank_features:
        features += [f for f in RANK_FEATURES if f in df.columns and df[f].null_count() < df.height]
    ranked = rank_features and "me_rank" in features

    def mat(d):
        return d.select([pl.col(f).cast(pl.Float64) for f in features]).to_numpy()

    dtrain = lgb.Dataset(mat(train), label=train["action_id"].to_numpy(), feature_name=features, free_raw_data=False)
    dvalid = lgb.Dataset(mat(valid), label=valid["action_id"].to_numpy(), reference=dtrain)
    booster = lgb.train(PARAMS, dtrain, num_boost_round=2000, valid_sets=[dvalid],
                        callbacks=[lgb.early_stopping(50, verbose=False)])
    booster.save_model(str(out / "policy.lgb.txt"))

    y = test["action_id"].to_numpy()
    p = booster.predict(mat(test), num_iteration=booster.best_iteration)
    prior = np.bincount(train["action_id"].to_numpy(), minlength=len(ACTIONS)) / train.height
    ll_model = float(-np.mean(np.log(np.clip(p[np.arange(len(y)), y], 1e-9, 1))))
    ll_prior = float(-np.mean(np.log(np.clip(prior[y], 1e-9, 1))))
    acc = float(np.mean(p.argmax(axis=1) == y))
    per_class = {}
    for k, name in enumerate(ACTIONS):
        mask = y == k
        per_class[name] = {"share": float(mask.mean()), "recall": float(np.mean(p[mask].argmax(axis=1) == k)) if mask.any() else None,
                           "mean_prob_when_true": float(p[mask, k].mean()) if mask.any() else None}
    gain = booster.feature_importance(importance_type="gain")
    order = np.argsort(gain)[::-1][:15]
    importances = [{"feature": features[i], "gain_share": float(gain[i] / gain.sum())} for i in order]

    # Per-player decision similarity on held-out games: mean log-likelihood of the chosen action.
    test = test.with_columns(pl.Series("loglik", np.log(np.clip(p[np.arange(len(y)), y], 1e-9, 1))),
                             pl.Series("p_challenge", p[:, ACTIONS.index("challenge")]))
    aggs = [pl.len().alias("frames"), pl.col("loglik").mean().alias("decision_loglik"),
            (pl.col("action") == "challenge").mean().alias("challenge_rate"),
            pl.col("p_challenge").mean().alias("policy_challenge_rate")]
    counterfactual = None
    if ranked:
        # Same states, GC player: both rank inputs set to gc_rank. The GC gap is the extra surprise
        # of the player's actual choices under that policy; the action matrix says where it disagrees.
        gc_test = test.with_columns(pl.lit(gc_rank).alias("me_rank"), pl.lit(gc_rank).alias("game_rank"))
        p_gc = booster.predict(mat(gc_test), num_iteration=booster.best_iteration)
        gc_argmax = p_gc.argmax(axis=1)
        test = test.with_columns(pl.Series("loglik_gc", np.log(np.clip(p_gc[np.arange(len(y)), y], 1e-9, 1))),
                                 pl.Series("gc_action", [ACTIONS[k] for k in gc_argmax]),
                                 pl.Series("p_gc_challenge", p_gc[:, ACTIONS.index("challenge")]),
                                 pl.col("me_rank").alias("me_rank_row"))
        aggs += [(pl.col("loglik") - pl.col("loglik_gc")).mean().alias("gc_gap"),
                 pl.col("p_gc_challenge").mean().alias("gc_policy_challenge_rate"),
                 ((pl.col("action") == "retreat") & (pl.col("gc_action") == "challenge")).mean().alias("retreat_where_gc_challenges"),
                 ((pl.col("action") == "challenge") & (pl.col("gc_action") != "challenge")).mean().alias("challenge_where_gc_would_not"),
                 pl.col("me_rank_row").mean().alias("me_rank")]
        matrix = (test.group_by("action", "gc_action").len().sort("action", "gc_action"))
        by_band = (test.with_columns((pl.col("me_rank_row") // 12 * 12).alias("rank_bin"))
                       .group_by("rank_bin").agg(pl.len().alias("rows"), (pl.col("loglik") - pl.col("loglik_gc")).mean().alias("gc_gap"),
                                                 (pl.col("action") == "challenge").mean().alias("challenge_rate"),
                                                 pl.col("p_gc_challenge").mean().alias("gc_policy_challenge_rate"))
                       .sort("rank_bin"))
        counterfactual = {"gc_rank": gc_rank,
                          "action_matrix": {f"{r['action']}->{r['gc_action']}": int(r["len"]) for r in matrix.iter_rows(named=True)},
                          "by_rank_bin": [{k: (float(v) if isinstance(v, float) else v) for k, v in r.items()} for r in by_band.iter_rows(named=True)]}
    per_game = test.group_by("replay_id", "player_id", "player_name", "played_at").agg(*aggs)
    per_game.write_parquet(out / "player_game_decisions.parquet")
    from .xg import split_half
    reliability = {"decision_loglik": split_half(per_game, "decision_loglik", min_games=10),
                   "challenge_rate": split_half(per_game, "challenge_rate", min_games=10),
                   "challenge_minus_policy": split_half(per_game.with_columns((pl.col("challenge_rate") - pl.col("policy_challenge_rate")).alias("d")), "d", min_games=10)}
    if ranked:
        reliability["gc_gap"] = split_half(per_game, "gc_gap", min_games=10)
        reliability["retreat_where_gc_challenges"] = split_half(per_game, "retreat_where_gc_challenges", min_games=10)
        reliability["challenge_where_gc_would_not"] = split_half(per_game, "challenge_where_gc_would_not", min_games=10)
    player_aggs = [pl.len().alias("games"), pl.col("decision_loglik").mean(), pl.col("challenge_rate").mean(), pl.col("policy_challenge_rate").mean()]
    if ranked:
        player_aggs += [pl.col("gc_gap").mean(), pl.col("retreat_where_gc_challenges").mean(),
                        pl.col("challenge_where_gc_would_not").mean(), pl.col("me_rank").mean()]
    players = (per_game.group_by("player_id", "player_name").agg(*player_aggs)
                       .filter(pl.col("games") >= 10).sort("decision_loglik", descending=True))
    players.write_parquet(out / "player_decisions.parquet")
    report = {"decision_points_only": decision_points_only, "rank_features": ranked,
              "rows": {"total_available": total, "sampled": df.height, "train": train.height, "valid": valid.height, "test": test.height},
              "best_iteration": int(booster.best_iteration), "test_logloss": ll_model, "prior_logloss": ll_prior,
              "accuracy": acc, "prior_accuracy": float(prior.max()), "per_class": per_class, "importances": importances,
              "reliability": reliability, "counterfactual": counterfactual}
    lines = [f"Decision policy (5 actions, {len(features)} state features{', decision points only' if decision_points_only else ''}). "
             f"Rows: {total} available, {df.height} sampled, "
             f"train {train.height} / valid {valid.height} / test {test.height}; {booster.best_iteration} rounds.", "",
             f"Held-out log loss {ll_model:.4f} vs prior {ll_prior:.4f}; accuracy {acc:.3f} vs majority {prior.max():.3f}.", "",
             "| action | share | recall | mean p when true |", "|---|---|---|---|"]
    def _f(x):
        return "-" if x is None else f"{x:.3f}"
    lines += [f"| {k} | {_f(v['share'])} | {_f(v['recall'])} | {_f(v['mean_prob_when_true'])} |" for k, v in per_class.items()]
    lines += ["", "Top features: " + ", ".join(f"{i['feature']} ({i['gain_share']:.2f})" for i in importances[:10]), "",
              "Split-half reliability of per-player decision statistics (held-out games, 10+ games):", "",
              "| statistic | players | correlation |", "|---|---|---|"]
    lines += [f"| {k} | {v['players']} | {v['split_half_corr']:.3f} |" for k, v in reliability.items()]
    if counterfactual:
        lines += ["", f"Counterfactual: the same held-out states scored with both rank inputs set to {gc_rank:.0f} rank points (GC).", "",
                  "| player rank bin | rows | challenge rate | GC policy challenge rate | GC gap (nats) |", "|---|---|---|---|---|"]
        lines += [f"| {int(r['rank_bin'])}-{int(r['rank_bin']) + 11} | {r['rows']} | {r['challenge_rate']:.3f} | {r['gc_policy_challenge_rate']:.3f} | {r['gc_gap']:.4f} |"
                  for r in counterfactual["by_rank_bin"] if r["rank_bin"] is not None]
        total_rows = sum(counterfactual["action_matrix"].values()) or 1
        lines += ["", "Actual action -> action the GC-conditioned policy rates most likely (share of held-out rows):", "",
                  "| actual \\ GC policy | " + " | ".join(ACTIONS) + " |", "|---|" + "---|" * len(ACTIONS)]
        for actual in ACTIONS:
            cells = [counterfactual["action_matrix"].get(f"{actual}->{gc}", 0) / total_rows for gc in ACTIONS]
            lines.append(f"| {actual} | " + " | ".join(f"{c:.3f}" for c in cells) + " |")
    lines += ["", "Players by decision log-likelihood under the policy at their own rank (held-out games):", "",
              "| player | games | mean loglik | challenge rate | policy challenge rate |", "|---|---|---|---|---|"]
    for row in list(players.head(6).iter_rows(named=True)) + list(players.tail(4).iter_rows(named=True)):
        lines.append(f"| {row['player_name']} | {row['games']} | {row['decision_loglik']:.4f} | {row['challenge_rate']:.3f} | {row['policy_challenge_rate']:.3f} |")
    report["markdown"] = "\n".join(lines)
    (out / "results.json").write_text(json.dumps({k: v for k, v in report.items() if k != "markdown"}, indent=1, default=str))
    (out / "results.md").write_text(report["markdown"], encoding="utf-8")
    return report


def cross_with_value(decisions_dir, value_dir, out=None) -> dict:
    """Does deviating from the GC+ policy help or hurt? Join decision similarity with value added."""
    import json
    from pathlib import Path

    import polars as pl

    from .xg import split_half

    dec = pl.read_parquet(Path(decisions_dir) / "player_game_decisions.parquet")
    val = pl.read_parquet(Path(value_dir) / "player_game_value.parquet").select("replay_id", "player_id", "value_added", "touches")
    xg = pl.read_parquet(Path(value_dir).parent / "v2-xg" / "player_game_xg.parquet").select("replay_id", "player_id", "xg", "goals", "mean_threat")
    game = dec.join(val, on=["replay_id", "player_id"], how="inner").join(xg, on=["replay_id", "player_id"], how="left")
    game = game.with_columns((pl.col("challenge_rate") - pl.col("policy_challenge_rate")).alias("challenge_excess"))
    player = (game.group_by("player_id", "player_name")
                  .agg(pl.len().alias("games"), pl.col("decision_loglik").mean(), pl.col("challenge_excess").mean(),
                       pl.col("value_added").mean(), pl.col("mean_threat").mean(), pl.col("xg").mean())
                  .filter(pl.col("games") >= 10))

    def corr(df, a, b):
        return float(df.select(pl.corr(a, b)).item()) if df.height > 2 else float("nan")

    result = {
        "player_games": game.height, "players": player.height,
        "game_level": {"corr_loglik_value_added": corr(game, "decision_loglik", "value_added"),
                       "corr_challenge_excess_value_added": corr(game, "challenge_excess", "value_added"),
                       "corr_loglik_mean_threat": corr(game, "decision_loglik", "mean_threat")},
        "player_level": {"corr_loglik_value_added": corr(player, "decision_loglik", "value_added"),
                         "corr_challenge_excess_value_added": corr(player, "challenge_excess", "value_added"),
                         "corr_loglik_xg": corr(player, "decision_loglik", "xg")},
        "reliability_value_added_these_games": split_half(game, "value_added", min_games=10),
    }
    if out:
        Path(out).write_text(json.dumps(result, indent=1, default=str))
    return result
