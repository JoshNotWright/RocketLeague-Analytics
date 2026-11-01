"""Leakage and join tests for the modelling table."""
import unittest
from datetime import datetime, timedelta, timezone

try:
    import polars as pl
except ImportError:  # pragma: no cover - v2 extras not installed
    pl = None

if pl is not None:
    from rlreplay import features

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)


def games(player, n, team="blue", start=T0, rank=88):
    """n consecutive games for one player, one day apart, score == game index."""
    rows = []
    for i in range(n):
        rows.append({"replay_id": f"{player}-{i}", "player_id": player, "player_name": player.upper(), "team": team,
                     "slot": 0, "played_at": start + timedelta(days=i), "playlist": "ranked-doubles",
                     "rank_points": rank + (1 if i >= n - 1 else 0), "team_goals": 1 + (i % 2), "opp_goals": 1,
                     "core_score": float(i), "won": i % 2 == 1, "goal_diff": i % 2})
    return rows


@unittest.skipIf(pl is None, "polars not installed")
class RollingTests(unittest.TestCase):
    def test_rolling_uses_only_earlier_games(self):
        df = pl.DataFrame(games("a", 6) + games("b", 3, start=T0 + timedelta(days=100)))
        out = features.add_rolling(df, ["core_score"], window=3).sort("player_id", "played_at")
        a = out.filter(pl.col("player_id") == "a")["r3_core_score"].to_list()
        # game 0 has no history; game 1 sees {0}; game 3 sees {0,1,2}; game 5 sees {2,3,4}
        self.assertIsNone(a[0])
        self.assertEqual(a[1], 0.0)
        self.assertEqual(a[3], 1.0)
        self.assertEqual(a[5], 3.0)
        b = out.filter(pl.col("player_id") == "b")["r3_core_score"].to_list()
        self.assertIsNone(b[0])  # player b's history does not borrow from player a

    def test_history_counts_and_future_rank(self):
        df = pl.DataFrame(games("a", 5))
        out = features.add_future_rank(features.add_history_counts(df)).sort("played_at")
        self.assertEqual(out["n_prev_games"].to_list(), [0, 1, 2, 3, 4])
        self.assertIsNone(out["prev_win_rate"][0])
        self.assertAlmostEqual(out["prev_win_rate"][2], 0.5)
        # 30-day horizon: no game exists 30 days after any of these, so rank_change is null
        self.assertTrue(out["rank_change"].is_null().all())
        near = features.add_future_rank(features.add_history_counts(df), days=2).sort("played_at")
        # game 0 (rank 88) -> first game >= day 2 is game 2 (rank 88): change 0; game 2 -> game 4 (rank 89): +1
        self.assertEqual(near["rank_change"][0], 0)
        self.assertEqual(near["rank_change"][2], 1)
        self.assertIsNone(near["rank_change"][4])

    def test_game_context_uses_opponent_team(self):
        rows = games("a", 1, team="blue", rank=80) + games("b", 1, team="orange", rank=84)
        rows[1]["replay_id"] = rows[0]["replay_id"]
        out = features.add_game_context(pl.DataFrame(rows)).sort("player_id")
        # games() bumps the last game's rank by one, so the single games carry 81 and 85.
        self.assertEqual(out["opp_rank_mean"].to_list(), [85.0, 81.0])
        self.assertEqual(out["rank_diff"].to_list(), [-4.0, 4.0])


@unittest.skipIf(pl is None, "polars not installed")
class JoinFramesTests(unittest.TestCase):
    def test_id_join_then_name_fallback(self):
        meta = pl.DataFrame(games("steam:1", 1) + games("ps4:Name", 1))
        meta = meta.with_columns(pl.Series("replay_id", ["r", "r"]), pl.Series("player_name", ["One", "Name"]))
        frames = pl.DataFrame({
            "replay_id": ["r", "r"],
            "player_id": ["steam:1", "playstation:123456"],  # console id does not match metadata
            "frame_player_name": ["One", "Name"],
            "active_time": [300.0, 290.0],
            "touch_rate": [10.0, 12.0],
        })
        out = features.join_frames(meta, frames).sort("player_id")
        self.assertEqual(out["active_time"].to_list(), [290.0, 300.0])
        self.assertEqual(out["touch_rate"].to_list(), [12.0, 10.0])
        self.assertEqual(out.height, 2)


if __name__ == "__main__":
    unittest.main()
