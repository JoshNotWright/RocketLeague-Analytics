import unittest
from datetime import datetime, timedelta, timezone

try:
    import polars as pl
except ImportError:  # pragma: no cover
    pl = None

if pl is not None:
    from rlreplay import xg

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)


def per_game(player, values):
    return [{"replay_id": f"{player}-{i}", "player_id": player, "played_at": T0 + timedelta(days=i),
             "stat": v, "shots": 2, "weighted": v * 2} for i, v in enumerate(values)]


@unittest.skipIf(pl is None, "polars not installed")
class SplitHalfTests(unittest.TestCase):
    def test_persistent_statistic_has_high_reliability(self):
        # four players whose per-game value is a stable trait with small symmetric noise
        rows = per_game("a", [1.0, 1.2] * 5) + per_game("b", [2.0, 1.8] * 5) + per_game("c", [3.0, 3.1] * 5) \
            + per_game("d", [4.0, 3.9] * 5)
        result = xg.split_half(pl.DataFrame(rows), "stat", min_games=10)
        self.assertEqual(result["players"], 4)
        self.assertGreater(result["split_half_corr"], 0.95)

    def test_alternating_statistic_has_no_reliability(self):
        # values alternate by game, so odd and even halves disagree for every player
        rows = per_game("a", [0.0, 1.0] * 5) + per_game("b", [1.0, 0.0] * 5) + per_game("c", [0.0, 2.0] * 5) \
            + per_game("d", [2.0, 0.0] * 5)
        result = xg.split_half(pl.DataFrame(rows), "stat", min_games=10)
        self.assertLess(result["split_half_corr"], -0.5)

    def test_weighted_ratio_and_min_games(self):
        rows = per_game("a", [1.0] * 10) + per_game("b", [2.0] * 10) + per_game("short", [5.0] * 4)
        result = xg.split_half(pl.DataFrame(rows), "weighted", min_games=10, weight="shots")
        self.assertEqual(result["players"], 2)  # "short" is below min_games

    def test_calibration_bins(self):
        import numpy as np
        p = np.linspace(0, 1, 100)
        y = (p > 0.5).astype(float)
        bins = xg.calibration(y, p, bins=4)
        self.assertEqual([b["n"] for b in bins], [25, 25, 25, 25])
        self.assertEqual(bins[0]["mean_actual"], 0.0)
        self.assertEqual(bins[-1]["mean_actual"], 1.0)


if __name__ == "__main__":
    unittest.main()
