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
        for rid in self.ids:
            a, b = self.py[rid], self.rust[rid]
            for k in META:
                self.assertEqual(a[k], b[k], f"{rid} meta {k}")

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
