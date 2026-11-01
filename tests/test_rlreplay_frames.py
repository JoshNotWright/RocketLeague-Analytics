"""Unit tests for the frame feature extractor using a tiny synthetic network stream."""
import unittest

from rlreplay import frames

OBJECTS = [
    "TAGame.Default__PRI_TA", "Archetypes.Car.Car_Default", "Archetypes.Ball.Ball_Default",
    "Archetypes.GameEvent.GameEvent_Soccar", "Archetypes.Teams.Team0", "Archetypes.Teams.Team1",
    "Archetypes.CarComponents.CarComponent_Boost", "cs_p.TheWorld:PersistentLevel.VehiclePickup_Boost_TA_5",
    "TAGame.RBActor_TA:ReplicatedRBState", "TAGame.CarComponent_Boost_TA:ReplicatedBoostAmount",
    "TAGame.CarComponent_TA:Vehicle", "Engine.Pawn:PlayerReplicationInfo", "Engine.PlayerReplicationInfo:PlayerName",
    "Engine.PlayerReplicationInfo:UniqueId", "Engine.PlayerReplicationInfo:Team",
    "TAGame.GameEvent_TA:ReplicatedStateName", "TAGame.VehiclePickup_TA:NewReplicatedPickupData",
    "TAGame.Car_TA:ReplicatedDemolishGoalExplosion",
]
OBJ = {name: i for i, name in enumerate(OBJECTS)}
NAMES = ["Countdown", "Active", "PostGoalScored"]

# actor ids
PRI_A, PRI_B, CAR_A, CAR_B, BALL, GAME, TEAM0, TEAM1, BOOST_A, BOOST_B, PAD = range(11)


def rb(loc, vel):
    return {"RigidBody": {"sleeping": False, "location": dict(zip("xyz", loc)),
                          "rotation": {"x": 0, "y": 0, "z": 0, "w": 1},
                          "linear_velocity": dict(zip("xyz", vel)), "angular_velocity": None}}


def upd(actor, key, attribute):
    return {"actor_id": actor, "stream_id": 0, "object_id": OBJ[key], "attribute": attribute}


def new(actor, key, loc=None):
    return {"actor_id": actor, "name_id": 0, "object_id": OBJ[key],
            "initial_trajectory": {"location": dict(zip("xyz", loc)) if loc else None, "rotation": None}}


def frame(t, dt, new_actors=(), updated=(), deleted=()):
    return {"time": t, "delta": dt, "new_actors": list(new_actors), "updated_actors": list(updated),
            "deleted_actors": list(deleted)}


