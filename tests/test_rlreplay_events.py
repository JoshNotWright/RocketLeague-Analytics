import unittest

from rlreplay import events
from tests.test_rlreplay_frames import build_replay


class ShotGeometryTests(unittest.TestCase):
    def test_straight_shot_on_target(self):
        # 1000 uu from the goal line, rolling straight at 2000 uu/s: crosses in ~0.46 s at ground level
        self.assertTrue(events.is_shot((0.0, 4120.0, 93.0), (0.0, 2000.0, 0.0)))

    def test_wide_and_high_misses(self):
        self.assertFalse(events.is_shot((0.0, 4120.0, 93.0), (2500.0, 2000.0, 0.0)))   # wide
        self.assertFalse(events.is_shot((0.0, 4120.0, 93.0), (0.0, 2000.0, 3000.0)))   # over the bar
        self.assertFalse(events.is_shot((0.0, 4120.0, 93.0), (0.0, -2000.0, 0.0)))     # wrong way

    def test_slow_ball_far_out_is_not_a_shot(self):
        # 5000 uu away at 500 uu/s takes 10 s, beyond the horizon
        self.assertFalse(events.is_shot((0.0, 120.0, 93.0), (0.0, 500.0, 0.0)))


class TouchExtractionTests(unittest.TestCase):
    def test_touch_rows_and_labels(self):
        replay = build_replay()
        # one header goal for team 0 at the last frame (index 8, t=7.0), scored by alice
        replay["properties"] = {"TeamSize": 1, "Goals": [{"frame": 8, "PlayerName": "alice", "PlayerTeam": 0}]}
        rows = events.extract_touches(replay, replay_id="t")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["player_name"], "alice")
        self.assertEqual(row["player_id"], "steam:111")
        self.assertEqual(row["team"], 0)
        # attacking frame for team 0 is unchanged: ball just below the half line heading +y
        self.assertAlmostEqual(row["ball_y"], -100.0)
        self.assertAlmostEqual(row["post_vy"], 1500.0)
        self.assertEqual(row["n_opps"], 1)
        self.assertEqual(row["opps_between"], 1)        # bob at y=+4000, inside the goal cone
        self.assertEqual(row["score_diff"], 0)           # goal is scored after the touch
        self.assertTrue(row["scored"])                   # team 0 goal with no later touch
        self.assertTrue(row["team_goal_within_5s"])      # goal at t=7.0, touch at t=5.0
        self.assertAlmostEqual(row["time_to_next_goal"], 2.0)
        self.assertIsNone(row["next_touch_same_team"])


if __name__ == "__main__":
    unittest.main()
