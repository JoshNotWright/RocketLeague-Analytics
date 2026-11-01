"""Rank bands, the game_rank feature and reference selection by band."""
import unittest

try:
    import polars as pl
except ImportError:  # pragma: no cover
    pl = None

if pl is not None:
    from rlreplay import ranks, score, xg


@unittest.skipIf(pl is None, "polars not installed")
class RankTests(unittest.TestCase):
    def test_band_of_covers_the_scale(self):
        self.assertEqual(ranks.band_of(51), "platinum_or_below")
        self.assertEqual(ranks.band_of(52), "diamond")       # Diamond I div 1
        self.assertEqual(ranks.band_of(63.9), "diamond")
        self.assertEqual(ranks.band_of(64), "champion")      # Champion I div 1
        self.assertEqual(ranks.band_of(76), "gc")            # GC1 div 1
        self.assertEqual(ranks.band_of(88), "gc")            # SSL
        self.assertIsNone(ranks.band_of(None))
        self.assertIsNone(ranks.band_of(99))

    def test_game_rank_is_the_mean_over_ranked_players(self):
        table = pl.DataFrame({"replay_id": ["r1", "r1", "r1", "r1", "r2", "r2"],
                              "rank_points": [52, 56, None, 60, 76, 80]})
        got = ranks.game_ranks(table).sort("replay_id")
        self.assertEqual(got["game_rank"].to_list(), [56.0, 78.0])
        self.assertEqual(got["rank_band"].to_list(), ["diamond", "gc"])
        touches = pl.DataFrame({"replay_id": ["r2", "r1", "r3"], "x": [1, 2, 3]})
        joined = ranks.attach_game_rank(touches, table)
        self.assertEqual(joined["game_rank"].to_list(), [78.0, 56.0, None])

    def test_attach_without_ranks_adds_null_columns(self):
        touches = pl.DataFrame({"replay_id": ["r1"], "x": [1]})
        joined = ranks.attach_game_rank(touches, pl.DataFrame({"replay_id": ["r1"], "played_at": ["2025-01-01"]}))
        self.assertIn("game_rank", joined.columns)
        self.assertIsNone(joined["game_rank"][0])

    def test_xg_attach_dates_carries_game_rank(self):
        self.assertIn("game_rank", xg.STATE_FEATURES)
        table = pl.DataFrame({"replay_id": ["r1", "r1"], "played_at": ["d", "d"], "rank_points": [64, 68]})
        touches = pl.DataFrame({"replay_id": ["r1"], "ball_x": [0.0]})
        got = xg.attach_dates(touches, table)
        self.assertEqual(got["game_rank"][0], 66.0)
        self.assertEqual(got["played_at"][0], "d")

    def test_pick_reference_by_band_with_fallback(self):
        refs = [{"source": "gcplus", "band": None}, {"source": "diamond", "band": "diamond"}, {"source": "gc", "band": "gc"}]
        self.assertEqual(score.pick_reference(refs, 58.0)["source"], "diamond")
        self.assertEqual(score.pick_reference(refs, 80.0)["source"], "gc")
        self.assertEqual(score.pick_reference(refs, 70.0)["source"], "gcplus")  # no champion reference: fallback
        self.assertEqual(score.pick_reference(refs, None)["source"], "gcplus")
        only_bands = [{"source": "diamond", "band": "diamond"}]
        self.assertEqual(score.pick_reference(only_bands, None)["source"], "diamond")


if __name__ == "__main__":
    unittest.main()
