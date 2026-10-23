import copy
import importlib.util
import io
import json
from pathlib import Path
import runpy
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch
from rlanalytics.stats import aggregate_players, aggregate_teams, flatten_rows

def player(identity, team, **values):
    data = dict(player_id=identity, name=identity, team=team, duration_seconds=300,
                goals=0, assists=0, shots=0, saves=0, shots_against=0, goals_against=0,
                demos=0, demos_taken=0, boost_mean=50)
    data.update(values)
    return data

def match(identity="m1", **values):
    data = dict(schema_version=1, match_id=identity, replay_ids=[identity], playlist="ranked-duels",
                played_at="2024-01-01T00:00:00Z", duration_seconds=300, blue_score=1, orange_score=0,
                players=[player("steam:a", "blue"), player("steam:b", "orange")])
    data.update(values)
    return data

def by_player(rows, identity="steam:a"):
    return next(r for r in rows if r.get("player_id") == identity)

def by_team(rows, identities):
    return next(r for r in rows if r["roster_player_ids"] == sorted(identities))

class PlayerStatsTests(unittest.TestCase):
    def test_exact_identity_not_prefix(self):
        data = match(players=[player("steam:al", "blue", goals=1),
                              player("steam:alpha", "orange", goals=9)])
        rows = aggregate_players([data])
        self.assertEqual(by_player(rows, "steam:al")["metrics"]["goals"]["total"], 1)
        self.assertEqual(by_player(rows, "steam:alpha")["metrics"]["goals"]["total"], 9)

    def test_alias_merge_playlist_separation(self):
        first, second, third = match(), match("m2"), match("m3", playlist="private")
        second["players"][0]["name"] = "Changed name"
        rows = [r for r in aggregate_players([third, second, first]) if r["player_id"] == "steam:a"]
        self.assertEqual([r["match_count"] for r in rows], [1, 2])
        self.assertEqual(next(r for r in rows if r["playlist"] == "ranked-duels")["names"], ["Changed name", "steam:a"])

    def test_private_sizes_separate(self):
        first = match(playlist="private")
        second = match("m2", playlist="private", players=[player("steam:a", "blue"),
            player("steam:c", "blue"), player("steam:b", "orange"), player("steam:d", "orange")])
        rows = [r for r in aggregate_players([first, second]) if r["player_id"] == "steam:a"]
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["team_size"] for r in rows}, {1, 2})

    def test_pooled_shooting_zero_shots(self):
        first, second = match(), match("m2")
        first["players"][0].update(goals=1, shots=1)
        second["players"][0].update(goals=0, shots=9)
        rows = aggregate_players([first, second])
        self.assertEqual(by_player(rows)["shooting"]["percentage"], 10)
        self.assertIsNone(by_player(rows, "steam:b")["shooting"]["percentage"])

    def test_joint_missing_shooting_and_metrics(self):
        first, second = match(), match("m2")
        first["players"][0].update(goals=2, shots=None, assists=None)
        second["players"][0].update(goals=1, shots=4, assists=None)
        row = by_player(aggregate_players([first, second]))
        self.assertEqual(row["shooting"]["percentage"], 25)
        self.assertEqual(row["shooting"]["missing_matches"], 1)
        self.assertIsNone(row["metrics"]["assists"]["total"])
        self.assertIsNone(row["metrics"]["assists"]["per_match"])

    def test_boost_weighting_and_matched_rate_exposure(self):
        first, second, third = match(), match("m2"), match("m3")
        first["players"][0].update(duration_seconds=100, boost_mean=20, goals=1, saves=2)
        second["players"][0].update(duration_seconds=300, boost_mean=80, goals=3, saves=None)
        third["players"][0].update(duration_seconds=None, boost_mean=90, goals=100, saves=None)
        row = by_player(aggregate_players([third, second, first]))
        self.assertEqual(row["boost"]["mean"], 65)
        self.assertEqual(row["boost"]["missing_exposure_observations"], 1)
        self.assertEqual(row["metrics"]["goals"]["total"], 104)
        self.assertEqual(row["metrics"]["goals"]["rate_total"], 4)
        self.assertEqual(row["metrics"]["goals"]["per_5_minutes"], 3)
        self.assertEqual(row["metrics"]["saves"]["per_5_minutes"], 6)

    def test_duplicate_and_conflicting_records(self):
        first = match()
        alias = copy.deepcopy(first)
        alias["replay_ids"] = ["other"]
        alias["players"].reverse()
        self.assertEqual(by_player(aggregate_players([first, alias]))["match_count"], 1)
        alias["blue_score"] = 2
        with self.assertRaisesRegex(ValueError, "conflicting records"):
            aggregate_players([first, alias])

    def test_determinism_no_mutation_series(self):
        first, second = match(series_id="s2"), match("m2", series_id="s1")
        original = copy.deepcopy([first, second])
        forward = aggregate_players([first, second])
        self.assertEqual(forward, aggregate_players([second, first]))
        self.assertEqual([first, second], original)
        self.assertEqual(forward[0]["series_ids"], ["s1", "s2"])
        json.dumps(forward, allow_nan=False)

    def test_invalid_values_and_duplicate_ids(self):
        data = match()
        data["players"][1]["player_id"] = "steam:a"
        with self.assertRaisesRegex(ValueError, "duplicate"):
            aggregate_players([data])
        for value in (float("nan"), float("inf"), -1, True, "3"):
            data = match()
            data["players"][0]["shots"] = value
            with self.assertRaises(ValueError):
                aggregate_players([data])

