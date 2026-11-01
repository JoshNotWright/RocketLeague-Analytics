"""Frame-derived per-player features from rrrocket network output.

This is a pure function ``replay_json -> list[dict]`` (one dict per player) so it
parallelizes trivially. It walks the network stream once, carrying forward the
last replicated state of every relevant actor, and accumulates features only
while the game state is ``Active`` (kickoff countdowns and goal replays are
excluded).

Feature definitions (v1, chosen before the big parse run; see docs/V2_METHODOLOGY.md):

* ``active_time``          seconds of active play this player was present for
* ``avg_speed``            mean car speed (uu/s) during active play
* ``supersonic_frac``      fraction of active time at >= 2200 uu/s
* ``ground_frac``          fraction of active time with car z <= 50
* ``avg_boost``            mean boost amount (0-100)
* ``low_boost_frac``       fraction of active time with boost < 20
* ``zero_boost_frac``      fraction of active time with boost == 0
* ``behind_ball_frac``     fraction of active time between the ball and own goal
* ``avg_dist_ball``        mean distance to ball (uu)
* ``avg_dist_mate``        mean distance to nearest teammate (uu; null in 1v1)
* ``last_man_frac``        fraction of time this player is the team's deepest defender
* ``last_man_low_boost_frac`` fraction of time deepest defender AND boost < 20
* ``commit_dist_mate``     mean distance to nearest teammate at the moment of this player's touches
* ``touches``              ball touches attributed to this player (per active minute as ``touch_rate``)
* ``challenges``           touches with an opponent within 800 uu of the ball
* ``challenge_win_rate``   share of challenges after which the ball heads toward the opponent half
* ``kickoffs``             rounds where this player made the first touch
* ``kickoff_time_to_ball`` mean seconds from Active to that first touch (kickoffs taken)
* ``kickoff_win_rate``     share of taken kickoffs where the ball headed toward the opponent half 1 s later
* ``demos_inflicted``      demolish events attributed to the attacking car (keyframe re-sends ignored)
* ``demos_taken``          the victim's car is detached from its player at the demolition
* ``big_pads`` / ``small_pads``  boost pickups inferred from boost jumps during active play
  (the replay's pickup attribute does not name the collecting car in current versions)
"""
from __future__ import annotations

import math
from collections import defaultdict

SUPERSONIC = 2200.0
TOUCH_DV = 500.0          # uu/s change in ball velocity between consecutive ball updates
TOUCH_RADIUS = 350.0      # car center within this distance of ball center at a touch
CHALLENGE_RADIUS = 800.0  # opponent within this distance of the ball at the touch
KICKOFF_EVAL_DELAY = 1.0  # seconds after kickoff touch to judge direction


def _vec(v):
    if not v:
        return None
    return (float(v["x"]), float(v["y"]), float(v["z"]))


