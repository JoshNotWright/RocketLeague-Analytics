"""Rank-conditioned policy fit: rank joins, counterfactual GC scoring and the report shape."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import lightgbm  # noqa: F401
    import polars as pl
except ImportError:  # pragma: no cover
    pl = None

if pl is not None:
    from rlreplay import decisions
    from tests.test_rlreplay_decisions import doubles_replay


def _dataset(tmp: Path, n_replays: int = 10):  # ids 8 (Diamond) and 9 (GC) land in the test window
    """The fixture replay copied under several ids and dates, with Diamond and GC players."""
    base = decisions.build_rows(doubles_replay(), replay_id="t", hz=1.0, horizon=1.0)
    frames_dir = tmp / "frames"
    frames_dir.mkdir()
    table_rows = []
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    for i in range(n_replays):
        rid = f"r{i}"
        played = start + timedelta(days=30 * i)
        rows = [dict(r, replay_id=rid) for r in base]
        pl.DataFrame(rows).write_parquet(frames_dir / f"{rid}.parquet")
        for r in rows:
            points = 58 if i % 2 == 0 else 80  # alternate Diamond and GC games
            table_rows.append({"replay_id": rid, "player_id": r["player_id"], "played_at": played, "rank_points": points})
    table = pl.DataFrame(table_rows).unique(subset=["replay_id", "player_id"])
    return frames_dir, table


@unittest.skipIf(pl is None, "polars or lightgbm not installed")
class RankPolicyTests(unittest.TestCase):
    def test_attach_ranks(self):
        table = pl.DataFrame({"replay_id": ["a", "a"], "player_id": ["p1", "p2"], "played_at": ["d", "d"], "rank_points": [52, 60]})
        rows = pl.DataFrame({"replay_id": ["a", "a", "b"], "player_id": ["p1", "p2", "p1"]}).lazy()
        got = decisions.attach_ranks(rows, table).collect().sort("replay_id", "player_id")
        self.assertEqual(got["me_rank"].to_list(), [52.0, 60.0, None])
        self.assertEqual(got["game_rank"].to_list(), [56.0, 56.0, None])

    def test_fit_with_ranks_writes_counterfactual(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            frames_dir, table = _dataset(tmp)
            out = tmp / "out"
            report = decisions.fit_policy(frames_dir, table, out, train_end="2025-05-01", valid_end="2025-08-01",
                                          sample_rows=10_000, rank_features=True, gc_rank=80.0)
            per_game = pl.read_parquet(out / "player_game_decisions.parquet")
            players = pl.read_parquet(out / "player_decisions.parquet")
        self.assertTrue(report["rank_features"])
        self.assertIsNotNone(report["counterfactual"])
        self.assertEqual(report["counterfactual"]["gc_rank"], 80.0)
        for col in ("gc_gap", "retreat_where_gc_challenges", "challenge_where_gc_would_not", "me_rank"):
            self.assertIn(col, per_game.columns)
        self.assertIn("gc_gap", report["reliability"])
        self.assertIn("Counterfactual", report["markdown"])
        # a GC game scored "as GC" is the same policy, so its gap is exactly zero
        gc_games = per_game.filter(pl.col("me_rank") == 80.0)
        self.assertTrue(gc_games.height > 0)
        self.assertTrue((gc_games["gc_gap"].abs() < 1e-9).all())
        self.assertEqual(players.height, 0)  # nobody has 10 held-out games in the fixture

    def test_fit_without_rank_table_falls_back(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            frames_dir, table = _dataset(tmp)
            report = decisions.fit_policy(frames_dir, table.drop("rank_points"), tmp / "out", train_end="2025-05-01",
                                          valid_end="2025-08-01", sample_rows=10_000, rank_features=True)
        self.assertFalse(report["rank_features"])
        self.assertIsNone(report["counterfactual"])


if __name__ == "__main__":
    unittest.main()