def build_replay():
    setup = frame(0.0, 0.0, new_actors=[
        new(PRI_A, "TAGame.Default__PRI_TA"), new(PRI_B, "TAGame.Default__PRI_TA"),
        new(CAR_A, "Archetypes.Car.Car_Default"), new(CAR_B, "Archetypes.Car.Car_Default"),
        new(BALL, "Archetypes.Ball.Ball_Default"), new(GAME, "Archetypes.GameEvent.GameEvent_Soccar"),
        new(TEAM0, "Archetypes.Teams.Team0"), new(TEAM1, "Archetypes.Teams.Team1"),
        new(BOOST_A, "Archetypes.CarComponents.CarComponent_Boost"),
        new(BOOST_B, "Archetypes.CarComponents.CarComponent_Boost"),
        new(PAD, "cs_p.TheWorld:PersistentLevel.VehiclePickup_Boost_TA_5", loc=(3072, 4096, 73)),
    ], updated=[
        upd(PRI_A, "Engine.PlayerReplicationInfo:PlayerName", {"String": "alice"}),
        upd(PRI_B, "Engine.PlayerReplicationInfo:PlayerName", {"String": "bob"}),
        upd(PRI_A, "Engine.PlayerReplicationInfo:UniqueId", {"UniqueId": {"system_id": 1, "remote_id": {"Steam": "111"}, "local_id": 0}}),
        upd(PRI_B, "Engine.PlayerReplicationInfo:UniqueId", {"UniqueId": {"system_id": 11, "remote_id": {"Epic": "bob-epic"}, "local_id": 0}}),
        upd(PRI_A, "Engine.PlayerReplicationInfo:Team", {"ActiveActor": {"active": True, "actor": TEAM0}}),
        upd(PRI_B, "Engine.PlayerReplicationInfo:Team", {"ActiveActor": {"active": True, "actor": TEAM1}}),
        upd(CAR_A, "Engine.Pawn:PlayerReplicationInfo", {"ActiveActor": {"active": True, "actor": PRI_A}}),
        upd(CAR_B, "Engine.Pawn:PlayerReplicationInfo", {"ActiveActor": {"active": True, "actor": PRI_B}}),
        upd(BOOST_A, "TAGame.CarComponent_TA:Vehicle", {"ActiveActor": {"active": True, "actor": CAR_A}}),
        upd(BOOST_B, "TAGame.CarComponent_TA:Vehicle", {"ActiveActor": {"active": True, "actor": CAR_B}}),
        upd(BOOST_A, "TAGame.CarComponent_Boost_TA:ReplicatedBoostAmount", {"Byte": 255}),
        upd(BOOST_B, "TAGame.CarComponent_Boost_TA:ReplicatedBoostAmount", {"Byte": 25}),
        upd(GAME, "TAGame.GameEvent_TA:ReplicatedStateName", {"Int": 0}),
        upd(CAR_A, "TAGame.RBActor_TA:ReplicatedRBState", rb((0, -4000, 17), (0, 0, 0))),
        upd(CAR_B, "TAGame.RBActor_TA:ReplicatedRBState", rb((0, 4000, 17), (0, 0, 0))),
        upd(BALL, "TAGame.RBActor_TA:ReplicatedRBState", rb((0, 0, 93), (0, 0, 0))),
    ])
    # Countdown time must not count.
    countdown = frame(1.0, 1.0)
    go = frame(2.0, 1.0, updated=[upd(GAME, "TAGame.GameEvent_TA:ReplicatedStateName", {"Int": 1})])
    # 2 s of active play: alice drives supersonic toward the ball, bob sits still.
    drive = [frame(2.0 + i, 1.0, updated=[
        upd(CAR_A, "TAGame.RBActor_TA:ReplicatedRBState", rb((0, -300, 17), (0, 2300, 0))),
    ]) for i in range(1, 3)]
    # alice touches the ball at t=5: ball velocity jumps toward +y (bob is 4000 away -> not a challenge).
    touch = frame(5.0, 1.0, updated=[
        upd(BALL, "TAGame.RBActor_TA:ReplicatedRBState", rb((0, -100, 93), (0, 1500, 0))),
        # bob: 25 -> 56 (small pad class), then 56 -> 255 (big pad)
        upd(BOOST_B, "TAGame.CarComponent_Boost_TA:ReplicatedBoostAmount", {"Byte": 56}),
        upd(BOOST_B, "TAGame.CarComponent_Boost_TA:ReplicatedBoostAmount", {"Byte": 255}),
    ])
    # alice demolishes bob: bob's car is detached from his player and the event names it as victim.
    after = frame(6.5, 1.5, updated=[
        upd(BALL, "TAGame.RBActor_TA:ReplicatedRBState", rb((0, 2000, 93), (0, 1400, 0))),
        upd(CAR_B, "Engine.Pawn:PlayerReplicationInfo", {"ActiveActor": {"active": False, "actor": -1}}),
        upd(CAR_B, "TAGame.Car_TA:ReplicatedDemolishGoalExplosion", {"DemolishFx": {"attacker": CAR_A, "victim": CAR_B}}),
    ])
    # A keyframe re-announces bob's car and re-sends the demolish attribute: not a second demo.
    keyframe = frame(6.8, 0.3, new_actors=[new(CAR_B, "Archetypes.Car.Car_Default")], updated=[
        upd(CAR_B, "TAGame.Car_TA:ReplicatedDemolishGoalExplosion", {"DemolishFx": {"attacker": CAR_A, "victim": CAR_B}}),
    ])
    done = frame(7.0, 0.2, updated=[upd(GAME, "TAGame.GameEvent_TA:ReplicatedStateName", {"Int": 2})])
    return {"objects": OBJECTS, "names": NAMES,
            "network_frames": {"frames": [setup, countdown, go, *drive, touch, after, keyframe, done]}}


