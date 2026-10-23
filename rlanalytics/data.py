"""Validated, provider-independent match records. No network or import side effects."""
from __future__ import annotations
import copy
import csv
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
STAT_FIELDS = ("goals", "assists", "shots", "saves", "shots_against",
               "goals_against", "demos", "demos_taken", "boost_mean")
ROSTER_SIZES = {"ranked-duels": 1, "ranked-doubles": 2,
                "ranked-standard": 3, "ranked-solo-standard": 3}

class DataValidationError(ValueError):
    """A record is unusable; the message describes the invalid field."""

def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise DataValidationError(f"{field}: nonempty string required")
    return value.strip()

def _number(value, field, *, positive=False, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DataValidationError(f"{field}: finite number required")
    if not math.isfinite(value) or value < 0 or (positive and value <= 0):
        raise DataValidationError(f"{field}: {'positive' if positive else 'nonnegative'} finite number required")
    if integer and int(value) != value:
        raise DataValidationError(f"{field}: integer required")
    return int(value) if integer else float(value)

def _utc(value, field="played_at"):
    value = _text(value, field)
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DataValidationError(f"{field}: ISO8601 timestamp required") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise DataValidationError(f"{field}: explicit timezone required")
    return result.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _mapping(value, field):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise DataValidationError(f"{field}: object required")
    return value

def validate_match(match, *, expected_players_per_team=None):
    """Return a validated copy. Missing statistics become null, never zero.

    Required: match/upload/player identities, team membership, date, playlist,
    final score, match duration and observed player durations. Ranked modes have
    fixed rosters; private matches require an explicit expected size.
    Tied outcomes and roster substitutions are not supported.
    """
    if not isinstance(match, dict):
        raise DataValidationError("match: object required")
    result = copy.deepcopy(match)
    if type(result.get("schema_version")) is not int or result["schema_version"] != SCHEMA_VERSION:
        raise DataValidationError("schema_version: expected 1")
    for key in ("match_id", "playlist"):
        result[key] = _text(result.get(key), key)
    replay_ids = result.get("replay_ids")
    if not isinstance(replay_ids, list) or not replay_ids:
        raise DataValidationError("replay_ids: nonempty list required")
    result["replay_ids"] = sorted({_text(x, "replay_id") for x in replay_ids})
    if "source_rocket_league_ids" in result:
        if not isinstance(result["source_rocket_league_ids"], list):
            raise DataValidationError("source_rocket_league_ids: list required")
        result["source_rocket_league_ids"] = sorted({_text(x, "rocket_league_id") for x in result["source_rocket_league_ids"]})
    result["played_at"] = _utc(result.get("played_at"))
    result["duration_seconds"] = _number(result.get("duration_seconds"), "duration_seconds", positive=True)
    for field in ("blue_score", "orange_score"):
        result[field] = _number(result.get(field), field, integer=True)
    if result["blue_score"] == result["orange_score"]:
        raise DataValidationError("score: tied or unfinished outcomes are unsupported")
    if result.get("series_id") is not None:
        result["series_id"] = _text(result["series_id"], "series_id")
    players = result.get("players")
    if not isinstance(players, list) or not players:
        raise DataValidationError("players: nonempty list required")
    seen, counts = set(), {"blue": 0, "orange": 0}
    for index, player in enumerate(players):
        if not isinstance(player, dict):
            raise DataValidationError(f"players[{index}]: object required")
        pid = _text(player.get("player_id"), "player_id")
        platform, separator, identity = pid.partition(":")
        if not separator or not platform or not identity:
            raise DataValidationError("player_id: full platform:id required")
        pid = platform.lower() + ":" + identity
        if pid in seen:
            raise DataValidationError("player_id: duplicate player in match")
        seen.add(pid)
        player["player_id"] = pid
        player["name"] = _text(player.get("name"), "name")
        if player.get("team") not in counts:
            raise DataValidationError("team: blue or orange required")
        counts[player["team"]] += 1
        if player.get("team_name") is not None:
            player["team_name"] = _text(player["team_name"], "team_name")
        duration = _number(player.get("duration_seconds"), "player.duration_seconds", positive=True)
        # Provider match duration is rounded; observed player spans retain fractions.
        if duration > result["duration_seconds"] + 1:
            raise DataValidationError("player.duration_seconds: exceeds match duration")
        player["duration_seconds"] = duration
        for field in STAT_FIELDS:
            value = player.get(field)
            player[field] = None if value is None else _number(value, field, integer=field != "boost_mean")
        if player["boost_mean"] is not None and player["boost_mean"] > 100:
            raise DataValidationError("boost_mean: must be between 0 and 100")
    roster = ROSTER_SIZES.get(result["playlist"])
    if roster is None and result["playlist"] != "private":
        raise DataValidationError("playlist: unsupported mode; use ranked duels/doubles/standard or private")
    expected = expected_players_per_team
    if expected is None:
        expected = result.get("expected_players_per_team")
    if expected is not None:
        expected = _number(expected, "expected_players_per_team", positive=True, integer=True)
        if roster is not None and roster != expected:
            raise DataValidationError("expected_players_per_team: contradicts ranked playlist")
        roster = expected
    if roster is None:
        raise DataValidationError("expected_players_per_team: private matches require an explicit team size")
    result["expected_players_per_team"] = roster
    if counts["blue"] != counts["orange"] or counts["blue"] == 0:
        raise DataValidationError("players: equal positive team rosters required")
    if any(count != roster for count in counts.values()):
        raise DataValidationError(f"players: expected {roster} players per team")
    result["players"] = sorted(players, key=lambda p: (p["team"], p["player_id"]))
    return result

def from_ballchasing(payload, *, metadata=None, expected_players_per_team=None):
    """Normalize GET /api/replays/{id} using named fields.

    Exposure is replay elapsed seconds: duration and end_time-start_time, not
    play_duration. Prefer match_guid; the labeled rocket_league_id fallback
    cannot guarantee joining different recording perspectives. Metadata can
    supply series_id and played_at correction, but not provider identities.
    """
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise DataValidationError("status: successfully processed replay required")
    metadata = _mapping(metadata, "metadata")
    date = metadata.get("played_at", payload.get("date"))
    if (payload.get("date_has_timezone") is False or payload.get("date_has_tz") is False) and "played_at" not in metadata:
        raise DataValidationError("played_at: provider timezone is unknown; explicit metadata override required")
    record = {
        "schema_version": SCHEMA_VERSION,
        "match_id": payload.get("match_guid") or payload.get("rocket_league_id"),
        "match_id_basis": "match_guid" if payload.get("match_guid") else "rocket_league_id",
        "source_rocket_league_ids": [payload["rocket_league_id"]] if payload.get("rocket_league_id") else [],
        "duration_basis": "replay_elapsed_seconds",
        "replay_ids": [payload.get("id")],
        "playlist": payload.get("playlist_id"),
        "played_at": date, "duration_seconds": payload.get("duration"), "players": [],
    }
    if payload.get("playlist_id") == "private" and payload.get("team_size") is not None:
        record["expected_players_per_team"] = payload["team_size"]
    if metadata.get("series_id") is not None:
        record["series_id"] = metadata["series_id"]
    for color in ("blue", "orange"):
        team = payload.get(color)
        if not isinstance(team, dict) or not isinstance(team.get("players"), list):
            raise DataValidationError(f"{color}: team and player list required")
        stats = _mapping(team.get("stats"), "team.stats")
        core = _mapping(stats.get("core"), "team.stats.core")
        record[f"{color}_score"] = team.get("goals", core.get("goals"))
        for raw in team["players"]:
            if not isinstance(raw, dict):
                raise DataValidationError("player: object required")
            identity = _mapping(raw.get("id"), "player.id")
            platform = _text(identity.get("platform"), "player.id.platform")
            platform_id = _text(identity.get("id"), "player.id.id")
            start = _number(raw.get("start_time"), "start_time")
            end = _number(raw.get("end_time"), "end_time", positive=True)
            match_duration = _number(payload.get("duration"), "duration", positive=True)
            if end <= start or end > match_duration + 1:
                raise DataValidationError("player exposure window: invalid start/end for match duration")
            pstats = _mapping(raw.get("stats"), "player.stats")
            pcore = _mapping(pstats.get("core"), "player.stats.core")
            demo = _mapping(pstats.get("demo"), "player.stats.demo")
            boost = _mapping(pstats.get("boost"), "player.stats.boost")
            player = {"player_id": f"{platform}:{platform_id}", "name": raw.get("name"),
                      "team": color, "duration_seconds": end - start,
                      **{key: pcore.get(key) for key in STAT_FIELDS[:6]},
                      "demos": demo.get("inflicted"), "demos_taken": demo.get("taken"),
                      "boost_mean": boost.get("avg_amount")}
            if team.get("name"):
                player["team_name"] = team["name"]
            record["players"].append(player)
    return validate_match(record, expected_players_per_team=expected_players_per_team)

def atomic_json(path, value):
    """Atomically replace JSON, leaving the old file on failure."""
    _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n")

def _atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def write_matches(path, matches):
    """Validate then atomically write canonical JSONL in a stable order."""
    records = [validate_match(match) for match in matches]
    ids = [record["match_id"] for record in records]
    if len(ids) != len(set(ids)):
        raise DataValidationError("match_id: duplicate match; merge upload provenance first")
    records.sort(key=lambda r: (r["played_at"], r["match_id"]))
    _atomic_text(path, "".join(json.dumps(r, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n" for r in records))

def load_matches(path):
    """Read canonical JSONL; reject duplicate matches and identify bad lines."""
    matches, seen = [], set()
    with Path(path).open(encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = validate_match(json.loads(line))
            except (ValueError, TypeError) as exc:
                raise DataValidationError(f"line {line_number}: {exc}") from exc
            if record["match_id"] in seen:
                raise DataValidationError(f"line {line_number}: duplicate match_id")
            seen.add(record["match_id"])
            matches.append(record)
    return matches

def import_csv(path, *, metadata, column_map=None, delimiter=";"):
    """Import ONE historical match using named headers and explicit metadata.

    Metadata contains canonical match fields except players. It may provide
    player_duration_seconds as a scalar or mapping keyed by full ID. column_map
    maps canonical player fields to source headers. Full platform:id or separate
    named platform/player_id columns are required. Default separator is semicolon.
    """
    if not isinstance(metadata, dict):
        raise DataValidationError("metadata: explicit match metadata required")
    if column_map is not None and not isinstance(column_map, dict):
        raise DataValidationError("column_map: object required")
    record = {k: copy.deepcopy(v) for k, v in metadata.items()
              if k not in ("players", "player_duration_seconds")}
    record.setdefault("schema_version", SCHEMA_VERSION)
    record["players"] = []
    columns = column_map or {}
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter=delimiter)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise DataValidationError("CSV: unique named headers required")
        for field in ("player_id", "name", "team"):
            if columns.get(field, field) not in reader.fieldnames:
                raise DataValidationError(f"CSV: missing named header for {field}")
        for row in reader:
            if None in row:
                raise DataValidationError("CSV: row contains extra columns")
            def cell(field):
                return row.get(columns.get(field, field))
            pid, platform = cell("player_id"), cell("platform")
            if pid and ":" not in pid and platform:
                pid = f"{platform}:{pid}"
            if pid and ":" in pid:
                prefix, account = pid.split(":", 1)
                pid = prefix.lower() + ":" + account
            player = {"player_id": pid, "name": cell("name"), "team": cell("team")}
            duration = cell("duration_seconds")
            if duration in (None, ""):
                exposure = metadata.get("player_duration_seconds")
                duration = exposure.get(pid) if isinstance(exposure, dict) else exposure
            try:
                player["duration_seconds"] = float(duration)
                for field in STAT_FIELDS:
                    value = cell(field)
                    player[field] = None if value in (None, "") else float(value)
            except (TypeError, ValueError) as exc:
                raise DataValidationError("CSV: invalid or missing numeric field/exposure") from exc
            if cell("team_name"):
                player["team_name"] = cell("team_name")
            record["players"].append(player)
    return validate_match(record, expected_players_per_team=metadata.get("expected_players_per_team"))
