"""The Rust decoder (tools/rldecode.exe) must reproduce the Python decode path bit for bit.

Skipped unless the binary and the real 2v2 data are present. Five real replays are decoded
with the binary and compared against the cached Python-path output in data/decoded/2v2.

Tolerance notes: every array is expected to match exactly, including the float arrays.
times/delta/round_start are float64 in both paths; the Rust side reproduces Python's value
(the float64 parse of rrrocket's shortest f32 decimal, ryu tie-breaking included) rather
than widening the f32 directly, and ball/cars are float32 in both. The allclose check with
atol 1e-3 is a documented fallback only; it is not expected to be needed.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np

try:
    import polars as pl
except ImportError:  # pragma: no cover
    pl = None

from rlreplay import decode

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "tools" / "rldecode.exe"
RAW = ROOT / "data" / "raw" / "2v2"
DECODED = ROOT / "data" / "decoded" / "2v2"
ARRAYS = ("times", "delta", "active", "round_start", "seconds_remaining", "ball", "ball_hit", "cars")
META = ("replay_id", "players", "goals", "demos", "header")


def _sample_ids(n=5):
    if not (RAW.is_dir() and DECODED.is_dir()):
        return []
    ids = sorted(p.stem for p in DECODED.glob("*.npz") if (RAW / f"{p.stem}.replay").exists())
    if len(ids) < n:
        return ids
    step = max(1, len(ids) // n)
    return ids[::step][:n]


@unittest.skipUnless(EXE.exists(), "tools/rldecode.exe not built")
class RustDecodeParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ids = _sample_ids()
        if not cls.ids:
            raise unittest.SkipTest("no decoded 2v2 replays to compare against")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.rust = {}
        cls.py = {}
        for rid in cls.ids:
            completed = subprocess.run([str(EXE), str(RAW / f"{rid}.replay"), cls.tmp.name, "--replay-id", rid],
                                       capture_output=True, text=True)
            assert completed.returncode == 0, completed.stderr
            cls.rust[rid] = decode.load(Path(cls.tmp.name) / f"{rid}.npz")
            cls.py[rid] = decode.load(DECODED / f"{rid}.npz")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_sidecar_meta_json_written(self):
        for rid in self.ids:
            self.assertTrue((Path(self.tmp.name) / f"{rid}.meta.json").exists(), rid)

    def test_arrays_identical(self):
        for rid in self.ids:
            a, b = self.py[rid], self.rust[rid]
            for k in ARRAYS:
                x, y = a[k], b[k]
                self.assertEqual(x.dtype, y.dtype, f"{rid} {k} dtype")
                self.assertEqual(x.shape, y.shape, f"{rid} {k} shape")
                if x.dtype.kind == "f":
                    if not np.array_equal(x, y, equal_nan=True):
                        # Documented fallback (see module docstring); should never trigger.
                        self.assertTrue(np.allclose(x, y, atol=1e-3, equal_nan=True), f"{rid} {k} not close")
                        self.fail(f"{rid} {k}: within 1e-3 but not bit-identical; investigate before loosening")
                else:
                    self.assertTrue(np.array_equal(x, y), f"{rid} {k} differs")

    def test_meta_identical(self):
        # The Rust decoder adds wide keys inside each player object and inside header
        # (see test_wide_layout_present); every key the Python path writes must still be
        # identical, so those two are compared on the Python side's keys.
        for rid in self.ids:
            a, b = self.py[rid], self.rust[rid]
            for k in META:
                if k == "players":
                    self.assertEqual(len(a[k]), len(b[k]), f"{rid} meta players length")
                    for pa, pb in zip(a[k], b[k]):
                        self.assertEqual(pa, {kk: pb[kk] for kk in pa}, f"{rid} meta players")
                elif k == "header":
                    self.assertEqual(a[k], {kk: b[k][kk] for kk in a[k]}, f"{rid} meta header")
                else:
                    self.assertEqual(a[k], b[k], f"{rid} meta {k}")

    def test_wide_layout_present(self):
        """The Rust decoder appends the wide arrays and meta keys after the narrow set."""
        wide = {"ball_angvel": (3,), "cars_rot": ("P", 4), "cars_angvel": ("P", 3), "cars_ctrl": ("P", 8), "overtime": ()}
        for rid in self.ids:
            b = self.rust[rid]
            T, P = b["cars"].shape[:2]
            for k, tail in wide.items():
                self.assertIn(k, b, f"{rid} missing {k}")
                self.assertEqual(b[k].shape, (T,) + tuple(P if s == "P" else s for s in tail), f"{rid} {k} shape")
                want = {"overtime": np.bool_, "cars_ctrl": np.int16}.get(k, np.float16)
                self.assertEqual(b[k].dtype, want, f"{rid} {k} dtype")
            # the Python-path file is narrow and load() must simply omit the wide keys
            self.assertFalse(set(wide) & set(self.py[rid]), rid)
            # rotations are unit quaternions wherever a car is present, and absent where it is not
            q = b["cars_rot"].astype(np.float32)
            finite = np.isfinite(q[..., 0])
            self.assertTrue(np.array_equal(finite, np.isfinite(b["cars"][..., 0])), f"{rid} cars_rot NaN mask")
            if finite.any():
                norms = np.linalg.norm(q[finite], axis=1)
                self.assertTrue(np.allclose(norms, 1.0, atol=1e-2), f"{rid} cars_rot not unit: {norms.min()}..{norms.max()}")
            self.assertTrue(np.array_equal(np.isfinite(b["ball_angvel"][..., 0]), np.isfinite(b["ball"][..., 0])), rid)
            # controls: throttle/steer as raw bytes, then handbrake, on ground, boosting, jump,
            # double jump, dodge as 0/1 once seen; -1 before a car has replicated them
            ctrl = b["cars_ctrl"]
            self.assertTrue(np.all((ctrl >= -1) & (ctrl <= 255)), f"{rid} ctrl range")
            self.assertTrue(np.all(np.isin(ctrl[..., 2:], (-1, 0, 1))), f"{rid} ctrl flags")
            self.assertTrue(np.all(ctrl[~np.isfinite(b["cars"][..., 0])] == -1), f"{rid} ctrl set where no car")
            self.assertTrue((ctrl[..., 4] == 1).any(), f"{rid} nobody ever boosted")
            self.assertTrue(np.array_equal(np.isfinite(b["cars_angvel"][..., 0].astype(np.float32)), finite), rid)
            # overtime is False everywhere for a regulation-length game (header has no overtime
            # flag, so use total play time: a game that ends by 5:00 plus the countdowns never
            # reaches overtime)
            hdr = b["header"]
            if b["times"][-1] - b["times"][0] < 330.0:
                self.assertFalse(b["overtime"].any(), f"{rid} overtime set in a regulation game")
            # meta additions
            self.assertIsInstance(b["pickups"], list)
            self.assertTrue(len(b["pickups"]) > 0, f"{rid} no boost pickups")
            for pk in b["pickups"]:
                self.assertEqual(set(pk), {"time", "slot", "pad", "big", "pad_name", "car_loc"}, rid)
            self.assertIsInstance(hdr["player_stats"], list)
            for row in hdr["player_stats"]:
                self.assertEqual(set(row), {"Name", "Platform", "Team", "Score", "Goals", "Assists", "Saves", "Shots", "bBot", "OnlineID"}, rid)
                self.assertIsInstance(row["OnlineID"], str)
            for p in b["players"]:
                for k in ("body", "camera", "match_score", "match_goals", "match_assists", "match_saves", "match_shots"):
                    self.assertIn(k, p, f"{rid} player missing {k}")
                if p["camera"] is not None:
                    self.assertEqual(set(p["camera"]), {"fov", "height", "angle", "distance", "stiffness", "swivel", "transition"}, rid)

    @unittest.skipIf(pl is None, "polars not installed")
    def test_decision_rows_identical(self):
        from rlreplay import decisions_fast
        for rid in self.ids:
            fast_py = decisions_fast.rows_from_decoded(self.py[rid])
            fast_rs = decisions_fast.rows_from_decoded(self.rust[rid])
            self.assertEqual(fast_py.height, fast_rs.height, rid)
            if fast_py.height == 0:
                continue
            for col in ("action", "action_id", "decision_point", "team", "player_id", "time"):
                if col in fast_py.columns:
                    self.assertTrue((fast_py[col] == fast_rs[col]).all(), f"{rid} {col}")


class DecodeOneSwitchTests(unittest.TestCase):
    def test_env_var_forces_python(self):
        old = os.environ.get("RLREPLAY_NO_RUST")
        try:
            os.environ["RLREPLAY_NO_RUST"] = "1"
            self.assertFalse(decode.use_rust())
            os.environ.pop("RLREPLAY_NO_RUST")
            self.assertEqual(decode.use_rust(), decode.USE_RUST and EXE.exists())
        finally:
            if old is None:
                os.environ.pop("RLREPLAY_NO_RUST", None)
            else:
                os.environ["RLREPLAY_NO_RUST"] = old


if __name__ == "__main__":
    unittest.main()