class TeamStatsTests(unittest.TestCase):
    def roster(self, identity="m1"):
        return match(identity, playlist="ranked-standard", players=[
            player("steam:a", "blue", goals=1, shots=1, team_name="Old"),
            player("steam:b", "blue", shots=9, team_name="Old"),
            player("steam:c", "blue", shots=0, team_name="Old"),
            player("steam:d", "orange", shots=2), player("steam:e", "orange", shots=3),
            player("steam:f", "orange", shots=5)])

    def test_roster_order_names_and_team_exposure(self):
        first, second = self.roster(), self.roster("m2")
        second["players"].reverse()
        for p in second["players"]:
            if p["team"] == "blue":
                p["team_name"] = "New"
        rows = aggregate_teams([first, second])
        blue = by_team(rows, ["steam:a", "steam:b", "steam:c"])
        self.assertEqual(blue["match_count"], 2)
        self.assertEqual(blue["team_names"], ["New", "Old"])
        self.assertEqual(blue["duration_seconds"], 600)
        self.assertEqual(blue["boost"]["mean"], 50)
        self.assertEqual(blue["boost"]["exposure_seconds"], 1800)
        self.assertEqual(rows, aggregate_teams([second, first]))

    def test_transfer_and_playlist_separate(self):
        first, changed, other = self.roster(), self.roster("m2"), self.roster("m3")
        changed["players"][2]["player_id"] = "steam:substitute"
        other["playlist"] = "private"
        rows = aggregate_teams([first, changed, other])
        self.assertEqual(len(rows), 5)
        self.assertEqual(len([r for r in rows if "steam:a" in r["roster_player_ids"]]), 3)

    def test_scores_and_opponent_shots_not_multiplied(self):
        data = self.roster()
        data["blue_score"] = 2
        for p in data["players"]:
            p.update(shots_against=999, goals_against=999)
        blue = by_team(aggregate_teams([data]), ["steam:a", "steam:b", "steam:c"])
        self.assertEqual(blue["metrics"]["goals"]["total"], 2)
        self.assertEqual(blue["metrics"]["goals_against"]["total"], 0)
        self.assertEqual(blue["metrics"]["shots_against"]["total"], 10)
        self.assertEqual(blue["shooting"]["percentage"], 10)
        self.assertEqual(blue["outcomes"]["wins"], 1)

    def test_missing_roster_values_not_zero(self):
        data = self.roster()
        data["players"][2].update(shots=None, boost_mean=None)
        rows = aggregate_teams([data])
        blue = by_team(rows, ["steam:a", "steam:b", "steam:c"])
        self.assertIsNone(blue["metrics"]["shots"]["total"])
        self.assertIsNone(blue["shooting"]["percentage"])
        self.assertEqual(blue["boost"]["mean"], 50)
        self.assertEqual(blue["boost"]["partial_matches"], 1)
        self.assertIsNone(by_team(rows, ["steam:d", "steam:e", "steam:f"])["metrics"]["shots_against"]["total"])

    def test_empty_inputs_and_csv_first_record(self):
        self.assertEqual(aggregate_players([]), [])
        self.assertEqual(aggregate_teams([]), [])
        self.assertEqual(flatten_rows([]), [])
        rows = aggregate_players([match()])
        flat = flatten_rows(rows)
        self.assertEqual(len(flat), 2)
        self.assertEqual(flat[0]["player_id"], rows[0]["player_id"])
        self.assertEqual(json.loads(flat[0]["names"]), rows[0]["names"])
        self.assertEqual(list(flat[0]), list(flat[1]))

class LegacySafetyTests(unittest.TestCase):
    def test_legacy_imports_inert(self):
        repo = Path(__file__).resolve().parents[1]
        paths = list(repo.glob("*.py")) + list((repo/"utils").glob("*.py")) + list((repo/"war_calculation").glob("*.py"))
        self.assertEqual(len(paths), 24)
        for path in paths:
            spec = importlib.util.spec_from_file_location("retired_" + path.stem, path)
            module = importlib.util.module_from_spec(spec)
            with patch("builtins.open", side_effect=AssertionError("file access")), patch("subprocess.run", side_effect=AssertionError("process")):
                spec.loader.exec_module(module)
            self.assertTrue(callable(module.main))

    def test_direct_nested_and_root_guidance(self):
        repo = Path(__file__).resolve().parents[1]
        for relative in ("clear_files.py", "utils/get_pro_player_info.py", "war_calculation/calculate_war.py"):
            output = io.StringIO()
            with redirect_stderr(output), self.assertRaises(SystemExit) as caught:
                runpy.run_path(str(repo/relative), run_name="__main__")
            self.assertEqual(caught.exception.code, 2)
            self.assertIn("python -m rlanalytics --help", output.getvalue())
            self.assertIn("retired", output.getvalue())
