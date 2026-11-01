"""The decoded-array path must reproduce the stream-walking path exactly."""
import tempfile
import unittest
from pathlib import Path

try:
    import polars as pl
except ImportError:  # pragma: no cover
    pl = None

if pl is not None:
    from rlreplay import decisions, decisions_fast, decode
    from tests.test_rlreplay_decisions import doubles_replay


@unittest.skipIf(pl is None, "polars not installed")
class DecodeParityTests(unittest.TestCase):
    def test_roundtrip_and_parity_with_build_rows(self):
        replay = doubles_replay()
        dec = decode.decode_json(replay, replay_id="t")
        self.assertEqual(dec["cars"].shape[1], 4)
        self.assertEqual({p["name"] for p in dec["players"]}, {"alice", "bob", "carol", "dave"})
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "t.npz"
            decode.save(dec, path)
            loaded = decode.load(path)
        self.assertEqual(loaded["replay_id"], "t")
        self.assertEqual(loaded["header"]["team_size"], 2)
        fast = decisions_fast.rows_from_decoded(loaded, hz=1.0, horizon=1.0)
        slow = pl.DataFrame(decisions.build_rows(replay, replay_id="t", hz=1.0, horizon=1.0))
        self.assertEqual(fast.height, slow.height)
        joined = (slow.with_columns(pl.col("time").round(4))
                      .join(fast.with_columns(pl.col("time").round(4)), on=["time", "player_id"], suffix="_f"))
        self.assertEqual(joined.height, slow.height)
        for col in ("action", "decision_point", "team", "me_closer_than_mate"):
            self.assertTrue((joined[col] == joined[col + "_f"]).all(), col)
        for col in ("me_x", "me_vy", "mate_x", "opp1_dist_ball", "ball_dist_goal", "closing_speed"):
            diff = (joined[col].cast(pl.Float64) - joined[col + "_f"].cast(pl.Float64)).abs().max()
            self.assertLess(diff, 1e-2, col)


if __name__ == "__main__":
    unittest.main()
