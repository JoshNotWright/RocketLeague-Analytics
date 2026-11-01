"""Walk an rrrocket network stream and yield one game-state snapshot per frame.

This is the shared decoder for event-level analytics. It carries forward the last
replicated value of every relevant actor and handles the replay-format drift seen in the
GC+ dataset (byte vs struct boost, three demolish attribute names, keyframe re-sends).
``frames.py`` predates it and keeps its own walker; both are validated against ballchasing.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Player:
    pri: int
    name: str | None = None
    platform: str | None = None
    uid: str | None = None
    team_actor: int | None = None
    team: int | None = None


@dataclass
class Car:
    player: Player
    loc: tuple
    vel: tuple
    boost: float | None


@dataclass
class Snapshot:
    index: int
    time: float
    dt: float
    state: str | None          # Countdown / Active / PostGoalScored / ...
    elapsed_active: bool       # the dt interval belonged to Active play
    round_start: float | None  # time the current Active phase began
    seconds_remaining: int | None
    ball_loc: tuple | None
    ball_vel: tuple | None
    ball_hit: bool             # ball velocity jumped since its previous update (a touch candidate)
    cars: dict = field(default_factory=dict)  # pri actor -> Car
    demos: list = field(default_factory=list)  # (attacker pri, victim pri or None)


def _vec(v):
    return (float(v["x"]), float(v["y"]), float(v["z"])) if v else None


def _unique_id(attr):
    remote = attr.get("remote_id")
    if isinstance(remote, dict):
        (platform, value), = remote.items()
        if isinstance(value, dict):
            value = value.get("online_id") or value.get("name") or value.get("id") or str(value)
        return platform, str(value)
    return "unknown", str(remote)


TOUCH_DV = 500.0


def iter_snapshots(replay_json: dict):
    objects = replay_json["objects"]
    names = replay_json["names"]
    frames = replay_json["network_frames"]["frames"]

    actor_obj: dict[int, str] = {}
    players: dict[int, Player] = {}
    car_pri: dict[int, int] = {}
    car_rb: dict[int, tuple] = {}
    boost_car: dict[int, int] = {}
    car_boost: dict[int, float] = {}
    team_index: dict[int, int] = {}
    ball_actors: set[int] = set()
    ball_loc = ball_vel = None
    state = None
    round_start = None
    seconds_remaining = None

    for index, frame in enumerate(frames):
        t = frame["time"]
        dt = frame["delta"] or 0.0
        elapsed_active = state == "Active"
        resent = {n["actor_id"] for n in frame["new_actors"] if n["actor_id"] in actor_obj}
        for new in frame["new_actors"]:
            aid, obj = new["actor_id"], objects[new["object_id"]]
            actor_obj[aid] = obj
            if obj == "TAGame.Default__PRI_TA":
                players.setdefault(aid, Player(aid))
            elif obj.startswith("Archetypes.Ball."):
                ball_actors.add(aid)
            elif obj.endswith("Teams.Team0"):
                team_index[aid] = 0
            elif obj.endswith("Teams.Team1"):
                team_index[aid] = 1

        ball_hit = False
        demos = []
        for upd in frame["updated_actors"]:
            aid, key, attr = upd["actor_id"], objects[upd["object_id"]], upd["attribute"]
            if key == "TAGame.RBActor_TA:ReplicatedRBState":
                rb = attr["RigidBody"]
                loc = _vec(rb.get("location"))
                vel = _vec(rb.get("linear_velocity")) or (0.0, 0.0, 0.0)
                if aid in ball_actors:
                    if ball_vel is not None and sum((a - b) ** 2 for a, b in zip(ball_vel, vel)) > TOUCH_DV ** 2:
                        ball_hit = True
                    ball_loc, ball_vel = loc, vel
                else:
                    car_rb[aid] = (loc, vel)
            elif key in ("TAGame.CarComponent_Boost_TA:ReplicatedBoostAmount", "TAGame.CarComponent_Boost_TA:ReplicatedBoost"):
                car = boost_car.get(aid)
                if car is not None:
                    raw = attr["Byte"] if "Byte" in attr else attr["ReplicatedBoost"]["boost_amount"]
                    car_boost[car] = raw / 2.55
            elif key == "TAGame.CarComponent_TA:Vehicle":
                boost_car[aid] = attr["ActiveActor"]["actor"]
            elif key == "Engine.Pawn:PlayerReplicationInfo":
                link = attr["ActiveActor"]
                pri = link.get("actor")
                if link.get("active", True) and pri is not None and pri >= 0:
                    car_pri[aid] = pri
                elif aid in car_pri and aid not in resent:
                    victim = car_pri.pop(aid)
                    if elapsed_active:
                        demos.append((None, victim))
            elif key == "Engine.PlayerReplicationInfo:PlayerName":
                if aid in players:
                    players[aid].name = attr["String"]
            elif key == "Engine.PlayerReplicationInfo:UniqueId":
                if aid in players:
                    players[aid].platform, players[aid].uid = _unique_id(attr["UniqueId"])
            elif key == "Engine.PlayerReplicationInfo:Team":
                if aid in players:
                    players[aid].team_actor = attr["ActiveActor"]["actor"]
            elif key == "TAGame.GameEvent_TA:ReplicatedStateName":
                idx = attr.get("Int")
                new_state = names[idx] if isinstance(idx, int) and idx < len(names) else str(attr)
                if new_state == "Active" and state != "Active":
                    round_start = t
                state = new_state
            elif key == "TAGame.GameEvent_Soccar_TA:SecondsRemaining":
                seconds_remaining = attr.get("Int")
            elif key in ("TAGame.Car_TA:ReplicatedDemolishGoalExplosion", "TAGame.Car_TA:ReplicatedDemolish",
                         "TAGame.Car_TA:ReplicatedDemolish_CustomFX", "TAGame.Car_TA:ReplicatedDemolishExtended"):
                if aid in resent:
                    continue
                attacker = None
                if "DemolishExtended" in attr:
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
                    if attacker_car is not None and attacker_car >= 0:
                        attacker = car_pri.get(attacker_car)
                if attacker in players:
                    demos.append((attacker, None))

        for deleted in frame["deleted_actors"]:
            car_rb.pop(deleted, None)
            car_boost.pop(deleted, None)
            car_pri.pop(deleted, None)
            if deleted in ball_actors:
                ball_actors.discard(deleted)
                ball_loc = ball_vel = None

        cars = {}
        for car, pri in car_pri.items():
            player = players.get(pri)
            if player is None or car not in car_rb or car_rb[car][0] is None:
                continue
            if player.team is None and player.team_actor in team_index:
                player.team = team_index[player.team_actor]
            cars[pri] = Car(player, car_rb[car][0], car_rb[car][1], car_boost.get(car))

        yield Snapshot(index, t, dt, state, elapsed_active, round_start, seconds_remaining,
                       ball_loc, ball_vel, ball_hit, cars, demos)


def roster(replay_json: dict) -> dict[int, Player]:
    """Final player records (ids, names, teams) after one pass over the stream."""
    last = None
    for snap in iter_snapshots(replay_json):
        last = snap
    if last is None:
        return {}
    return {pri: car.player for pri, car in last.cars.items()}
