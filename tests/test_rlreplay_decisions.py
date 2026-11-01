import unittest

from rlreplay import decisions
from tests.test_rlreplay_frames import (CAR_A, CAR_B, GAME, OBJECTS, NAMES, build_replay, frame, new, rb, upd)


def doubles_replay():
    """Extend the 1v1 fixture to 2v2 with two more cars, then play 4 s of active time."""
    replay = build_replay()
    replay["properties"] = {"TeamSize": 2}
    PRI_C, PRI_D, CAR_C, CAR_D = 20, 21, 22, 23
    setup = replay["network_frames"]["frames"][0]
    setup["new_actors"] += [new(PRI_C, "TAGame.Default__PRI_TA"), new(PRI_D, "TAGame.Default__PRI_TA"),
                            new(CAR_C, "Archetypes.Car.Car_Default"), new(CAR_D, "Archetypes.Car.Car_Default")]
    setup["updated_actors"] += [
        upd(PRI_C, "Engine.PlayerReplicationInfo:PlayerName", {"String": "carol"}),
        upd(PRI_D, "Engine.PlayerReplicationInfo:PlayerName", {"String": "dave"}),
        upd(PRI_C, "Engine.PlayerReplicationInfo:UniqueId", {"UniqueId": {"system_id": 1, "remote_id": {"Steam": "333"}, "local_id": 0}}),
        upd(PRI_D, "Engine.PlayerReplicationInfo:UniqueId", {"UniqueId": {"system_id": 1, "remote_id": {"Steam": "444"}, "local_id": 0}}),
        upd(PRI_C, "Engine.PlayerReplicationInfo:Team", {"ActiveActor": {"active": True, "actor": 6}}),   # TEAM0
        upd(PRI_D, "Engine.PlayerReplicationInfo:Team", {"ActiveActor": {"active": True, "actor": 7}}),   # TEAM1
        upd(CAR_C, "Engine.Pawn:PlayerReplicationInfo", {"ActiveActor": {"active": True, "actor": PRI_C}}),
        upd(CAR_D, "Engine.Pawn:PlayerReplicationInfo", {"ActiveActor": {"active": True, "actor": PRI_D}}),
        upd(CAR_C, "TAGame.RBActor_TA:ReplicatedRBState", rb((1000, -4500, 17), (0, 0, 0))),
        upd(CAR_D, "TAGame.RBActor_TA:ReplicatedRBState", rb((-1000, 4500, 17), (0, 0, 0))),
    ]
    # Replace everything after the "go" frame with a clean 4-second active stretch at 1 frame/s:
    # alice drives +y to the ball (challenge), bob retreats toward his goal (+y for orange = retreat),
    # carol and dave hold still.
    go = replay["network_frames"]["frames"][2]
    frames = [setup, replay["network_frames"]["frames"][1], go]
    for i in range(1, 6):
        t = 2.0 + i
        updates = [upd(CAR_A, "TAGame.RBActor_TA:ReplicatedRBState", rb((0, -4000 + 800 * i, 17), (0, 800, 0))),
                   upd(CAR_B, "TAGame.RBActor_TA:ReplicatedRBState", rb((0, 4000 + 1200 * i, 17), (0, 1200, 0)))]
        if i == 5:  # alice reaches the ball: ball velocity jumps
            updates.append(upd(2 if False else 4, "TAGame.RBActor_TA:ReplicatedRBState", rb((0, 0, 93), (0, 1200, 0))))
        frames.append(frame(t, 1.0, updated=updates))
    replay["network_frames"]["frames"] = frames
    return replay


class DecisionRowTests(unittest.TestCase):
    def test_rows_only_for_doubles(self):
        replay = build_replay()
        replay["properties"] = {"TeamSize": 1}
        self.assertEqual(decisions.build_rows(replay, replay_id="t"), [])

    def test_state_and_labels(self):
        rows = decisions.build_rows(doubles_replay(), replay_id="t", hz=1.0, horizon=1.0)
        by = {}
        for r in rows:
            by.setdefault(r["player_name"], []).append(r)
        self.assertEqual(set(by), {"alice", "bob", "carol", "dave"})
        first_alice = by["alice"][0]
        # attacking frame for blue is unchanged; mate is carol, opponents sorted by distance to ball
        self.assertEqual(first_alice["team"], 0)
        self.assertEqual(first_alice["mate_x"], 1000.0)
        self.assertLessEqual(first_alice["opp1_dist_ball"], first_alice["opp2_dist_ball"])
        self.assertIn("me_boost", first_alice)
        self.assertEqual(set(decisions.ACTIONS), {"challenge", "big_pad", "retreat", "advance", "hold"})
        # bob (orange) moves toward +y, which is toward his own goal in his attacking frame -> retreat
        self.assertEqual(by["bob"][0]["action"], "retreat")
        # carol never moves -> hold
        self.assertEqual({r["action"] for r in by["carol"]}, {"hold"})
        self.assertTrue(all(r["action_id"] == decisions.ACTIONS.index(r["action"]) for r in rows))


if __name__ == "__main__":
    unittest.main()
