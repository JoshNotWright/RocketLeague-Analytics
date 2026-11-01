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
)
RETREAT_UU = 1000.0
TOUCH_RADIUS = 350.0


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


def fit_policy(frames_dir, table, out, *, train_end: str, valid_end: str, sample_rows: int = 3_000_000, seed: int = 7) -> dict:
    """Fit the behavioural policy on a date split and score per-player decision similarity."""
    import json
    from pathlib import Path

    import lightgbm as lgb
    import numpy as np
    import polars as pl

    from .model import _utc

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    dates = table.select("replay_id", "played_at").unique(subset=["replay_id"])
    lazy = pl.scan_parquet(str(Path(frames_dir) / "*.parquet")).join(dates.lazy(), on="replay_id", how="inner")
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
    per_game = (test.group_by("replay_id", "player_id", "player_name", "played_at")
                    .agg(pl.len().alias("frames"), pl.col("loglik").mean().alias("decision_loglik"),
                         (pl.col("action") == "challenge").mean().alias("challenge_rate"),
                         pl.col("p_challenge").mean().alias("policy_challenge_rate")))
    per_game.write_parquet(out / "player_game_decisions.parquet")
    from .xg import split_half
    reliability = {"decision_loglik": split_half(per_game, "decision_loglik", min_games=10),
                   "challenge_rate": split_half(per_game, "challenge_rate", min_games=10),
                   "challenge_minus_policy": split_half(per_game.with_columns((pl.col("challenge_rate") - pl.col("policy_challenge_rate")).alias("d")), "d", min_games=10)}
    players = (per_game.group_by("player_id", "player_name").agg(pl.len().alias("games"), pl.col("decision_loglik").mean(),
                                                                 pl.col("challenge_rate").mean(), pl.col("policy_challenge_rate").mean())
                       .filter(pl.col("games") >= 10).sort("decision_loglik", descending=True))
    players.write_parquet(out / "player_decisions.parquet")
    report = {"rows": {"total_available": total, "sampled": df.height, "train": train.height, "valid": valid.height, "test": test.height},
              "best_iteration": int(booster.best_iteration), "test_logloss": ll_model, "prior_logloss": ll_prior,
              "accuracy": acc, "prior_accuracy": float(prior.max()), "per_class": per_class, "importances": importances,
              "reliability": reliability}
    lines = [f"Decision policy (5 actions, {len(features)} state features). Rows: {total} available, {df.height} sampled, "
             f"train {train.height} / valid {valid.height} / test {test.height}; {booster.best_iteration} rounds.", "",
             f"Held-out log loss {ll_model:.4f} vs prior {ll_prior:.4f}; accuracy {acc:.3f} vs majority {prior.max():.3f}.", "",
             "| action | share | recall | mean p when true |", "|---|---|---|---|"]
    lines += [f"| {k} | {v['share']:.3f} | {v['recall']:.3f} | {v['mean_prob_when_true']:.3f} |" for k, v in per_class.items()]
    lines += ["", "Top features: " + ", ".join(f"{i['feature']} ({i['gain_share']:.2f})" for i in importances[:10]), "",
              "Split-half reliability of per-player decision statistics (held-out games, 10+ games):", "",
              "| statistic | players | correlation |", "|---|---|---|"]
    lines += [f"| {k} | {v['players']} | {v['split_half_corr']:.3f} |" for k, v in reliability.items()]
    lines += ["", "Players by decision log-likelihood under the GC+ policy (held-out games):", "",
              "| player | games | mean loglik | challenge rate | policy challenge rate |", "|---|---|---|---|---|"]
    for row in list(players.head(6).iter_rows(named=True)) + list(players.tail(4).iter_rows(named=True)):
        lines.append(f"| {row['player_name']} | {row['games']} | {row['decision_loglik']:.4f} | {row['challenge_rate']:.3f} | {row['policy_challenge_rate']:.3f} |")
    report["markdown"] = "\n".join(lines)
    (out / "results.json").write_text(json.dumps({k: v for k, v in report.items() if k != "markdown"}, indent=1, default=str))
    (out / "results.md").write_text(report["markdown"], encoding="utf-8")
    return report
