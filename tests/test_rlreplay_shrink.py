import random
import unittest

try:
    import polars as pl
except ImportError:  # pragma: no cover
    pl = None

if pl is not None:
    from rlreplay import shrink


def games(player, true_mean, sd, n, rng):
    return [{"player_id": player, "stat": rng.gauss(true_mean, sd)} for _ in range(n)]


@unittest.skipIf(pl is None, "polars not installed")
class ShrinkTests(unittest.TestCase):
    def test_pure_noise_is_shrunk_to_population_mean(self):
        rng = random.Random(1)
        rows = []
        for p in range(40):
            rows += games(f"p{p}", 0.0, 1.0, 30, rng)  # every player has the same true mean
        out = shrink.shrink_means(pl.DataFrame(rows), "stat")
        self.assertLess(float(out["shrink_factor"].max()), 0.35)
        self.assertLess(float(out["shrunk_mean"].std()), float(out["raw_mean"].std()) * 0.5)

    def test_real_differences_survive_and_more_games_shrink_less(self):
        rng = random.Random(2)
        rows = []
        for p in range(40):
            rows += games(f"p{p}", rng.gauss(0.0, 2.0), 1.0, 10 if p % 2 else 60, rng)
        out = shrink.shrink_means(pl.DataFrame(rows), "stat")
        self.assertGreater(float(out["shrink_factor"].min()), 0.7)
        few = out.filter(pl.col("games") == 10)["shrink_factor"].mean()
        many = out.filter(pl.col("games") == 60)["shrink_factor"].mean()
        self.assertLess(few, many)
        # ordering by shrunk mean is close to ordering by raw mean when signal is strong
        self.assertGreater(float(out.select(pl.corr("raw_mean", "shrunk_mean")).item()), 0.99)

    def test_table_and_min_games(self):
        rng = random.Random(3)
        rows = games("a", 1.0, 1.0, 5, rng) + games("b", 2.0, 1.0, 5, rng) + games("c", 3.0, 1.0, 1, rng)
        for r in rows:
            r["other"] = r["stat"] * 2
        out = shrink.shrink_table(pl.DataFrame(rows), ["stat", "other", "missing"], min_games=2)
        self.assertEqual(set(out["statistic"].to_list()), {"stat", "other"})
        self.assertNotIn("c", out["player_id"].to_list())


if __name__ == "__main__":
    unittest.main()
