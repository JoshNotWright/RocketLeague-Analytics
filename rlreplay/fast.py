"""Frame features and touch rows from a decoded replay (see decode.py), without the raw file.

The archive keeps decoded ``.npz`` files only, so every table the models need must be
buildable from them. ``frames.extract`` and ``events.extract_touches`` walk the raw network
stream; the two functions here produce the same rows from the decoded arrays, with the same
rules (active play only, touch = ball velocity jump attributed to the nearest car within
TOUCH_RADIUS, challenge = opponent within CHALLENGE_RADIUS, kickoff = first touch of a round,
outcomes judged one second later). Checked against the stream versions on real replays in
tests/test_rlreplay_fast.py; the residual differences are listed there.
"""
from __future__ import annotations

import math

import numpy as np

from .events import (BALL_RADIUS, GOAL_HALF_WIDTH, GOAL_Y, TOUCH_RADIUS, is_shot, shot_projection)  # noqa: F401
from .frames import CHALLENGE_RADIUS, KICKOFF_EVAL_DELAY, SUPERSONIC

GOAL_C = np.array([0.0, GOAL_Y, 300.0])
OWN_C = np.array([0.0, -GOAL_Y, 300.0])


def _ffill(values: np.ndarray) -> np.ndarray:
    """Forward-fill NaNs along axis 0 (1-D or 2-D)."""
    v = np.asarray(values, dtype=np.float64)
    if v.ndim == 1:
        ok = ~np.isnan(v)
        idx = np.where(ok, np.arange(len(v)), 0)
        np.maximum.accumulate(idx, out=idx)
        out = v[idx]
        out[~ok.cumsum().astype(bool)] = np.nan
        return out
    out = v.copy()
    for c in range(v.shape[1]):
        out[:, c] = _ffill(v[:, c])
    return out


def _teams(dec: dict) -> np.ndarray:
    return np.array([(-1 if p.get("team") is None else int(p["team"])) for p in dec["players"]], dtype=np.int64)


def _touch_frames(dec: dict, active: np.ndarray, ball_ok: np.ndarray, present: np.ndarray):
    """Frames with an attributed touch: (frame index, toucher slot), nearest present car within TOUCH_RADIUS."""
    cars = dec["cars"].astype(np.float64)
    ball = dec["ball"].astype(np.float64)
    hit = np.flatnonzero(np.asarray(dec["ball_hit"], bool) & active & ball_ok & present.any(1))
    if len(hit) == 0:
        return hit, np.empty(0, dtype=np.int64)
    d = np.linalg.norm(cars[hit, :, :3] - ball[hit, None, :3], axis=2)
    d = np.where(present[hit], d, np.inf)
    nearest = d.argmin(1)
    ok = d[np.arange(len(hit)), nearest] <= TOUCH_RADIUS
    return hit[ok], nearest[ok]


def _eval_after(times: np.ndarray, ball_vy: np.ndarray, t_due: float, sign: float) -> bool | None:
    """Did the ball head toward the opponent half at the first frame at or after t_due? None if no frame."""
    j = int(np.searchsorted(times, t_due, side="left"))
    while j < len(times) and np.isnan(ball_vy[j]):
        j += 1
    if j >= len(times):
        return None
    return bool(ball_vy[j] * sign > 0)


# ---------------------------------------------------------------- frame features

