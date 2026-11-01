"""Run rrrocket and return parsed replay JSON (header only, or header + network frames)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def rrrocket_path() -> str:
    """Locate rrrocket: $RRROCKET, then tools/rrrocket(.exe), then PATH."""
    candidates = [os.environ.get("RRROCKET"), _ROOT / "tools" / "rrrocket.exe",
                  _ROOT / "tools" / "rrrocket", shutil.which("rrrocket")]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    raise FileNotFoundError("rrrocket not found; set RRROCKET or place it in tools/")


def parse(path, *, network: bool = False, timeout: int = 300) -> dict:
    args = [rrrocket_path()]
    if network:
        args.append("-n")
    args.append(str(path))
    completed = subprocess.run(args, capture_output=True, timeout=timeout)
    if completed.returncode != 0:
        raise RuntimeError(f"rrrocket failed on {path}: {completed.stderr.decode(errors='replace')[:300]}")
    return json.loads(completed.stdout)


def header_summary(replay_json: dict) -> dict:
    """Flatten the header properties that matter for joins and sanity checks."""
    props = replay_json["properties"]
    players = []
    for stat in props.get("PlayerStats") or []:
        platform = stat.get("Platform")
        if isinstance(platform, dict):
            platform = platform.get("value")
        players.append({
            "name": stat.get("Name"),
            "team": stat.get("Team"),
            "online_id": str(stat["OnlineID"]) if stat.get("OnlineID") is not None else None,
            "platform": platform,
            "score": stat.get("Score"), "goals": stat.get("Goals"), "assists": stat.get("Assists"),
            "saves": stat.get("Saves"), "shots": stat.get("Shots"), "bot": stat.get("bBot"),
        })
    return {
        "replay_id_header": props.get("Id"),
        "match_guid": props.get("MatchGuid"),
        "team_size": props.get("TeamSize"),
        "team0_score": props.get("Team0Score") or 0,
        "team1_score": props.get("Team1Score") or 0,
        "num_frames": props.get("NumFrames"),
        "record_fps": props.get("RecordFPS"),
        "map_name": props.get("MapName"),
        "date": props.get("Date"),
        "build_version": props.get("BuildVersion"),
        "replay_version": (replay_json.get("major_version"), replay_json.get("minor_version"),
                           replay_json.get("net_version")),
        "goals": [{"frame": g.get("frame"), "player_name": g.get("PlayerName"), "team": g.get("PlayerTeam")}
                  for g in props.get("Goals") or []],
        "players": players,
    }
