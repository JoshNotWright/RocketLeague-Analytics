"""Decode a replay once into compact arrays, so every downstream product is vectorized.

Narrow layout, per replay (``<id>.npz``, ~0.5 MB compressed), written by both decoders:

    times[T], delta[T], active[T] (the dt interval belonged to Active play), round_start[T],
    seconds_remaining[T], ball[T, 6] (loc xyz, vel xyz; NaN when absent), ball_hit[T],
    cars[T, P, 7] (loc xyz, vel xyz, boost; NaN when the car is absent),
    players: JSON list of {slot, pri, player_id, name, platform, team},
    goals: JSON list of {time, team, scorer}, demos: JSON list of {time, attacker_slot, victim_slot},
    header: JSON (team scores, team size, map, date, build version, num frames).

Wide layout, appended after the narrow arrays by the Rust decoder only (tools/rldecode.exe;
the Python fallback below writes the narrow set and nothing more). Raw replays are not kept
long term, so the wide set is meant to be a superset of what any model will need:

    cars_rot[T, P, 4] float16 (unit quaternion x y z w: which way each car faces),
    cars_angvel[T, P, 3] float16 (rad/s), ball_angvel[T, 3] float16 (spin; 0 when the rigid
    body carries none, like velocity), cars_ctrl[T, P, 8] int16 (throttle, steer as the raw
    0..255 byte, handbrake, on_ground, boost_active, jump_active, double_jump_active,
    dodge_active as 0/1; -1 until first replicated, then carried forward like boost),
    overtime[T] bool. The inputs are kept because they are the player's actual actions, which
    a sequence model trains on; ball orientation is the one decoded field not written. The
    compact dtypes keep the wide file well under twice the narrow one.
    Meta additions: pickups (list of {time, slot, pad, big, pad_name, car_loc}; pad and big
    are null because pad actors are level-placed and carry no spawn location, pad_name is
    the stable per-map pad id such as VehiclePickup_Boost_TA_27 and car_loc the collecting
    car's position), per player body, camera {fov, height, angle, distance, stiffness,
    swivel, transition}, match_score/goals/assists/saves/shots (null when never seen), and
    header.player_stats (the header PlayerStats rows as plain objects).

``load`` returns every array present, so the wide keys are simply absent for a narrow file.
Walking the network stream is still one Python pass (``stream.iter_snapshots``) on the
fallback path, but it happens once per replay instead of once per product.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import numpy as np

from .events import PLATFORMS
from .header import header_summary, parse
from .stream import iter_snapshots

_ROOT = Path(__file__).resolve().parent.parent
RLDECODE_EXE = _ROOT / "tools" / "rldecode.exe"
# Module-level switch for the Rust decoder (rust/rldecode, built to tools/rldecode.exe). It
# writes the same .npz as `save` about 10x faster; `RLREPLAY_NO_RUST=1` forces the Python path.
USE_RUST = True


def use_rust() -> bool:
    return USE_RUST and not os.environ.get("RLREPLAY_NO_RUST") and RLDECODE_EXE.exists()


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


NARROW_ARRAYS = ("times", "delta", "active", "round_start", "seconds_remaining", "ball", "ball_hit", "cars")
WIDE_ARRAYS = ("ball_angvel", "cars_rot", "cars_angvel", "cars_ctrl", "overtime")


def load(path) -> dict:
    with np.load(path) as z:
        meta = json.loads(bytes(z["meta"]).decode())
        out = {k: z[k] for k in NARROW_ARRAYS}
        present = set(z.files)
        out.update({k: z[k] for k in WIDE_ARRAYS if k in present})
    out.update(meta)
    return out


def decode_one(path: str, out_dir: str):
    """Worker: decode a replay to <out_dir>/<id>.npz unless it already exists."""
    replay_id = Path(path).stem
    target = Path(out_dir) / f"{replay_id}.npz"
    if target.exists():
        return replay_id, "cached", None
    if use_rust():
        completed = subprocess.run([str(RLDECODE_EXE), str(path), str(out_dir), "--replay-id", replay_id],
                                   capture_output=True, timeout=300)
        if completed.returncode == 0 and target.exists():
            return replay_id, "ok", None
        # Fall through to the Python path so a Rust-side failure never loses a replay that
        # rrrocket can still parse; the Python error (if any) is the one reported.
    return _decode_one_python(path, out_dir, replay_id)


def _decode_one_python(path: str, out_dir: str, replay_id: str):
    target = Path(out_dir) / f"{replay_id}.npz"
    try:
        save(decode_replay(path, replay_id=replay_id), target)
        return replay_id, "ok", None
    except Exception as exc:  # noqa: BLE001
        return replay_id, "failed", f"{type(exc).__name__}: {str(exc)[:200]}"


def decode_batch(paths, out_dir: str):
    """Decode many replays in one rldecode process (saves ~70 ms of process start-up each on
    Windows). Yields (replay_id, status, error) like `decode_one`; replays the Rust decoder
    rejects are retried on the Python path. Without the binary this is just a loop over
    `decode_one`."""
    paths = [str(p) for p in paths]
    pending = []
    for path in paths:
        replay_id = Path(path).stem
        if (Path(out_dir) / f"{replay_id}.npz").exists():
            yield replay_id, "cached", None
        else:
            pending.append((path, replay_id))
    if not pending:
        return
    if not use_rust():
        for path, replay_id in pending:
            yield _decode_one_python(path, out_dir, replay_id)
        return
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    by_id = dict((rid, p) for p, rid in pending)
    proc = subprocess.run([str(RLDECODE_EXE), "--batch", str(out_dir)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          input="".join(f"{p}\t{rid}\n" for p, rid in pending))
    seen = set()
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2 or parts[0] not in ("ok", "failed"):
            continue
        status, replay_id = parts[0], parts[1]
        seen.add(replay_id)
        if status == "ok" and (Path(out_dir) / f"{replay_id}.npz").exists():
            yield replay_id, "ok", None
        else:
            yield _decode_one_python(by_id[replay_id], out_dir, replay_id)
    for replay_id, path in by_id.items():  # the process died before reporting these
        if replay_id not in seen:
            yield _decode_one_python(path, out_dir, replay_id)
