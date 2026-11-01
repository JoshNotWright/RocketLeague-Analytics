"""Vectorized decision rows from a decoded replay (see decode.py).

Produces the same columns and labels as ``decisions.build_rows`` but from numpy arrays, so a
new horizon or label rule costs well under a second per replay instead of several seconds
of stream walking. Rules mirror ``build_rows``: 5 Hz sampling keyed off the previous sample,
four cars present with two per team, labels from the player's own future trajectory, and
momentum flags for decision points.
"""
from __future__ import annotations

import numpy as np
import polars as pl

from .decisions import ACTIONS, RETREAT_UU, TOUCH_RADIUS
from .events import GOAL_Y

GOAL_C = np.array([0.0, GOAL_Y, 300.0], dtype=np.float64)
OWN_C = np.array([0.0, -GOAL_Y, 300.0], dtype=np.float64)


def _block(prefix, loc, vel, boost, ball_loc):
    speed = np.linalg.norm(vel, axis=1)
    return {f"{prefix}_x": loc[:, 0], f"{prefix}_y": loc[:, 1], f"{prefix}_z": loc[:, 2],
            f"{prefix}_vx": vel[:, 0], f"{prefix}_vy": vel[:, 1], f"{prefix}_vz": vel[:, 2],
            f"{prefix}_speed": speed, f"{prefix}_boost": boost,
            f"{prefix}_dist_ball": np.linalg.norm(loc - ball_loc, axis=1),
            f"{prefix}_ahead_of_ball": loc[:, 1] > ball_loc[:, 1]}


def sample_indices(times, usable, hz: float):
    step = 1.0 / hz
    out, nxt = [], None
    for i in np.flatnonzero(usable):
        t = float(times[i])
        if nxt is not None and t < nxt:
            continue
        nxt = t + step
        out.append(i)
    return np.asarray(out, dtype=np.int64)


