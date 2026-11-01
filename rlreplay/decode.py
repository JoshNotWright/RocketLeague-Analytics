"""Decode a replay once into compact arrays, so every downstream product is vectorized.

Per replay (``<id>.npz``, ~0.5 MB compressed):

    times[T], delta[T], active[T] (the dt interval belonged to Active play), round_start[T],
    seconds_remaining[T], ball[T, 6] (loc xyz, vel xyz; NaN when absent), ball_hit[T],
    cars[T, P, 7] (loc xyz, vel xyz, boost; NaN when the car is absent),
    players: JSON list of {slot, pri, player_id, name, platform, team},
    goals: JSON list of {time, team, scorer}, demos: JSON list of {time, attacker_slot, victim_slot},
    header: JSON (team scores, team size, map, date, build version, num frames).

Walking the network stream is still one Python pass (``stream.iter_snapshots``), but it
happens once per replay instead of once per product, and label changes no longer need it.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .events import PLATFORMS
from .header import header_summary, parse
from .stream import iter_snapshots


def _player_id(player) -> str | None:
    if player.uid is None:
        return None
    return f"{PLATFORMS.get(player.platform, (player.platform or 'unknown').lower())}:{player.uid}"


def decode_replay(path, *, replay_id: str | None = None) -> dict:
    return decode_json(parse(path, network=True), replay_id=replay_id or Path(path).stem)


def decode_json(replay_json: dict, *, replay_id: str) -> dict:
    head = header_summary(replay_json)
    frames = replay_json["network_frames"]["frames"]
    T = len(frames)
    times = np.zeros(T, np.float64); delta = np.zeros(T, np.float64)
    active = np.zeros(T, bool); ball_hit = np.zeros(T, bool)
    round_start = np.full(T, np.nan, np.float64); seconds = np.full(T, -1, np.int16)
    ball = np.full((T, 6), np.nan, np.float32)
    slots: dict[int, int] = {}
    players: list[dict] = []
    car_chunks = []  # filled after we know P; store sparse updates first
    per_frame_cars = []
    demos = []
    for snap in iter_snapshots(replay_json):
        i = snap.index
        times[i], delta[i], active[i] = snap.time, snap.dt, snap.elapsed_active
        if snap.round_start is not None:
            round_start[i] = snap.round_start
        if snap.seconds_remaining is not None:
            seconds[i] = snap.seconds_remaining
        if snap.ball_loc is not None:
            ball[i, :3] = snap.ball_loc
            ball[i, 3:] = snap.ball_vel or (0.0, 0.0, 0.0)
        ball_hit[i] = snap.ball_hit
        entries = []
        for pri, car in snap.cars.items():
            if pri not in slots:
                slots[pri] = len(slots)
                players.append({"slot": slots[pri], "pri": pri, "player_id": _player_id(car.player),
                                "name": car.player.name, "platform": car.player.platform, "team": car.player.team})
            entries.append((slots[pri], car.loc, car.vel, car.boost))
        per_frame_cars.append(entries)
        for attacker, victim in snap.demos:
            demos.append({"time": float(snap.time), "attacker_slot": slots.get(attacker), "victim_slot": slots.get(victim)})
    # Teams can resolve late; refresh from the last snapshot's player objects is not available here,
    # so fall back to the last known team per pri by scanning once more cheaply through the entries.
    P = len(slots)
    cars = np.full((T, P, 7), np.nan, np.float32)
    for i, entries in enumerate(per_frame_cars):
        for slot, loc, vel, boost in entries:
            cars[i, slot, :3] = loc
            cars[i, slot, 3:6] = vel
            cars[i, slot, 6] = np.nan if boost is None else boost
    goals = []
    for g in head["goals"]:
        f = g["frame"]
        if f is not None and f < T:
            goals.append({"time": float(times[f]), "team": g["team"], "scorer": g["player_name"]})
    return {"replay_id": replay_id, "times": times, "delta": delta, "active": active,
            "round_start": round_start, "seconds_remaining": seconds, "ball": ball, "ball_hit": ball_hit,
            "cars": cars, "players": players, "goals": goals, "demos": demos,
            "header": {k: v for k, v in head.items() if k not in ("goals", "players")}}


def save(decoded: dict, path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.npz")
    np.savez_compressed(tmp, times=decoded["times"], delta=decoded["delta"], active=decoded["active"],
                        round_start=decoded["round_start"], seconds_remaining=decoded["seconds_remaining"],
                        ball=decoded["ball"], ball_hit=decoded["ball_hit"], cars=decoded["cars"],
                        meta=np.frombuffer(json.dumps({"replay_id": decoded["replay_id"], "players": decoded["players"],
                                                       "goals": decoded["goals"], "demos": decoded["demos"],
                                                       "header": decoded["header"]}).encode(), dtype=np.uint8))
    tmp.replace(path)


def load(path) -> dict:
    with np.load(path) as z:
        meta = json.loads(bytes(z["meta"]).decode())
        out = {k: z[k] for k in ("times", "delta", "active", "round_start", "seconds_remaining", "ball", "ball_hit", "cars")}
    out.update(meta)
    return out


def decode_one(path: str, out_dir: str):
    """Worker: decode a replay to <out_dir>/<id>.npz unless it already exists."""
    replay_id = Path(path).stem
    target = Path(out_dir) / f"{replay_id}.npz"
    if target.exists():
        return replay_id, "cached", None
    try:
        save(decode_replay(path, replay_id=replay_id), target)
        return replay_id, "ok", None
    except Exception as exc:  # noqa: BLE001
        return replay_id, "failed", f"{type(exc).__name__}: {str(exc)[:200]}"
