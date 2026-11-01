"""Touch events with game-state features and outcome labels, for expected-goals models.

One row per ball touch during active play. Every geometric feature is expressed in the
touching team's attacking frame: the opponent goal is at (0, +5120), own goal at (0, -5120),
so one model serves both teams. Labels look forward in time only.
"""
from __future__ import annotations

import math

from .stream import iter_snapshots

TOUCH_RADIUS = 350.0
GOAL_Y = 5120.0
GOAL_HALF_WIDTH = 893.0
GOAL_HEIGHT = 643.0
BALL_RADIUS = 92.75
GRAVITY = 650.0
SHOT_HORIZON = 3.0
PLATFORMS = {"Steam": "steam", "Epic": "epic", "PlayStation": "ps4", "PsyNet": "psynet", "Xbox": "xbox",
             "Switch": "switch"}


def player_key(player) -> str | None:
    if player.uid is None:
        return None
    platform = PLATFORMS.get(player.platform, (player.platform or "unknown").lower())
    return f"{platform}:{player.uid}"


def _flip(v, sign):
    return (v[0] * sign, v[1] * sign, v[2])


def _norm(v):
    return math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)


def _dist(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


BOUNCE_RESTITUTION = 0.6


def shot_projection(loc, vel):
    """Where (x, z) the ball crosses the opponent goal plane, and when; None if it won't within the horizon.

    Ballistic flight under gravity with at most one ground bounce (vertical restitution 0.6).
    Walls, posts and spin are ignored. Chosen before modelling; against ballchasing's shot
    counts this definition reaches corr 0.78 per player-game and flags 89% of scoring touches.
    """
    vy = vel[1]
    if vy <= 1e-6:
        return None
    t = (GOAL_Y - BALL_RADIUS - loc[1]) / vy
    if t < 0 or t > SHOT_HORIZON:
        return None
    x = loc[0] + vel[0] * t
    z0, vz = loc[2] - BALL_RADIUS, vel[2]  # height of the ball's underside
    z = z0 + vz * t - 0.5 * GRAVITY * t * t
    if z < 0:
        # time of the ground hit: z0 + vz*t1 - g/2*t1^2 = 0, take the positive root
        disc = vz * vz + 2 * GRAVITY * z0
        t1 = (vz + math.sqrt(disc)) / GRAVITY if disc >= 0 else None
        if t1 is not None and 0 <= t1 < t:
            vz1 = -(vz - GRAVITY * t1) * BOUNCE_RESTITUTION
            rem = t - t1
            z = vz1 * rem - 0.5 * GRAVITY * rem * rem
    return x, z + BALL_RADIUS, t


def is_shot(loc, vel) -> bool:
    proj = shot_projection(loc, vel)
    if proj is None:
        return False
    x, z, _ = proj
    return abs(x) <= GOAL_HALF_WIDTH + BALL_RADIUS and -BALL_RADIUS <= z <= GOAL_HEIGHT + BALL_RADIUS


def goal_times(replay_json: dict) -> list[tuple[float, int, str | None]]:
    """(time, scoring team, scorer name) from the header's goal list."""
    frames = replay_json["network_frames"]["frames"]
    out = []
    for goal in replay_json["properties"].get("Goals") or []:
        frame = goal.get("frame")
        if frame is None or frame >= len(frames):
            continue
        out.append((frames[frame]["time"], goal.get("PlayerTeam"), goal.get("PlayerName")))
    return sorted(out)


def extract_touches(replay_json: dict, *, replay_id: str | None = None) -> list[dict]:
    goals = goal_times(replay_json)
    team_size = replay_json["properties"].get("TeamSize")
    touches = []
    prev_ball_vel = None
    score = {0: 0, 1: 0}
    goal_cursor = 0
    players = {}

    for snap in iter_snapshots(replay_json):
        for pri, car in snap.cars.items():
            players[pri] = car.player
        while goal_cursor < len(goals) and goals[goal_cursor][0] <= snap.time:
            team = goals[goal_cursor][1]
            if team in score:
                score[team] += 1
            goal_cursor += 1
        if not (snap.elapsed_active and snap.ball_hit and snap.ball_loc is not None and snap.cars):
            prev_ball_vel = snap.ball_vel if snap.ball_vel is not None else prev_ball_vel
            continue
        pri, car = min(snap.cars.items(), key=lambda kv: _dist(kv[1].loc, snap.ball_loc))
        if _dist(car.loc, snap.ball_loc) > TOUCH_RADIUS or car.player.team is None:
            prev_ball_vel = snap.ball_vel
            continue
        team = car.player.team
        sign = 1.0 if team == 0 else -1.0
        ball = _flip(snap.ball_loc, sign)
        pre = _flip(prev_ball_vel or (0.0, 0.0, 0.0), sign)
        post = _flip(snap.ball_vel or (0.0, 0.0, 0.0), sign)
        me = _flip(car.loc, sign)
        me_vel = _flip(car.vel, sign)
        mates = [c for p, c in snap.cars.items() if p != pri and c.player.team == team]
        opps = [c for c in snap.cars.values() if c.player.team not in (None, team)]
        mate_locs = [_flip(c.loc, sign) for c in mates]
        opp_locs = [_flip(c.loc, sign) for c in opps]
        goal_center = (0.0, GOAL_Y, 300.0)
        own_goal = (0.0, -GOAL_Y, 300.0)
        to_goal = (goal_center[0] - ball[0], goal_center[1] - ball[1], 0.0)
        post_speed = _norm(post)
        toward = ((post[0] * to_goal[0] + post[1] * to_goal[1]) / (math.hypot(to_goal[0], to_goal[1]) or 1.0))
        angle = math.degrees(math.atan2(abs(post[0] * to_goal[1] - post[1] * to_goal[0]),
                                        post[0] * to_goal[0] + post[1] * to_goal[1])) if post_speed > 1 else None
        left = math.atan2(-GOAL_HALF_WIDTH - ball[0], GOAL_Y - ball[1])
        right = math.atan2(GOAL_HALF_WIDTH - ball[0], GOAL_Y - ball[1])
        proj = shot_projection(ball, post)
        row = {
            "replay_id": replay_id, "frame": snap.index, "time": snap.time,
            "player_id": player_key(car.player), "player_name": car.player.name, "team": team,
            "team_size": team_size, "seconds_remaining": snap.seconds_remaining,
            "time_since_kickoff": (snap.time - snap.round_start) if snap.round_start is not None else None,
            "score_diff": score[team] - score[1 - team],
            # ball state (attacking frame)
            "ball_x": ball[0], "ball_y": ball[1], "ball_z": ball[2],
            "pre_vx": pre[0], "pre_vy": pre[1], "pre_vz": pre[2], "pre_speed": _norm(pre),
            "post_vx": post[0], "post_vy": post[1], "post_vz": post[2], "post_speed": post_speed,
            "dist_goal": _dist(ball, goal_center), "dist_own_goal": _dist(ball, own_goal),
            "goal_angle_width": math.degrees(right - left),
            "post_toward_goal": toward, "post_angle_off_goal": angle,
            "proj_x": proj[0] if proj else None, "proj_z": proj[1] if proj else None,
            "proj_t": proj[2] if proj else None, "shot": is_shot(ball, post),
            # toucher
            "me_z": me[2], "me_speed": _norm(me_vel), "me_boost": car.boost,
            "me_on_ground": me[2] <= 50, "me_dist_own_goal": _dist(me, own_goal),
            # teammates
            "n_mates": len(mates),
            "mate_min_dist": min((_dist(me, m) for m in mate_locs), default=None),
            "mates_ahead": sum(1 for m in mate_locs if m[1] > ball[1]),
            "mate_min_dist_own_goal": min((_dist(m, own_goal) for m in mate_locs), default=None),
            # opponents
            "n_opps": len(opps),
            "opp_min_dist_ball": min((_dist(o, ball) for o in opp_locs), default=None),
            "opp_min_dist_goal": min((_dist(o, goal_center) for o in opp_locs), default=None),
            "opps_between": sum(1 for o in opp_locs if ball[1] < o[1] <= GOAL_Y and abs(o[0]) < 2000),
            "opps_behind_ball": sum(1 for o in opp_locs if o[1] > ball[1]),
            "opp_nearest_boost": min(((_dist(_flip(o.loc, sign), ball), o.boost) for o in opps), default=(None, None))[1],
            # who the defenders were, for save credit and value attribution downstream
            "opp_nearest_id": min(((_dist(_flip(o.loc, sign), ball), player_key(o.player)) for o in opps), default=(None, None))[1],
            "opp_goalie_id": min(((_dist(_flip(o.loc, sign), goal_center), player_key(o.player)) for o in opps), default=(None, None))[1],
            "mate_nearest_id": min(((_dist(_flip(m.loc, sign), me), player_key(m.player)) for m in mates), default=(None, None))[1],
        }
        touches.append(row)
        prev_ball_vel = snap.ball_vel

    # Forward-looking labels.
    for i, row in enumerate(touches):
        t, team = row["time"], row["team"]
        nxt = touches[i + 1] if i + 1 < len(touches) else None
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

    # Goal attribution follows the replay header: the scoring touch is the credited scorer's
    # last touch before the goal (within 10 s). Any later touch by the conceding team before
    # the ball crossed the line is a failed save, not a change of possession.
    for gt, gteam, gname in goals:
        candidates = [i for i, r in enumerate(touches)
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
