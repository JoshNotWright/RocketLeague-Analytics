import unittest

try:
    import polars as pl
except ImportError:  # pragma: no cover
    pl = None

if pl is not None:
    from rlreplay import score

REFERENCE = {"metrics": {
    "xg": {"sorted": [1.0, 1.2, 1.4, 1.6, 1.8], "quantiles": {"0.5": 1.4},
           "prior": {"mean": 1.4, "between_var": 0.04, "within_var": 1.0}},
    "goals_above_xg": {"sorted": [-0.1, 0.0, 0.1], "quantiles": {"0.5": 0.0},
                       "prior": {"mean": 0.0, "between_var": 0.0, "within_var": 1.0}},
}}


@unittest.skipIf(pl is None, "polars not installed")
class ScoreHelperTests(unittest.TestCase):
    def test_percentile(self):
        self.assertEqual(score.percentile(REFERENCE, "xg", 1.5), 60.0)
        self.assertEqual(score.percentile(REFERENCE, "xg", 0.5), 0.0)
        self.assertEqual(score.percentile(REFERENCE, "xg", 5.0), 100.0)
        self.assertIsNone(score.percentile(REFERENCE, "unknown", 1.0))

    def test_shrink_with_prior(self):
        # xg: between 0.04, within 1.0 -> after 25 games factor = 0.04 / (0.04 + 0.04) = 0.5
        shrunk, factor = score.shrink_with_prior(REFERENCE, "xg", 2.4, 25)
        self.assertAlmostEqual(factor, 0.5)
        self.assertAlmostEqual(shrunk, 1.9)
        # a statistic with no between-player variance is shrunk fully to the population mean
        shrunk, factor = score.shrink_with_prior(REFERENCE, "goals_above_xg", 0.8, 100)
        self.assertEqual(factor, 0.0)
        self.assertEqual(shrunk, 0.0)
        # unknown metric passes the raw value through
        self.assertEqual(score.shrink_with_prior(REFERENCE, "nope", 3.0, 10), (3.0, None))


if __name__ == "__main__":
    unittest.main()
