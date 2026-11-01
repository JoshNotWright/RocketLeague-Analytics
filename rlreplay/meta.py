"""Load the dataset's line-delimited metadata.json into flat per-player-game rows.

Every row is one player in one match, with the ballchasing summary stats and the
player's rank at the time of the match. Nothing here touches replay files.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

CORE = ("shots", "shots_against", "goals", "goals_against", "saves", "assists", "score")
BOOST = ("bpm", "bcpm", "avg_amount", "amount_collected", "amount_stolen", "amount_collected_big",
         "amount_stolen_big", "amount_collected_small", "amount_stolen_small", "count_collected_big",
         "count_stolen_big", "count_collected_small", "count_stolen_small", "amount_overfill",
         "amount_overfill_stolen", "amount_used_while_supersonic", "time_zero_boost", "percent_zero_boost",
         "time_full_boost", "percent_full_boost", "percent_boost_0_25", "percent_boost_25_50",
         "percent_boost_50_75", "percent_boost_75_100")
MOVEMENT = ("avg_speed", "total_distance", "time_supersonic_speed", "time_boost_speed", "time_slow_speed",
            "time_ground", "time_low_air", "time_high_air", "time_powerslide", "count_powerslide",
            "avg_powerslide_duration", "avg_speed_percentage", "percent_slow_speed", "percent_boost_speed",
            "percent_supersonic_speed", "percent_ground", "percent_low_air", "percent_high_air")
POSITIONING = ("avg_distance_to_ball", "avg_distance_to_ball_possession", "avg_distance_to_ball_no_possession",
               "avg_distance_to_mates", "time_defensive_third", "time_neutral_third", "time_offensive_third",
               "time_defensive_half", "time_offensive_half", "time_behind_ball", "time_infront_ball",
               "time_most_back", "time_most_forward", "time_closest_to_ball", "time_farthest_from_ball",
               "percent_defensive_third", "percent_offensive_third", "percent_neutral_third",
               "percent_defensive_half", "percent_offensive_half", "percent_behind_ball", "percent_infront_ball",
               "percent_most_back", "percent_most_forward", "percent_closest_to_ball", "percent_farthest_from_ball")
DEMO = ("inflicted", "taken")
STAT_GROUPS = {"core": CORE, "boost": BOOST, "movement": MOVEMENT, "positioning": POSITIONING, "demo": DEMO}
SUMMARY_COLUMNS = [f"{group}_{field}" for group, fields in STAT_GROUPS.items() for field in fields]


def rank_points(rank: dict | None) -> int | None:
    """Monotone rank scale with 4 divisions per tier (GC1 div1 = 76 ... SSL = 88).

    Supersonic Legend (tier 22) has no divisions and is placed one step above GC3 div 4.
    """
    if not rank or rank.get("tier") is None:
        return None
    tier = int(rank["tier"])
    division = rank.get("division") or 1
    return tier * 4 + (int(division) - 1)


def _utc(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def iter_records(path, *, on_bad_line=None):
    """Yield one dict per line. A malformed line (e.g. a truncated download) is reported, not raised."""
    import sys

    bad = 0
    with open(path, "r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                if on_bad_line:
                    on_bad_line(number, line)
    if bad:
        print(f"warning: skipped {bad} malformed line(s) in {path}", file=sys.stderr)


def player_rows(record: dict) -> list[dict]:
    data = record["data"]
    played_at = _utc(data.get("date")) or _utc(data.get("created"))
    team_goals = {color: (((data.get(color) or {}).get("stats") or {}).get("core") or {}).get("goals")
                  for color in ("blue", "orange")}
    rows = []
    for color in ("blue", "orange"):
        team = data.get(color) or {}
        other = "orange" if color == "blue" else "blue"
        for slot, player in enumerate(team.get("players") or []):
            pid = player.get("id") or {}
            stats = player.get("stats") or {}
            rank = player.get("rank") or {}
            row = {
                "replay_id": record["id"],
                "match_guid": data.get("match_guid"),
                "played_at": played_at,
                "season": data.get("season"),
                "playlist": data.get("playlist_id"),
                "team_size": data.get("team_size"),
                "match_type": data.get("match_type"),
                "map_code": data.get("map_code"),
                "overtime": bool(data.get("overtime")),
                "duration": data.get("duration"),
                "gameplay_duration": record.get("gameplay_duration"),
                "dataset_score": record.get("score"),
                "team": color,
                "slot": slot,
                "player_id": f"{pid.get('platform')}:{pid.get('id')}",
                "platform": pid.get("platform"),
                "player_name": player.get("name"),
                "start_time": player.get("start_time"),
                "end_time": player.get("end_time"),
                "car_name": player.get("car_name"),
                "rank_id": rank.get("id"),
                "rank_tier": rank.get("tier"),
                "rank_division": rank.get("division"),
                "rank_points": rank_points(rank),
                "team_goals": team_goals[color],
                "opp_goals": team_goals[other],
                "mvp": bool(player.get("mvp")),
            }
            for group, fields in STAT_GROUPS.items():
                block = stats.get(group) or {}
                for field in fields:
                    row[f"{group}_{field}"] = block.get(field)
            rows.append(row)
    return rows


def load_player_games(path, *, limit: int | None = None):
    """Return a polars DataFrame of player-game rows (one per player per match)."""
    import polars as pl

    rows = []
    for n, record in enumerate(iter_records(path)):
        if limit is not None and n >= limit:
            break
        rows.extend(player_rows(record))
    frame = pl.DataFrame(rows, infer_schema_length=None)
    return frame.with_columns(
        (pl.col("team_goals") > pl.col("opp_goals")).alias("won"),
        (pl.col("team_goals") - pl.col("opp_goals")).alias("goal_diff"),
    )