def rows_from_decoded(dec: dict, *, hz: float = 5.0, horizon: float = 1.0) -> pl.DataFrame:
    if dec["header"].get("team_size") != 2:
        return pl.DataFrame()
    times = dec["times"].astype(np.float64)
    active, ball, cars = dec["active"], dec["ball"].astype(np.float64), dec["cars"].astype(np.float64)
    T, P, _ = cars.shape
    players = dec["players"]
    team_of = np.array([(-1 if p["team"] is None else p["team"]) for p in players])
    present = ~np.isnan(cars[:, :, 0])
    ball_ok = ~np.isnan(ball[:, 0])
    usable = (active & ball_ok & (present.sum(1) == 4)
              & ((present & (team_of == 0)[None, :]).sum(1) == 2)
              & ((present & (team_of == 1)[None, :]).sum(1) == 2))
    S = sample_indices(times, usable, hz)
    if len(S) == 0:
        return pl.DataFrame()

    # Touch attribution: ball velocity jump, nearest present car within TOUCH_RADIUS.
    hit = np.flatnonzero(dec["ball_hit"] & active & ball_ok)
    touch_times = {p: np.empty(0) for p in range(P)}
    if len(hit):
        d = np.linalg.norm(cars[hit, :, :3] - ball[hit, None, :3], axis=2)
        d = np.where(present[hit], d, np.inf)
        nearest = d.argmin(1)
        ok = d[np.arange(len(hit)), nearest] <= TOUCH_RADIUS
        for p in range(P):
            touch_times[p] = np.sort(times[hit[ok & (nearest == p)]])
    traj_idx = {p: np.flatnonzero(active & ball_ok & present[:, p]) for p in range(P)}

    t_all = times[S]
    rs_all = dec["round_start"][S].astype(np.float64)
    sec_all = dec["seconds_remaining"][S].astype(np.int64)
    parts = []
    for p in range(P):
        team = int(team_of[p])
        if team not in (0, 1):
            continue
        has = present[S, p]
        if not has.any():
            continue
        Sp, tp = S[has], t_all[has]
        n = len(Sp)
        sign = 1.0 if team == 0 else -1.0
        flip = np.array([sign, sign, 1.0])
        ball_loc, ball_vel = ball[Sp, :3] * flip, ball[Sp, 3:] * flip
        me = cars[Sp, p]
        me_loc, me_vel, me_boost = me[:, :3] * flip, me[:, 3:6] * flip, me[:, 6]
        mate_slots = [q for q in range(P) if q != p and team_of[q] == team]
        opp_slots = [q for q in range(P) if team_of[q] == 1 - team]
        mates = cars[Sp][:, mate_slots, :]
        mate = mates[np.arange(n), (~np.isnan(mates[:, :, 0])).argmax(1)]
        opps = cars[Sp][:, opp_slots, :]
        od = np.linalg.norm(opps[:, :, :3] * flip - ball_loc[:, None, :], axis=2)
        od = np.where(~np.isnan(opps[:, :, 0]), od, np.inf)
        order = np.argsort(od, axis=1)
        o1 = opps[np.arange(n), order[:, 0]]
        o2 = opps[np.arange(n), order[:, 1]]
        cols = {
            "replay_id": [dec["replay_id"]] * n, "time": tp,
            "player_id": [players[p]["player_id"]] * n, "player_name": [players[p]["name"]] * n,
            "team": np.full(n, team), "seconds_remaining": [None if s < 0 else int(s) for s in sec_all[has]],
            "time_since_kickoff": tp - rs_all[has],
            "ball_x": ball_loc[:, 0], "ball_y": ball_loc[:, 1], "ball_z": ball_loc[:, 2],
            "ball_vx": ball_vel[:, 0], "ball_vy": ball_vel[:, 1], "ball_vz": ball_vel[:, 2],
            "ball_dist_goal": np.linalg.norm(ball_loc - GOAL_C, axis=1),
            "ball_dist_own_goal": np.linalg.norm(ball_loc - OWN_C, axis=1),
        }
        cols.update(_block("me", me_loc, me_vel, me_boost, ball_loc))
        cols.update(_block("mate", mate[:, :3] * flip, mate[:, 3:6] * flip, mate[:, 6], ball_loc))
        cols.update(_block("opp1", o1[:, :3] * flip, o1[:, 3:6] * flip, o1[:, 6], ball_loc))
        cols.update(_block("opp2", o2[:, :3] * flip, o2[:, 3:6] * flip, o2[:, 6], ball_loc))
        cols["me_closer_than_mate"] = cols["me_dist_ball"] < cols["mate_dist_ball"]
        cols["me_deeper_than_mate"] = me_loc[:, 1] < cols["mate_y"]
        to_ball = ball_loc - me_loc
        dist = np.linalg.norm(to_ball, axis=1)
        dist = np.where(dist == 0, 1.0, dist)
        closing = ((me_vel - ball_vel) * to_ball).sum(1) / dist
        ttb = np.where(closing > 1.0, dist / np.where(closing > 1.0, closing, 1.0), np.nan)
        cols["closing_speed"] = closing
        cols["time_to_ball"] = ttb
        committed_ball = ~np.isnan(ttb) & (ttb < horizon * 0.5)
        committed_y = np.abs(me_vel[:, 1]) * horizon > RETREAT_UU * 1.5
        cols["decision_point"] = ~(committed_ball | committed_y)
        # labels from this player's own future
        t_end = tp + horizon
        idx = traj_idx[p]
        tt = times[idx]
        j = np.searchsorted(tt, t_end, side="right") - 1
        jc = np.clip(j, 0, max(len(tt) - 1, 0))
        valid = (j >= 0) & (tt[jc] > t_end - 0.5) if len(tt) else np.zeros(n, bool)
        fut = cars[idx[jc], p] if len(tt) else np.full((n, 7), np.nan)
        fut_y, fut_boost = fut[:, 1] * sign, fut[:, 6]
        tts = touch_times[p]
        if len(tts):
            k = np.searchsorted(tts, tp, side="right")
            touched = (k < len(tts)) & (tts[np.clip(k, 0, len(tts) - 1)] <= t_end)
        else:
            touched = np.zeros(n, bool)
        dy = fut_y - me_loc[:, 1]
        big = (~np.isnan(me_boost)) & (~np.isnan(fut_boost)) & (fut_boost >= 99) & (me_boost < 85)
        action = np.where(touched, "challenge", np.where(big, "big_pad", np.where(dy < -RETREAT_UU, "retreat",
                          np.where(dy > RETREAT_UU, "advance", "hold"))))
        cols["action"] = action.tolist()
        cols["action_id"] = np.array([ACTIONS.index(a) for a in action])
        frame = pl.DataFrame({k: (v.tolist() if isinstance(v, np.ndarray) and v.dtype == object else v) for k, v in cols.items()})
        parts.append(frame.filter(pl.Series(valid)))
    if not parts:
        return pl.DataFrame()
    return pl.concat(parts, how="vertical_relaxed").sort("time", "team")