def frames_from_decoded(dec: dict) -> list[dict]:
    """One row per player, the columns of ``frames.extract``."""
    times = np.asarray(dec["times"], dtype=np.float64)
    dt = np.asarray(dec["delta"], dtype=np.float64)
    active = np.asarray(dec["active"], bool)
    ball = np.asarray(dec["ball"], dtype=np.float64)
    cars = np.asarray(dec["cars"], dtype=np.float64)
    T, P, _ = cars.shape
    players = dec["players"]
    team_of = _teams(dec)
    present = ~np.isnan(cars[:, :, 0])
    ball_ok = ~np.isnan(ball[:, 0])
    frame_ok = active & ball_ok & present.any(1)          # the stream version skips frames with no car
    round_start = np.asarray(dec["round_start"], dtype=np.float64)
    rs_known = round_start[~np.isnan(round_start)]
    rounds = int(len(np.unique(rs_known))) if len(rs_known) else 0
    match_active_time = float(dt[active & ball_ok].sum())

    hit_idx, hit_slot = _touch_frames(dec, active, ball_ok, present)
    ball_vy_ff = _ffill(ball[:, 4])
    demos = dec.get("demos") or []

    # kickoff: the first attributed touch after each Active transition
    kickoff_touch = {}   # slot -> list of (touch time, round start time)
    if len(hit_idx):
        rs_at_hit = round_start[hit_idx]
        seen = set()
        for i, slot, rs in zip(hit_idx, hit_slot, rs_at_hit):
            if np.isnan(rs) or rs in seen:
                continue
            seen.add(rs)
            kickoff_touch.setdefault(int(slot), []).append((float(times[i]), float(rs)))

    rows = []
    for p in range(P):
        team = int(team_of[p])
        if team not in (0, 1) or not players[p].get("player_id"):
            continue
        sign = 1.0 if team == 0 else -1.0
        m = frame_ok & present[:, p]
        at = float(dt[m].sum())
        if at <= 0:
            continue
        w = dt[m]
        loc, vel, boost = cars[m, p, :3], cars[m, p, 3:6], cars[m, p, 6]
        speed = np.linalg.norm(vel, axis=1)
        bloc = ball[m, :3]
        has_boost = ~np.isnan(boost)
        bw = w[has_boost]
        boost_time = float(bw.sum())
        mate_slots = [q for q in range(P) if q != p and team_of[q] == team]
        if mate_slots:
            mates = cars[m][:, mate_slots, :3]
            mate_present = ~np.isnan(mates[:, :, 0])
            md = np.where(mate_present, np.linalg.norm(mates - loc[:, None, :], axis=2), np.inf)
            any_mate = mate_present.any(1)
            nearest_mate = md.min(1)
            # deepest defender: own y*sign no greater than every present mate's
            my_y = loc[:, 1] * sign
            mate_y = np.where(mate_present, mates[:, :, 1] * sign, np.inf)
            last_man = any_mate & (my_y <= mate_y.min(1))
        else:
            any_mate = np.zeros(len(w), bool)
            nearest_mate = np.full(len(w), np.inf)
            last_man = np.zeros(len(w), bool)
        mate_time = float(w[any_mate].sum())
        low = has_boost & (boost < 20)

        acc = {
            "active_time": at,
            "speed_sum": float((speed * w).sum()),
            "supersonic_time": float(w[speed >= SUPERSONIC].sum()),
            "ground_time": float(w[loc[:, 2] <= 50].sum()),
            "boost_time": boost_time,
            "boost_sum": float((np.where(has_boost, boost, 0.0) * w).sum()),
            "low_boost_time": float(w[low].sum()),
            "zero_boost_time": float(w[has_boost & (boost <= 0.5)].sum()),
            "behind_ball_time": float(w[(bloc[:, 1] - loc[:, 1]) * sign > 0].sum()),
            "dist_ball_sum": float((np.linalg.norm(loc - bloc, axis=1) * w).sum()),
            "mate_time": mate_time,
            "dist_mate_sum": float((np.where(any_mate, nearest_mate, 0.0) * w).sum()),
            "last_man_time": float(w[last_man].sum()),
            "last_man_low_boost_time": float(w[last_man & low].sum()),
        }

        # touches, challenges, commit distance, kickoffs
        mine = hit_idx[hit_slot == p]
        touches = int(len(mine))
        commit_n, commit_sum, challenges, challenge_wins = 0, 0.0, 0, 0
        for i in mine:
            tloc = ball[i, :3]
            if mate_slots:
                md = [np.linalg.norm(cars[i, q, :3] - cars[i, p, :3]) for q in mate_slots if present[i, q]]
                if md:
                    commit_n += 1
                    commit_sum += float(min(md))
            opp = [q for q in range(P) if team_of[q] == 1 - team and present[i, q]]
            if any(np.linalg.norm(cars[i, q, :3] - tloc) <= CHALLENGE_RADIUS for q in opp):
                challenges += 1
                won = _eval_after(times, ball_vy_ff, float(times[i]) + KICKOFF_EVAL_DELAY, sign)
                challenge_wins += 1 if won else 0
        kicks = kickoff_touch.get(p, [])
        kickoff_wins = 0
        for t_touch, _ in kicks:
            won = _eval_after(times, ball_vy_ff, t_touch + KICKOFF_EVAL_DELAY, sign)
            kickoff_wins += 1 if won else 0

        # pads: boost jumps during active play. The decoder carries boost forward per car actor and
        # leaves NaN until a car's first replicated value, so the previous frame's raw value is the
        # stream version's "previous": NaN right after a goal reset or respawn means no pickup.
        bp = cars[:, p, 6]
        prev = np.roll(bp, 1)
        prev[0] = np.nan
        jump = active & ~np.isnan(bp) & ~np.isnan(prev) & (bp > prev + 0.5)
        big = jump & (bp >= 99.5) & (prev < 88)
        demos_inflicted = sum(1 for d in demos if d.get("attacker_slot") == p)
        demos_taken = sum(1 for d in demos if d.get("victim_slot") == p)

        def ratio(num, den):
            return (num / den) if den else None

        rows.append({
            "replay_id": dec.get("replay_id"),
            "frame_player_name": players[p].get("name"),
            "frame_platform": players[p].get("platform"),
            "frame_uid": players[p]["player_id"].split(":", 1)[1] if ":" in players[p]["player_id"] else players[p]["player_id"],
            "frame_team": team,
            "active_time": at,
            "avg_speed": ratio(acc["speed_sum"], at),
            "supersonic_frac": ratio(acc["supersonic_time"], at),
            "ground_frac": ratio(acc["ground_time"], at),
            "avg_boost": ratio(acc["boost_sum"], acc["boost_time"]),
            "low_boost_frac": ratio(acc["low_boost_time"], acc["boost_time"]),
            "zero_boost_frac": ratio(acc["zero_boost_time"], acc["boost_time"]),
            "behind_ball_frac": ratio(acc["behind_ball_time"], at),
            "avg_dist_ball": ratio(acc["dist_ball_sum"], at),
            "avg_dist_mate": ratio(acc["dist_mate_sum"], acc["mate_time"]),
            "last_man_frac": ratio(acc["last_man_time"], acc["mate_time"]),
            "last_man_low_boost_frac": ratio(acc["last_man_low_boost_time"], acc["mate_time"]),
            "commit_dist_mate": ratio(commit_sum, commit_n),
            "touches": touches,
            "touch_rate": touches / (at / 60.0),
            "challenges": challenges,
            "challenge_rate": challenges / (at / 60.0),
            "challenge_win_rate": ratio(challenge_wins, challenges),
            "kickoffs": len(kicks),
            "kickoff_time_to_ball": (sum(t - rs for t, rs in kicks) / len(kicks)) if kicks else None,
            "kickoff_win_rate": ratio(kickoff_wins, len(kicks)),
            "demos_inflicted": int(demos_inflicted),
            "demos_taken": int(demos_taken),
            "big_pads": int(big.sum()),
            "small_pads": int(jump.sum() - big.sum()),
            "match_active_time": match_active_time,
            "match_rounds": rounds,
        })
    return rows


