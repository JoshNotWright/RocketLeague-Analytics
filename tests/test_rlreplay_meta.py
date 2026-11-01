import unittest

from rlreplay import meta
from rlreplay.kaggle import file_url, metadata_name, replay_name


def record(rid="r1", date="2024-05-10T15:46:00Z"):
    def player(name, pid, goals, rank):
        return {"name": name, "id": {"platform": "steam", "id": pid}, "rank": rank,
                "stats": {"core": {"goals": goals, "shots": 3, "score": 400},
                          "boost": {"bpm": 400, "avg_amount": 50.5},
                          "movement": {"avg_speed": 1500},
                          "positioning": {"percent_behind_ball": 70.0},
                          "demo": {"inflicted": 1, "taken": 0}}}
    return {"id": rid, "score": 0.6, "gameplay_duration": 300.0, "players": ["a", "b", "c", "d"],
            "data": {"id": rid, "match_guid": "G", "date": date, "playlist_id": "ranked-doubles", "team_size": 2,
                     "season": 14, "match_type": "Online", "map_code": "cs_p", "overtime": False, "duration": 330,
                     "blue": {"players": [player("a", "1", 2, {"id": "grand-champion-1", "tier": 19, "division": 3}),
                                          player("b", "2", 1, {"id": "supersonic-legend", "tier": 22})],
                              "stats": {"core": {"goals": 3}}},
                     "orange": {"players": [player("c", "3", 1, {"tier": 20, "division": 1}),
                                            player("d", "4", 0, None)],
                                "stats": {"core": {"goals": 1}}}}}


class MetaTests(unittest.TestCase):
    def test_rank_points_monotone(self):
        self.assertEqual(meta.rank_points({"tier": 19, "division": 1}), 76)
        self.assertEqual(meta.rank_points({"tier": 21, "division": 4}), 87)
        self.assertEqual(meta.rank_points({"tier": 22}), 88)
        self.assertIsNone(meta.rank_points(None))
        self.assertIsNone(meta.rank_points({}))

    def test_player_rows(self):
        rows = meta.player_rows(record())
        self.assertEqual(len(rows), 4)
        a = rows[0]
        self.assertEqual(a["player_id"], "steam:1")
        self.assertEqual(a["team"], "blue")
        self.assertEqual((a["team_goals"], a["opp_goals"]), (3, 1))
        self.assertEqual(a["rank_points"], 78)
        self.assertEqual(a["core_goals"], 2)
        self.assertEqual(a["boost_avg_amount"], 50.5)
        self.assertEqual(a["positioning_percent_behind_ball"], 70.0)
        self.assertIsNone(a["positioning_avg_distance_to_ball"])  # missing stat stays null, never zero
        self.assertEqual(a["played_at"].isoformat(), "2024-05-10T15:46:00+00:00")
        d = rows[3]
        self.assertIsNone(d["rank_points"])
        self.assertEqual((d["team_goals"], d["opp_goals"]), (1, 3))

    def test_kaggle_names(self):
        self.assertEqual(replay_name("2v2", "abc"), "high_level/2v2/abc.replay")
        self.assertEqual(metadata_name("1v1"), "high_level/1v1/metadata.json")
        self.assertIn("file_name=high_level%2F2v2%2Fabc.replay", file_url(replay_name("2v2", "abc")))
        with self.assertRaises(ValueError):
            replay_name("4v4", "x")


if __name__ == "__main__":
    unittest.main()
