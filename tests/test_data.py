import copy
import json
import tempfile
import unittest
from pathlib import Path
from rlanalytics.data import DataValidationError, from_ballchasing, import_csv, load_matches, validate_match, write_matches

def replay(replay_id="upload-1", match_id="MATCH-1"):
    result = {"id": replay_id, "rocket_league_id": match_id, "status": "ok",
              "playlist_id": "ranked-doubles", "date": "2024-01-01T12:00:00+02:00", "duration": 300}
    for color, offset, score in (("blue", 0, 2), ("orange", 2, 1)):
        result[color] = {"name": color.title(), "stats": {"core": {"goals": score}}, "players": []}
        for i in range(2):
            result[color]["players"].append({
                "id": {"platform": "steam", "id": str(76561198000000000 + offset + i)},
                "name": f"Player {offset + i}", "start_time": 0, "end_time": 300.4,
                "stats": {"core": {"goals": 1 if i == 0 else 0, "shots": 3,
                    "shots_against": 6, "goals_against": 1 if color == "blue" else 2},
                    "demo": {"inflicted": 0, "taken": 1}, "boost": {"avg_amount": 45.2}}})
    return result

class DataTests(unittest.TestCase):
    def test_provider_identity_timezone_and_missingness(self):
        match = from_ballchasing(replay())
        self.assertEqual(match["match_id"], "MATCH-1")
        self.assertEqual(match["played_at"], "2024-01-01T10:00:00Z")
        self.assertEqual(len({p["player_id"] for p in match["players"]}), 4)
        self.assertEqual(match["players"][0]["player_id"], "steam:76561198000000000")
        self.assertIsNone(match["players"][0]["assists"])
        self.assertEqual(match["players"][0]["demos"], 0)
        self.assertAlmostEqual(match["players"][0]["duration_seconds"], 300.4)

    def test_distinct_matches_with_same_prefix_and_score_survive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "matches.jsonl"
            write_matches(path, [from_ballchasing(replay()), from_ballchasing(replay("u2", "MATCH-2"))])
            self.assertEqual(len(load_matches(path)), 2)

    def test_missing_match_identity_not_replaced_by_upload_id(self):
        raw = replay()
        del raw["rocket_league_id"]
        with self.assertRaisesRegex(DataValidationError, "match_id"):
            from_ballchasing(raw)

    def test_match_guid_preferred(self):
        raw = replay()
        raw["match_guid"] = "actual-match"
        match = from_ballchasing(raw)
        self.assertEqual(match["match_id"], "actual-match")
        self.assertEqual(match["match_id_basis"], "match_guid")
        self.assertEqual(match["source_rocket_league_ids"], ["MATCH-1"])

    def test_unknown_timezone_requires_correction(self):
        raw = replay()
        raw["date_has_timezone"] = False
        with self.assertRaisesRegex(DataValidationError, "timezone is unknown"):
            from_ballchasing(raw)
        fixed = from_ballchasing(raw, metadata={"played_at": "2024-01-01T12:00:00Z"})
        self.assertEqual(fixed["played_at"], "2024-01-01T12:00:00Z")

    def test_consistent_elapsed_exposure(self):
        raw = replay()
        raw["play_duration"] = 280
        fixed = from_ballchasing(raw)
        self.assertEqual(fixed["duration_seconds"], 300)
        self.assertEqual(fixed["duration_basis"], "replay_elapsed_seconds")
        self.assertEqual(fixed["players"][0]["duration_seconds"], 300.4)

    def test_impossible_exposure_window_rejected(self):
        raw = replay()
        raw["blue"]["players"][0].update(start_time=1000, end_time=1300)
        with self.assertRaisesRegex(DataValidationError, "exposure window"):
            from_ballchasing(raw)

    def test_required_exposure_and_outcome(self):
        for mutation in (lambda r: r["blue"]["players"][0].pop("end_time"),
                         lambda r: r["blue"]["stats"]["core"].pop("goals"), lambda r: r.pop("duration")):
            raw = replay()
            mutation(raw)
            with self.assertRaises(DataValidationError):
                from_ballchasing(raw)

    def test_missing_statistics_not_zero(self):
        raw = replay()
        raw["blue"]["players"][0]["stats"] = {}
        player = from_ballchasing(raw)["players"][0]
        for key in ("shots_against", "goals", "boost_mean"):
            self.assertIsNone(player[key])

    def test_duplicate_identity_case_and_incomplete_roster(self):
        raw = replay()
        raw["orange"]["players"][0]["id"] = dict(raw["blue"]["players"][0]["id"], platform="Steam")
        with self.assertRaisesRegex(DataValidationError, "duplicate"):
            from_ballchasing(raw)
        raw = replay()
        raw["orange"]["players"].pop()
        with self.assertRaisesRegex(DataValidationError, "roster"):
            from_ballchasing(raw)

    def test_private_requires_explicit_size_and_roundtrips(self):
        raw = replay()
        raw["playlist_id"] = "ranked-standard"
        with self.assertRaisesRegex(DataValidationError, "expected 3"):
            from_ballchasing(raw)
        raw["playlist_id"] = "private"
        with self.assertRaisesRegex(DataValidationError, "explicit team size"):
            from_ballchasing(raw)
        match = from_ballchasing(raw, expected_players_per_team=2)
        self.assertEqual(validate_match(match)["expected_players_per_team"], 2)
        raw["team_size"] = 2
        self.assertEqual(from_ballchasing(raw)["expected_players_per_team"], 2)
        with self.assertRaisesRegex(DataValidationError, "expected 3"):
            from_ballchasing(raw, expected_players_per_team=3)

    def test_invalid_numeric_and_timezone(self):
        base = from_ballchasing(replay())
        for bad in (float("nan"), float("inf"), -1, True):
            record = copy.deepcopy(base)
            record["players"][0]["shots"] = bad
            with self.assertRaises(DataValidationError):
                validate_match(record)
        base["played_at"] = "2024-01-01T12:00:00"
        with self.assertRaisesRegex(DataValidationError, "timezone"):
            validate_match(base)

    def test_metadata_cannot_override_identity(self):
        match = from_ballchasing(replay(), metadata={"series_id": "series-a", "match_id": "wrong"})
        self.assertEqual(match["series_id"], "series-a")
        self.assertEqual(match["match_id"], "MATCH-1")

    def test_ties_rejected(self):
        raw = replay()
        raw["orange"]["stats"]["core"]["goals"] = 2
        with self.assertRaisesRegex(DataValidationError, "tied"):
            from_ballchasing(raw)

    def test_malformed_nested_stats(self):
        raw = replay()
        raw["blue"]["players"][0]["stats"] = [1, 2]
        with self.assertRaisesRegex(DataValidationError, "object required"):
            from_ballchasing(raw)

    def test_atomic_roundtrip_and_duplicate_rejection(self):
        match = from_ballchasing(replay())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "matches.jsonl"
            write_matches(path, [match])
            before = path.read_bytes()
            with self.assertRaises(DataValidationError):
                write_matches(path, [match, match])
            self.assertEqual(path.read_bytes(), before)
            path.write_text(json.dumps(match) + "\n" + json.dumps(match) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(DataValidationError, "duplicate"):
                load_matches(path)

    def test_named_csv_and_canonical_duration_mapping(self):
        metadata = from_ballchasing(replay())
        metadata.pop("players")
        metadata["player_duration_seconds"] = {f"steam:{i}": 300 for i in range(11, 15)}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "historic.csv"
            path.write_text("platform;account;display;side;shots\n"
                            "Steam;11;Same;blue;3\nsteam;12;Same;blue;\n"
                            "steam;13;Other;orange;2\nsteam;14;Other;orange;1\n", encoding="utf-8")
            columns = {"player_id": "account", "name": "display", "team": "side"}
            record = import_csv(path, metadata=metadata, column_map=columns)
            self.assertEqual(len(record["players"]), 4)
            self.assertIsNone(record["players"][1]["shots"])
            with self.assertRaisesRegex(DataValidationError, "header"):
                import_csv(path, metadata=metadata)
            metadata.pop("player_duration_seconds")
            with self.assertRaisesRegex(DataValidationError, "exposure"):
                import_csv(path, metadata=metadata, column_map=columns)
