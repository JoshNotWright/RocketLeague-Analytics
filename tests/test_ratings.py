import copy
import json
import unittest
from rlanalytics.ratings import build_reference, rate_players
from rlanalytics.demo import synthetic_matches

def find(rows, pid):
    return next(r for r in rows if r["player_id"] == pid)

class FrozenReferenceTests(unittest.TestCase):
    def setUp(self):
        self.matches = synthetic_matches(30)
        self.reference = build_reference(self.matches[:20], version="test-v1")
        self.pid = self.matches[20]["players"][0]["player_id"]

    def test_reference_and_intervals_survive_unrelated_outlier(self):
        frozen = json.dumps(self.reference, sort_keys=True)
        scored = self.matches[20:]
        before = rate_players(scored, self.reference, bootstrap_samples=100)
        outlier = copy.deepcopy(scored[0])
        outlier["match_id"] = "unrelated"
        for player in outlier["players"]:
            player["player_id"] = "new:" + player["player_id"]
            player["goals"] = 10000
        after = rate_players(scored + [outlier], self.reference, bootstrap_samples=100)
        for row in before:
            self.assertEqual(row, find(after, row["player_id"]))
        self.assertEqual(frozen, json.dumps(self.reference, sort_keys=True))

    def test_equal_rates_after_doubling_duration(self):
        reference = build_reference(self.matches[:20], version="rates", prior_seconds=0)
        original = self.matches[20]
        doubled = copy.deepcopy(original)
        doubled["match_id"] = "doubled"
        doubled["duration_seconds"] *= 2
        for player in doubled["players"]:
            player["duration_seconds"] *= 2
            for metric in ("goals", "assists", "shots", "saves", "demos"):
                player[metric] *= 2
        a, b = rate_players([original], reference), rate_players([doubled], reference)
        for row in a:
            other = find(b, row["player_id"])
            self.assertEqual(row["rates_per_5_minutes"], other["rates_per_5_minutes"])
            self.assertEqual(row["descriptive_index"], other["descriptive_index"])

    def test_small_samples_shrink_more(self):
        game = copy.deepcopy(self.matches[20])
        for player in game["players"]:
            player["goals"] = 30
        short = find(rate_players([game], self.reference), self.pid)
        repeated = [dict(game, match_id=f"repeat-{i}") for i in range(10)]
        long = find(rate_players(repeated, self.reference, bootstrap_samples=0), self.pid)
        self.assertLess(abs(short["descriptive_index"]), abs(long["descriptive_index"]))
        self.assertLess(short["shrinkage_data_weight"], long["shrinkage_data_weight"])
        self.assertIsNone(short["interval"])

    def test_seeded_bootstrap_without_single_game_certainty(self):
        a = rate_players(self.matches, self.reference, bootstrap_samples=100, seed=8)
        self.assertEqual(a, rate_players(self.matches, self.reference, bootstrap_samples=100, seed=8))
        self.assertTrue(all(r["interval"] is not None for r in a))
        self.assertTrue(all(r["interval"] is None for r in rate_players(self.matches[:1], self.reference)))

    def test_constant_reference_future_outlier_contributes_zero(self):
        games = copy.deepcopy(self.matches[:20])
        for game in games:
            for player in game["players"]:
                for metric in ("goals", "assists", "shots", "saves", "demos"):
                    player[metric] = 0
        reference = json.loads(json.dumps(build_reference(games, version="constant")))
        self.assertTrue(all(p["constant_reference"] for p in reference["metrics"].values()))
        self.assertTrue(all(r["descriptive_index"] == 0 for r in rate_players(self.matches[20:], reference)))

    def test_cohort_mismatch(self):
        mixed = copy.deepcopy(self.matches[20])
        mixed["playlist"] = "ranked-standard"
        with self.assertRaisesRegex(ValueError, "playlist"):
            rate_players([mixed], self.reference)
        with self.assertRaisesRegex(ValueError, "separate reference"):
            build_reference(self.matches[:1] + [mixed], version="mixed")

    def test_private_team_sizes_cannot_share_reference(self):
        first = copy.deepcopy(self.matches[0])
        first.update(playlist="private", expected_players_per_team=2)
        second = copy.deepcopy(self.matches[1])
        second.update(playlist="private", expected_players_per_team=1)
        second["players"] = [second["players"][0], second["players"][2]]
        reference = build_reference([first], version="private-2v2")
        with self.assertRaises(ValueError):
            build_reference([first, second], version="mixed")
        with self.assertRaises(ValueError):
            rate_players([second], reference)

    def test_duplicate_upload_not_double_counted(self):
        game = self.matches[20]
        alias = copy.deepcopy(game)
        alias["replay_ids"] = ["other"]
        self.assertEqual(rate_players([game], self.reference), rate_players([game, alias], self.reference))
        alias["players"][0]["goals"] += 99
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            rate_players([game, alias], self.reference)

    def test_missing_weighted_metric_and_invalid_exposure_rejected(self):
        game = copy.deepcopy(self.matches[20])
        game["players"][0]["duration_seconds"] = 0
        with self.assertRaises(ValueError):
            rate_players([game], self.reference)
        game = copy.deepcopy(self.matches[20])
        game["players"][0]["goals"] = None
        with self.assertRaisesRegex(ValueError, "missing data"):
            rate_players([game], self.reference)

    def test_unweighted_missing_metrics_allowed(self):
        games = copy.deepcopy(self.matches)
        for game in games:
            for player in game["players"]:
                player["saves"] = None
        reference = build_reference(games[:20], version="goals", weights={"goals": 1, "saves": 0})
        self.assertEqual(set(reference["weights"]), {"goals"})
        self.assertTrue(rate_players(games[20:], reference))

    def test_fractional_provider_rounding_is_accepted(self):
        game = copy.deepcopy(self.matches[20])
        game["players"][0]["duration_seconds"] = game["duration_seconds"] + 0.4
        self.assertTrue(rate_players([game], self.reference))
