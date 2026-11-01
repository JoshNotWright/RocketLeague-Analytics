import unittest
from datetime import datetime, timezone

try:
    import polars as pl
except ImportError:  # pragma: no cover
    pl = None

if pl is not None:
    from rlreplay import value

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)


def touch(time, team, player, *, threat, against, shot=False, scored=False, failed_save=False,
          next_goal_team=None, t_goal=None, t_next=None, tsk=None, goalie="g"):
    return {"replay_id": "r", "played_at": T0, "out_of_time": False, "time": time, "team": team,
            "player_id": player, "player_name": player, "threat": threat, "threat_against": against,
            "touch_xg": 0.3 if shot else 0.0, "shot": shot, "scored": scored, "failed_save": failed_save,
            "next_goal_team": next_goal_team, "time_to_next_goal": t_goal, "time_to_next_touch": t_next,
            "time_since_kickoff": tsk if tsk is not None else time, "opp_goalie_id": goalie}


@unittest.skipIf(pl is None, "polars not installed")
class ValueAddedTests(unittest.TestCase):
    def test_value_chain(self):
        rows = [
            # A touches: net value 0.1; next touch is by B with net value 0.3 -> after = -0.3
            touch(1.0, 0, "a", threat=0.2, against=0.1, t_next=1.0),
            # B touches: net 0.3; next touch by B teammate with net 0.5 -> after = +0.5
            touch(2.0, 1, "b", threat=0.4, against=0.1, t_next=1.0),
            # B teammate shoots and scores before any other touch -> after = +1
            touch(3.0, 1, "c", threat=0.6, against=0.1, shot=True, scored=True, next_goal_team=True, t_goal=0.5, t_next=5.0),
            # after kickoff: A touches, no next touch -> after = 0
            touch(8.0, 0, "a", threat=0.1, against=0.1, tsk=0.5),
        ]
        out = value.add_value(pl.DataFrame(rows)).sort("time")
        self.assertAlmostEqual(out["v_after"][0], -0.3)
        self.assertAlmostEqual(out["value_added"][0], -0.3 - 0.1)
        self.assertAlmostEqual(out["v_after"][1], 0.5)
        self.assertAlmostEqual(out["v_after"][2], 1.0)
        self.assertAlmostEqual(out["value_added"][2], 1.0 - 0.5)
        self.assertAlmostEqual(out["v_after"][3], 0.0)

    def test_kickoff_between_touches_resets_value(self):
        rows = [touch(1.0, 0, "a", threat=0.5, against=0.0, t_next=10.0),
                touch(11.0, 1, "b", threat=0.9, against=0.0, tsk=2.0)]  # kickoff 2 s before this touch
        out = value.add_value(pl.DataFrame(rows)).sort("time")
        self.assertAlmostEqual(out["v_after"][0], 0.0)


@unittest.skipIf(pl is None, "polars not installed")
class SaveTests(unittest.TestCase):
    def test_saved_conceded_and_missed_shots(self):
        rows = [
            touch(1.0, 0, "a", threat=0.3, against=0.1, shot=True, t_next=1.0),            # saved by d
            touch(2.0, 1, "d", threat=0.1, against=0.3, t_next=8.0),
            touch(10.0, 0, "a", threat=0.3, against=0.1, shot=True, scored=True, next_goal_team=True, t_goal=0.5, t_next=0.3),
            touch(10.3, 1, "e", threat=0.1, against=0.3, failed_save=True, t_next=10.0),  # failed save by e
            touch(20.3, 0, "b", threat=0.3, against=0.1, shot=True, scored=True, next_goal_team=True, t_goal=0.5, t_next=15.0),  # clean goal: goalie g
            touch(35.3, 0, "b", threat=0.3, against=0.1, shot=True, t_next=6.0),           # missed: no defender touch in 3 s
            touch(41.3, 1, "d", threat=0.1, against=0.3),
        ]
        faced = value.saves(pl.DataFrame(rows)).sort("time")
        self.assertEqual(faced["defender_id"].to_list(), ["d", "e", "g"])
        self.assertEqual(faced["conceded"].to_list(), [False, True, True])
        self.assertAlmostEqual(faced["gsax"][0], 0.3)
        self.assertAlmostEqual(faced["gsax"][1], 0.3 - 1.0)


if __name__ == "__main__":
    unittest.main()
