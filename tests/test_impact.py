import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from rlanalytics.impact import evaluate_chronologically, expected_wins_above_replacement, fit_model, player_impact, predict_match

def match(number, *, winner="a", blue="a", orange="b", series=None, playlist="ranked-duels"):
    timestamp = datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(days=number)
    result = {"schema_version": 1, "match_id": str(number), "replay_ids": ["upload-" + str(number)],
              "playlist": playlist, "played_at": timestamp.isoformat(), "duration_seconds": 300.0,
              "blue_score": 2 if winner == blue else 1, "orange_score": 2 if winner == orange else 1,
              "players": [{"player_id": "steam:" + blue, "name": blue, "team": "blue"},
                          {"player_id": "steam:" + orange, "name": orange, "team": "orange"}]}
    if series is not None:
        result["series_id"] = series
    return result

def sample(count=20):
    return [match(i, winner="b" if i % 5 == 0 else "a", blue="a" if i % 2 else "b",
                  orange="b" if i % 2 else "a", series="series-" + str(i // 2)) for i in range(count)]

class RosterModelTests(unittest.TestCase):
    def setUp(self):
        self.matches = sample()
        self.model = fit_model(self.matches, replacement_player_ids=["steam:b"])

    def test_explicit_training_replacement_pool(self):
        for pool in ([], ["steam:unseen"]):
            with self.assertRaises(ValueError):
                fit_model(self.matches, replacement_player_ids=pool)

    def test_prediction_ignores_postmatch_values(self):
        game = match(25)
        before = predict_match(self.model, game)
        altered = copy.deepcopy(game)
        altered.update(blue_score=0, orange_score=10000, duration_seconds=1)
        for player in altered["players"]:
            player.update(goals=12345, saves=999, shots=10000)
        self.assertEqual(before, predict_match(self.model, altered))
        del altered["blue_score"], altered["orange_score"], altered["duration_seconds"]
        self.assertEqual(before, predict_match(self.model, altered))

    def test_counterfactual_correct_team_perspective(self):
        for game in (match(25), match(26, blue="b", orange="a")):
            result = player_impact(self.model, game, "steam:a")
            self.assertGreater(result["expected_wins_above_replacement"], 0)
            self.assertAlmostEqual(result["expected_wins_above_replacement"],
                result["team_win_probability"] - result["replacement_team_win_probability"])
        self.assertAlmostEqual(player_impact(self.model, match(25), "steam:b")["expected_wins_above_replacement"], 0)

    def test_schedule_shared_covariance(self):
        first, second = match(25), match(26)
        single = player_impact(self.model, first, "steam:a")
        total = expected_wins_above_replacement(self.model, [first, second], "steam:a")
        self.assertAlmostEqual(total["expected_wins_above_replacement"], 2 * single["expected_wins_above_replacement"])
        self.assertAlmostEqual(total["interval"]["upper"] - total["interval"]["lower"],
                               2 * (single["interval"]["upper"] - single["interval"]["lower"]))
        self.assertEqual(expected_wins_above_replacement(self.model, [first, first], "steam:a")["matches"], 1)

    def test_unknown_players_no_false_interval(self):
        result = predict_match(self.model, match(25, orange="unknown"))
        self.assertEqual(result["unknown_players"], ["steam:unknown"])
        self.assertIsNone(result["interval"])
        with self.assertRaisesRegex(ValueError, "unseen"):
            player_impact(self.model, match(25, orange="unknown"), "steam:unknown")

    def test_persistent_teammates_disclose_nonidentification(self):
        games = []
        for i in range(10):
            game = match(i, winner="a" if i % 3 else "c", blue="a", orange="c", playlist="ranked-doubles")
            game["players"].extend([{"player_id": "steam:b", "team": "blue"},
                                    {"player_id": "steam:d", "team": "orange"}])
            games.append(game)
        model = fit_model(games, replacement_player_ids=["steam:c", "steam:d"])
        self.assertTrue(model["training"]["additional_roster_confounding"])
        self.assertIn(["steam:a", "steam:b"], model["training"]["inseparable_teammate_groups"])
        self.assertAlmostEqual(model["coefficients"]["steam:a"], model["coefficients"]["steam:b"])

    def test_color_bias_confounding(self):
        games = [match(i, winner="a" if i % 3 else "b") for i in range(10)]
        self.assertTrue(fit_model(games, replacement_player_ids=["steam:b"])["training"]["additional_roster_confounding"])

    def test_incomplete_or_mixed_rosters_rejected(self):
        with self.assertRaises(ValueError):
            fit_model(self.matches + [match(25, playlist="ranked-standard")], replacement_player_ids=["steam:b"])
        with self.assertRaises(ValueError):
            predict_match(self.model, match(25, playlist="ranked-doubles"))

    def test_ties_and_flagged_forfeits_excluded(self):
        tied, forfeit = match(25), match(26)
        tied["blue_score"] = tied["orange_score"] = 2
        forfeit["forfeit"] = True
        model = fit_model(self.matches + [tied, forfeit], replacement_player_ids=["steam:b"])
        self.assertEqual(model["training"]["excluded"], {"ties": 1, "forfeits": 1})
        self.assertEqual(model["training"]["matches"], len(self.matches))

    def test_model_roundtrip_and_covariance_validation(self):
        restored = json.loads(json.dumps(self.model))
        self.assertEqual(predict_match(restored, match(25)), predict_match(self.model, match(25)))
        restored["covariance"][0][0] = -100
        with self.assertRaises(ValueError):
            predict_match(restored, match(25))

class TemporalEvaluationTests(unittest.TestCase):
    def test_holdout_labels_cannot_change_fit_or_prediction(self):
        games = sample()
        first = evaluate_chronologically(games, replacement_player_ids=["steam:b"], train_fraction=0.6)
        test_ids = set(first["split"]["test_match_ids"])
        mutated = copy.deepcopy(games)
        for game in mutated:
            if game["match_id"] in test_ids:
                game["blue_score"], game["orange_score"] = game["orange_score"], game["blue_score"]
                for player in game["players"]:
                    player["goals"] = 99999
        second = evaluate_chronologically(mutated, replacement_player_ids=["steam:b"], train_fraction=0.6)
        self.assertEqual(first["model"], second["model"])
        self.assertEqual([p["blue_win_probability"] for p in first["predictions"]],
                         [p["blue_win_probability"] for p in second["predictions"]])
        self.assertTrue(set(first["model"]["training"]["match_ids"]).isdisjoint(test_ids))
        self.assertEqual(first["baselines"]["coin_flip"]["accuracy"], 0.5)
        self.assertIn("calibration_bins", first["metrics"])

    def test_series_crossing_boundary_purged(self):
        games = sample(10)
        for game in games:
            game.pop("series_id")
        games[4]["series_id"] = games[5]["series_id"] = "crosses"
        result = evaluate_chronologically(games, replacement_player_ids=["steam:b"], train_fraction=0.5)
        self.assertEqual(set(result["split"]["purged_match_ids"]), {"4", "5"})
        self.assertFalse(result["split"]["all_matches_have_series_ids"])

    def test_explicit_mapping_and_missing_series(self):
        games = sample(10)
        groups = {game["match_id"]: "pair-" + str(i // 2) for i, game in enumerate(games)}
        for game in games:
            game.pop("series_id")
        result = evaluate_chronologically(games, replacement_player_ids=["steam:b"], group_ids=groups)
        self.assertTrue(result["split"]["all_matches_have_series_ids"])
        self.assertEqual(evaluate_chronologically(games, replacement_player_ids=["steam:b"])["split"]["series_coverage"], 0)

    def test_future_replacement_cannot_leak(self):
        games = sample(10)
        games[-1]["players"][0]["player_id"] = "steam:future"
        with self.assertRaisesRegex(ValueError, "absent from training"):
            evaluate_chronologically(games, replacement_player_ids=["steam:future"])