class NewerBuildAttributeTests(unittest.TestCase):
    """Builds since mid/late 2024 replicate boost as a struct and demolitions as DemolishExtended."""

    def test_replicated_boost_struct_and_demolish_extended(self):
        replay = build_replay()
        fr = replay["network_frames"]["frames"]
        OBJECTS_EXT = OBJECTS + ["TAGame.CarComponent_Boost_TA:ReplicatedBoost", "TAGame.Car_TA:ReplicatedDemolishExtended"]
        replay["objects"] = OBJECTS_EXT
        boost_key, demo_key = len(OBJECTS), len(OBJECTS) + 1
        # replace alice's initial byte boost with the struct form (same value) ...
        setup = fr[0]
        setup["updated_actors"] = [u for u in setup["updated_actors"]
                                   if not (u["actor_id"] == BOOST_A and u["object_id"] == OBJ["TAGame.CarComponent_Boost_TA:ReplicatedBoostAmount"])]
        setup["updated_actors"].append({"actor_id": BOOST_A, "stream_id": 0, "object_id": boost_key,
                                        "attribute": {"ReplicatedBoost": {"grant_count": 0, "boost_amount": 255, "unused1": 0, "unused2": 0}}})
        # ... and the demolition with the extended form naming alice's PRI directly.
        after = fr[6]
        after["updated_actors"] = [u for u in after["updated_actors"] if "DemolishFx" not in u["attribute"]]
        after["updated_actors"].append({"actor_id": 99, "stream_id": 0, "object_id": demo_key, "attribute": {"DemolishExtended": {
            "attacker_pri": {"active": True, "actor": PRI_A}, "attacker": {"active": True, "actor": CAR_A},
            "victim": {"active": True, "actor": CAR_B}, "self_demolish": False}}})
        rows = {r["frame_player_name"]: r for r in frames.extract(replay, replay_id="t")}
        self.assertAlmostEqual(rows["alice"]["avg_boost"], 100.0)
        self.assertEqual(rows["alice"]["demos_inflicted"], 1)
        self.assertEqual(rows["bob"]["demos_taken"], 1)


class FrameFeatureTests(unittest.TestCase):
    def setUp(self):
        self.rows = {r["frame_player_name"]: r for r in frames.extract(build_replay(), replay_id="t")}

    def test_players_teams_and_ids(self):
        self.assertEqual(set(self.rows), {"alice", "bob"})
        self.assertEqual(self.rows["alice"]["frame_team"], 0)
        self.assertEqual(self.rows["bob"]["frame_team"], 1)
        self.assertEqual((self.rows["alice"]["frame_platform"], self.rows["alice"]["frame_uid"]), ("Steam", "111"))
        self.assertEqual((self.rows["bob"]["frame_platform"], self.rows["bob"]["frame_uid"]), ("Epic", "bob-epic"))

    def test_only_active_time_counts(self):
        # active frames: t=3 (dt1), 4 (dt1), 5 (dt1), 6.5 (dt1.5), 6.8 (dt0.3), 7 (dt0.2) -> 5.0 s
        self.assertAlmostEqual(self.rows["alice"]["active_time"], 5.0)
        self.assertAlmostEqual(self.rows["alice"]["match_active_time"], 5.0)
        self.assertEqual(self.rows["alice"]["match_rounds"], 1)

    def test_speed_boost_and_position_fractions(self):
        alice, bob = self.rows["alice"], self.rows["bob"]
        self.assertAlmostEqual(alice["supersonic_frac"], 1.0)
        self.assertAlmostEqual(bob["supersonic_frac"], 0.0)
        self.assertAlmostEqual(alice["avg_boost"], 100.0)
        # bob is below 20 boost until his pickups at t=5 and his car is gone after t=6.5; each
        # interval uses the state at its end, so 2 of his 3 active seconds count as low boost.
        self.assertAlmostEqual(bob["low_boost_frac"], 2 / 3)
        self.assertAlmostEqual(alice["behind_ball_frac"], 1.0)  # blue at -y, ball ahead
        self.assertAlmostEqual(bob["behind_ball_frac"], 1.0)    # orange at +y, ball below
        self.assertIsNone(alice["avg_dist_mate"])  # 1v1

    def test_touch_kickoff_demo_pad(self):
        alice, bob = self.rows["alice"], self.rows["bob"]
        self.assertEqual(alice["touches"], 1)
        self.assertEqual(bob["touches"], 0)
        self.assertEqual(alice["challenges"], 0)
        self.assertEqual(alice["kickoffs"], 1)
        self.assertAlmostEqual(alice["kickoff_time_to_ball"], 3.0)
        self.assertAlmostEqual(alice["kickoff_win_rate"], 1.0)
        self.assertEqual(alice["demos_inflicted"], 1)
        self.assertEqual(bob["demos_taken"], 1)
        self.assertEqual((alice["big_pads"], alice["small_pads"]), (0, 0))
        self.assertEqual((bob["big_pads"], bob["small_pads"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