def _norm(v):
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def _dist(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


class _Player:
    __slots__ = ("pri", "name", "uid", "platform", "team_actor", "team", "acc", "touch_times",
                 "kickoff_times", "kickoff_eval", "challenge_eval", "last_car")

    def __init__(self, pri):
        self.pri = pri
        self.name = None
        self.uid = None
        self.platform = None
        self.team_actor = None
        self.team = None
        self.last_car = None
        self.acc = defaultdict(float)
        self.touch_times = []
        self.kickoff_times = []
        self.kickoff_eval = []    # (eval_time, team_sign)
        self.challenge_eval = []  # (eval_time, team_sign)


def _unique_id(attr):
    remote = attr.get("remote_id")
    if isinstance(remote, dict):
        (platform, value), = remote.items()
        if isinstance(value, dict):
            value = value.get("online_id") or value.get("name") or value.get("id") or str(value)
        return platform, str(value)
    return "unknown", str(remote)


def extract(replay_json: dict, *, replay_id: str | None = None) -> list[dict]:
    objects = replay_json["objects"]
    names = replay_json["names"]
    frames = replay_json["network_frames"]["frames"]

    actor_obj = {}        # actor_id -> object name (kept after deletion for late lookups)
    pris = {}             # PRI actor -> _Player
    car_pri = {}          # car actor -> PRI actor
    car_rb = {}           # car actor -> (loc, vel)
    boost_car = {}        # boost component actor -> car actor
    car_boost = {}        # car actor -> boost 0..100
    ball_actors = set()
    ball_loc = None
    ball_vel = None
    ball_prev_vel = None
    game_actor = None
    state = None
    active_since = None
    first_touch_done = True
    round_touch_pending = False
    active_time = 0.0
    rounds = 0
    team_actor_index = {}  # team actor -> 0/1

    def team_of(player):
        if player.team is None and player.team_actor in team_actor_index:
            player.team = team_actor_index[player.team_actor]
        return player.team

    for frame in frames:
        t = frame["time"]
        dt = frame["delta"] or 0.0
        # ``dt`` is the time elapsed since the previous frame, so it belongs to the
        # state that was current at the start of this frame.
        elapsed_active = state == "Active"
        # Keyframes (about every 10 s) re-announce every live actor and re-send their
        # attributes; such re-sent actors must not be treated as new events.
        resent = {new["actor_id"] for new in frame["new_actors"] if new["actor_id"] in actor_obj}
        for new in frame["new_actors"]:
            aid = new["actor_id"]
            obj = objects[new["object_id"]]
            actor_obj[aid] = obj
            if obj == "TAGame.Default__PRI_TA":
                pris.setdefault(aid, _Player(aid))
            elif obj.startswith("Archetypes.Ball."):
                ball_actors.add(aid)
            elif obj.startswith("Archetypes.GameEvent."):
                game_actor = aid
            elif obj.endswith("Teams.Team0"):
                team_actor_index[aid] = 0
            elif obj.endswith("Teams.Team1"):
                team_actor_index[aid] = 1

        touches_this_frame = []
        for upd in frame["updated_actors"]:
            aid = upd["actor_id"]
            key = objects[upd["object_id"]]
            attr = upd["attribute"]
            if key == "TAGame.RBActor_TA:ReplicatedRBState":
                rb = attr["RigidBody"]
                loc = _vec(rb.get("location"))
                vel = _vec(rb.get("linear_velocity")) or (0.0, 0.0, 0.0)
                if aid in ball_actors:
                    ball_prev_vel = ball_vel
                    ball_loc, ball_vel = loc, vel
                    if ball_prev_vel is not None and _dist(ball_prev_vel, vel) > TOUCH_DV:
                        touches_this_frame.append(loc)
                else:
                    car_rb[aid] = (loc, vel)
            elif key in ("TAGame.CarComponent_Boost_TA:ReplicatedBoostAmount",
                         "TAGame.CarComponent_Boost_TA:ReplicatedBoost"):
                car = boost_car.get(aid)
                if car is not None:
                    # Older builds replicate a byte; builds since mid-2024 replicate a struct.
                    raw = attr["Byte"] if "Byte" in attr else attr["ReplicatedBoost"]["boost_amount"]
                    amount = raw / 2.55
                    previous = car_boost.get(car)
                    car_boost[car] = amount
                    # The pickup attribute does not name the collector in current replay
                    # versions, so pads are inferred from boost jumps during active play.
                    # A big pad always fills to 100; a small pad adds 12.
                    if elapsed_active and previous is not None and amount > previous + 0.5:
                        pri = car_pri.get(car)
                        if pri in pris:
                            big = amount >= 99.5 and previous < 88
                            pris[pri].acc["big_pads" if big else "small_pads"] += 1
            elif key == "TAGame.CarComponent_TA:Vehicle":
                boost_car[aid] = attr["ActiveActor"]["actor"]
            elif key == "Engine.Pawn:PlayerReplicationInfo":
                link = attr["ActiveActor"]
                pri = link.get("actor")
                if link.get("active", True) and pri is not None and pri >= 0:
                    car_pri[aid] = pri
                    if pri in pris:
                        pris[pri].last_car = aid
                elif aid in car_pri and aid not in resent:
                    # The car is detached from its player: that is what a demolition does to
                    # the victim's car (it stays hidden until the respawn, then is deleted).
                    victim = car_pri.pop(aid)
                    if elapsed_active and victim in pris:
                        pris[victim].acc["demos_taken"] += 1
            elif key == "Engine.PlayerReplicationInfo:PlayerName":
                if aid in pris:
                    pris[aid].name = attr["String"]
            elif key == "Engine.PlayerReplicationInfo:UniqueId":
                if aid in pris:
                    pris[aid].platform, pris[aid].uid = _unique_id(attr["UniqueId"])
            elif key == "Engine.PlayerReplicationInfo:Team":
                if aid in pris:
                    pris[aid].team_actor = attr["ActiveActor"]["actor"]
            elif key == "TAGame.GameEvent_TA:ReplicatedStateName":
                idx = attr.get("Int")
                new_state = names[idx] if isinstance(idx, int) and idx < len(names) else str(attr)
                if new_state == "Active" and state != "Active":
                    active_since = t
                    rounds += 1
                    round_touch_pending = True
                state = new_state
            elif key in ("TAGame.Car_TA:ReplicatedDemolishGoalExplosion", "TAGame.Car_TA:ReplicatedDemolish",
                         "TAGame.Car_TA:ReplicatedDemolish_CustomFX", "TAGame.Car_TA:ReplicatedDemolishExtended"):
                if aid in resent:
                    continue  # keyframe re-send of an already counted demolition
                attacker = None
                if "DemolishExtended" in attr:  # builds since December 2024 name the attacker's PRI
                    ext = attr["DemolishExtended"]
                    link = ext.get("attacker_pri") or {}
                    if link.get("active") and link.get("actor", -1) >= 0:
                        attacker = link["actor"]
                    else:
                        car_link = ext.get("attacker") or {}
                        if car_link.get("active") and car_link.get("actor", -1) >= 0:
                            attacker = car_pri.get(car_link["actor"])
                else:
                    demo = attr.get("DemolishFx") or attr.get("Demolish") or {}
                    attacker_car = demo.get("attacker")
                    if attacker_car is None or attacker_car < 0:
                        continue  # goal explosion / custom fx without an attacking car
                    attacker = car_pri.get(attacker_car)
                if attacker in pris:
                    pris[attacker].acc["demos_inflicted"] += 1

        for deleted in frame["deleted_actors"]:
            car_rb.pop(deleted, None)
            car_boost.pop(deleted, None)
            car_pri.pop(deleted, None)
            if deleted in ball_actors:
                ball_actors.discard(deleted)
                ball_loc = ball_vel = ball_prev_vel = None

        if not elapsed_active or ball_loc is None:
            continue
        active_time += dt

        # Resolve current car positions per player.
        cars = []  # (player, car_actor, loc, vel)
        for car, pri in car_pri.items():
            if car in car_rb and pri in pris:
                loc, vel = car_rb[car]
                if loc is not None:
                    cars.append((pris[pri], car, loc, vel))
        if not cars:
            continue
        by_team = defaultdict(list)
        for entry in cars:
            team = team_of(entry[0])
            by_team[team].append(entry)

        for player, car, loc, vel in cars:
            team = team_of(player)
            if team is None:
                continue
            sign = 1.0 if team == 0 else -1.0  # team 0 (blue) attacks +y
            acc = player.acc
            speed = _norm(vel)
            acc["active_time"] += dt
            acc["speed_sum"] += speed * dt
            if speed >= SUPERSONIC:
                acc["supersonic_time"] += dt
            if loc[2] <= 50:
                acc["ground_time"] += dt
            boost = car_boost.get(car)
            if boost is not None:
                acc["boost_time"] += dt
                acc["boost_sum"] += boost * dt
                if boost < 20:
                    acc["low_boost_time"] += dt
                if boost <= 0.5:
                    acc["zero_boost_time"] += dt
            if (ball_loc[1] - loc[1]) * sign > 0:
                acc["behind_ball_time"] += dt
            acc["dist_ball_sum"] += _dist(loc, ball_loc) * dt
            mates = [m for m in by_team[team] if m[1] != car]
            if mates:
                nearest = min(_dist(loc, m[2]) for m in mates)
                acc["mate_time"] += dt
                acc["dist_mate_sum"] += nearest * dt
                # deepest defender: smallest y*sign (closest to own goal)
                if all(loc[1] * sign <= m[2][1] * sign for m in mates):
                    acc["last_man_time"] += dt
                    if boost is not None and boost < 20:
                        acc["last_man_low_boost_time"] += dt

        if touches_this_frame:
            touch_loc = touches_this_frame[-1]
            nearest = min(cars, key=lambda c: _dist(c[2], touch_loc))
            if _dist(nearest[2], touch_loc) <= TOUCH_RADIUS:
                player, car, loc, vel = nearest
                team = team_of(player)
                if team is not None:
                    sign = 1.0 if team == 0 else -1.0
                    acc = player.acc
                    acc["touches"] += 1
                    player.touch_times.append(t)
                    mates = [m for m in by_team[team] if m[1] != car]
                    if mates:
                        acc["commit_mate_n"] += 1
                        acc["commit_mate_sum"] += min(_dist(loc, m[2]) for m in mates)
                    opponents = [c for c in cars if team_of(c[0]) not in (None, team)]
                    if any(_dist(o[2], touch_loc) <= CHALLENGE_RADIUS for o in opponents):
                        acc["challenges"] += 1
                        player.challenge_eval.append((t + KICKOFF_EVAL_DELAY, sign))
                    if round_touch_pending and active_since is not None:
                        round_touch_pending = False
                        acc["kickoffs"] += 1
                        player.kickoff_times.append(t - active_since)
                        player.kickoff_eval.append((t + KICKOFF_EVAL_DELAY, sign))

        # Evaluate pending kickoff / challenge outcomes once their delay has passed.
        if ball_vel is not None:
            for player in pris.values():
                for store, key in ((player.kickoff_eval, "kickoff_wins"), (player.challenge_eval, "challenge_wins")):
                    while store and store[0][0] <= t:
                        _, sign = store.pop(0)
                        if ball_vel[1] * sign > 0:
                            player.acc[key] += 1

    rows = []
    for player in pris.values():
        acc = player.acc
        at = acc.get("active_time", 0.0)
        if at <= 0 or player.uid is None:
            continue

        def ratio(num, den):
            return (num / den) if den else None

        rows.append({
            "replay_id": replay_id,
            "frame_player_name": player.name,
            "frame_platform": player.platform,
            "frame_uid": player.uid,
            "frame_team": team_of(player),
            "active_time": at,
            "avg_speed": ratio(acc.get("speed_sum", 0.0), at),
            "supersonic_frac": ratio(acc.get("supersonic_time", 0.0), at),
            "ground_frac": ratio(acc.get("ground_time", 0.0), at),
            "avg_boost": ratio(acc.get("boost_sum", 0.0), acc.get("boost_time", 0.0)),
            "low_boost_frac": ratio(acc.get("low_boost_time", 0.0), acc.get("boost_time", 0.0)),
            "zero_boost_frac": ratio(acc.get("zero_boost_time", 0.0), acc.get("boost_time", 0.0)),
            "behind_ball_frac": ratio(acc.get("behind_ball_time", 0.0), at),
            "avg_dist_ball": ratio(acc.get("dist_ball_sum", 0.0), at),
            "avg_dist_mate": ratio(acc.get("dist_mate_sum", 0.0), acc.get("mate_time", 0.0)),
            "last_man_frac": ratio(acc.get("last_man_time", 0.0), acc.get("mate_time", 0.0)),
            "last_man_low_boost_frac": ratio(acc.get("last_man_low_boost_time", 0.0), acc.get("mate_time", 0.0)),
            "commit_dist_mate": ratio(acc.get("commit_mate_sum", 0.0), acc.get("commit_mate_n", 0.0)),
            "touches": int(acc.get("touches", 0)),
            "touch_rate": acc.get("touches", 0) / (at / 60.0),
            "challenges": int(acc.get("challenges", 0)),
            "challenge_rate": acc.get("challenges", 0) / (at / 60.0),
            "challenge_win_rate": ratio(acc.get("challenge_wins", 0.0), acc.get("challenges", 0.0)),
            "kickoffs": int(acc.get("kickoffs", 0)),
            "kickoff_time_to_ball": (sum(player.kickoff_times) / len(player.kickoff_times)) if player.kickoff_times else None,
            "kickoff_win_rate": ratio(acc.get("kickoff_wins", 0.0), acc.get("kickoffs", 0.0)),
            "demos_inflicted": int(acc.get("demos_inflicted", 0)),
            "demos_taken": int(acc.get("demos_taken", 0)),
            "big_pads": int(acc.get("big_pads", 0)),
            "small_pads": int(acc.get("small_pads", 0)),
            "match_active_time": active_time,
            "match_rounds": rounds,
        })
    return rows