# ---------------------------------------------------------------- touches

def touches_from_decoded(dec: dict) -> list[dict]:
    """One row per attributed touch during active play, the columns of ``events.extract_touches``."""
    times = np.asarray(dec["times"], dtype=np.float64)
    active = np.asarray(dec["active"], bool)
    ball = np.asarray(dec["ball"], dtype=np.float64)
    cars = np.asarray(dec["cars"], dtype=np.float64)
    T, P, _ = cars.shape
    players = dec["players"]
    team_of = _teams(dec)
    present = ~np.isnan(cars[:, :, 0])
    ball_ok = ~np.isnan(ball[:, 0])
    replay_id = dec.get("replay_id")
    team_size = (dec.get("header") or {}).get("team_size")
    seconds = np.asarray(dec["seconds_remaining"])
    round_start = np.asarray(dec["round_start"], dtype=np.float64)
    goals = sorted(((float(g["time"]), g.get("team"), g.get("scorer")) for g in (dec.get("goals") or [])), key=lambda g: g[0])

    # previous ball velocity = the last known ball velocity before this frame
    vel_ff = _ffill(ball[:, 3:])
    prev_vel = np.roll(vel_ff, 1, axis=0)
    prev_vel[0] = np.nan

    hit_idx, hit_slot = _touch_frames(dec, active, ball_ok, present)
    pid = [p.get("player_id") for p in players]
    name = [p.get("name") for p in players]
    touches = []
    for i, p in zip(hit_idx, hit_slot):
        team = int(team_of[p])
        if team not in (0, 1):
            continue
        t = float(times[i])
        sign = 1.0 if team == 0 else -1.0
        flip = np.array([sign, sign, 1.0])
        b = ball[i, :3] * flip
        pre = np.nan_to_num(prev_vel[i]) * flip
        post = ball[i, 3:] * flip
        me = cars[i, p, :3] * flip
        me_vel = cars[i, p, 3:6] * flip
        me_boost = cars[i, p, 6]
        mates = [q for q in range(P) if q != p and team_of[q] == team and present[i, q]]
        opps = [q for q in range(P) if team_of[q] == 1 - team and present[i, q]]
        mate_locs = [cars[i, q, :3] * flip for q in mates]
        opp_locs = [cars[i, q, :3] * flip for q in opps]
        score = {0: 0, 1: 0}
        for gt, gteam, _ in goals:
            if gt <= t and gteam in score:
                score[gteam] += 1
        to_goal = np.array([GOAL_C[0] - b[0], GOAL_C[1] - b[1], 0.0])
        post_speed = float(np.linalg.norm(post))
        horiz = math.hypot(to_goal[0], to_goal[1]) or 1.0
        toward = float((post[0] * to_goal[0] + post[1] * to_goal[1]) / horiz)
        angle = (math.degrees(math.atan2(abs(post[0] * to_goal[1] - post[1] * to_goal[0]),
                                         post[0] * to_goal[0] + post[1] * to_goal[1])) if post_speed > 1 else None)
        left = math.atan2(-GOAL_HALF_WIDTH - b[0], GOAL_Y - b[1])
        right = math.atan2(GOAL_HALF_WIDTH - b[0], GOAL_Y - b[1])
        proj = shot_projection(tuple(b), tuple(post))
        dist = lambda a, c: float(np.linalg.norm(np.asarray(a) - np.asarray(c)))  # noqa: E731
        opp_by_ball = sorted(((dist(o, b), q) for o, q in zip(opp_locs, opps)), key=lambda x: x[0])
        opp_by_goal = sorted(((dist(o, GOAL_C), q) for o, q in zip(opp_locs, opps)), key=lambda x: x[0])
        mate_by_me = sorted(((dist(mloc, me), q) for mloc, q in zip(mate_locs, mates)), key=lambda x: x[0])
        sec = int(seconds[i])
        rs = float(round_start[i])
        row = {
            "replay_id": replay_id, "frame": int(i), "time": t,
            "player_id": pid[p], "player_name": name[p], "team": team,
            "team_size": team_size, "seconds_remaining": None if sec < 0 else sec,
            "time_since_kickoff": None if np.isnan(rs) else t - rs,
            "score_diff": score[team] - score[1 - team],
            "ball_x": float(b[0]), "ball_y": float(b[1]), "ball_z": float(b[2]),
            "pre_vx": float(pre[0]), "pre_vy": float(pre[1]), "pre_vz": float(pre[2]), "pre_speed": float(np.linalg.norm(pre)),
            "post_vx": float(post[0]), "post_vy": float(post[1]), "post_vz": float(post[2]), "post_speed": post_speed,
            "dist_goal": dist(b, GOAL_C), "dist_own_goal": dist(b, OWN_C),
            "goal_angle_width": math.degrees(right - left),
            "post_toward_goal": toward, "post_angle_off_goal": angle,
            "proj_x": proj[0] if proj else None, "proj_z": proj[1] if proj else None,
            "proj_t": proj[2] if proj else None, "shot": is_shot(tuple(b), tuple(post)),
            "me_z": float(me[2]), "me_speed": float(np.linalg.norm(me_vel)),
            "me_boost": None if np.isnan(me_boost) else float(me_boost),
            "me_on_ground": bool(me[2] <= 50), "me_dist_own_goal": dist(me, OWN_C),
            "n_mates": len(mates),
            "mate_min_dist": mate_by_me[0][0] if mate_by_me else None,
            "mates_ahead": sum(1 for mloc in mate_locs if mloc[1] > b[1]),
            "mate_min_dist_own_goal": min((dist(mloc, OWN_C) for mloc in mate_locs), default=None),
            "n_opps": len(opps),
            "opp_min_dist_ball": opp_by_ball[0][0] if opp_by_ball else None,
            "opp_min_dist_goal": opp_by_goal[0][0] if opp_by_goal else None,
            "opps_between": sum(1 for o in opp_locs if b[1] < o[1] <= GOAL_Y and abs(o[0]) < 2000),
            "opps_behind_ball": sum(1 for o in opp_locs if o[1] > b[1]),
            "opp_nearest_boost": (None if not opp_by_ball or np.isnan(cars[i, opp_by_ball[0][1], 6])
                                  else float(cars[i, opp_by_ball[0][1], 6])),
            "opp_nearest_id": pid[opp_by_ball[0][1]] if opp_by_ball else None,
            "opp_goalie_id": pid[opp_by_goal[0][1]] if opp_by_goal else None,
            "mate_nearest_id": pid[mate_by_me[0][1]] if mate_by_me else None,
        }
        touches.append(row)

    for k, row in enumerate(touches):
        t, team = row["time"], row["team"]
        nxt = touches[k + 1] if k + 1 < len(touches) else None
        row["next_touch_same_team"] = (nxt["team"] == team) if nxt else None
        row["time_to_next_touch"] = (nxt["time"] - t) if nxt else None
        future = [g for g in goals if g[0] > t]
        first = future[0] if future else None
        row["time_to_next_goal"] = (first[0] - t) if first else None
        row["next_goal_team"] = (first[1] == team) if first else None
        row["team_goal_within_5s"] = bool(first and first[1] == team and first[0] - t <= 5.0)
        row["team_goal_within_10s"] = bool(first and first[1] == team and first[0] - t <= 10.0)
        row["opp_goal_within_10s"] = bool(first and first[1] != team and first[0] - t <= 10.0)
        row["scored"] = False
        row["failed_save"] = False
    for gt, gteam, gname in goals:
        candidates = [k for k, r in enumerate(touches)
                      if r["player_name"] == gname and r["team"] == gteam and 0 <= gt - r["time"] <= 10.0]
        if not candidates:
            continue
        shot_index = candidates[-1]
        touches[shot_index]["scored"] = True
        for r in touches[shot_index + 1:]:
            if r["time"] > gt:
                break
            if r["team"] != gteam:
                r["failed_save"] = True
    return touches
