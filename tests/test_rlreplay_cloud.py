"""The cloud batch loop end to end against a fake ballchasing, a fake decoder and a local 'lab'."""
import importlib.util
import json
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from rlreplay import cloud
from rlreplay.crawl import Crawler
from tests.test_rlreplay_crawl import FakeClock, FakeSite

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("lab_validate", ROOT / "scripts" / "lab_validate.py")
lab_validate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lab_validate)


class LocalTransport(cloud.Transport):
    """The lab is a folder; validation runs lab_validate in-process."""

    def __init__(self, cfg, lab_root: Path):
        super().__init__(cfg)
        self.root = lab_root
        self.fail_validations = 0
        self.validations = []

    def copy_files(self, src_dir, names, dest_subdir):
        dest = self.root / dest_subdir
        dest.mkdir(parents=True, exist_ok=True)
        for name in names:
            (dest / name).write_bytes((Path(src_dir) / name).read_bytes())

    def copy_to(self, src, dest_path):
        dest = self.root / dest_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(Path(src).read_bytes())

    def copy_from(self, remote_path, dest):
        src = self.root / remote_path
        if not src.exists():
            return False
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(src.read_bytes())
        return True

    def copy_tree(self, remote_dir, dest_dir):
        src = self.root / remote_dir
        if src.exists():
            shutil.copytree(src, dest_dir, dirs_exist_ok=True)
        return True

    def listdir(self, remote_dir):
        d = self.root / remote_dir
        return sorted(p.name for p in d.iterdir()) if d.exists() else []

    def validate(self, batch):
        self.validations.append(batch)
        if self.fail_validations > 0:
            self.fail_validations -= 1
            return False
        return lab_validate.validate(self.root, batch)["ok"]


def fake_decoder(fail_ids=()):
    def decode(paths, out_dir):
        out = []
        for path in paths:
            rid = Path(path).stem
            if rid in fail_ids:
                out.append((rid, "failed", "boom"))
            else:
                (Path(out_dir) / f"{rid}.npz").write_bytes(b"npz:" + rid.encode() * 3)
                (Path(out_dir) / f"{rid}.meta.json").write_text("{}")
                out.append((rid, "ok", None))
        return out
    return decode


class CloudLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.lab = self.base / "lab"
        self.cfg = cloud.Config(box="testbox", work=self.base / "work", lab_remote="lab:/x", lab_ssh="lab",
                                lab_validate="v", lab_root=str(self.lab), playlist="ranked-doubles",
                                bands=[{"name": "diamond", "min": "diamond-1", "max": "diamond-3"},
                                       {"name": "gc", "min": "grand-champion-1", "max": "grand-champion-3"}],
                                months=["2025-07", "2025-08"], batch_size=5, workers=1, tier="gc",
                                max_unvalidated=2, min_free_gb=0.0, status_every=0.0, retry_after=0.0)
        self.clock = FakeClock()
        self.per_window = 7
        self.sleeps = []

    def ids_for(self, filters):
        band = "gc" if filters.get("min-rank", "").startswith("grand") else "diamond"
        month = filters["replay-date-after"][:7]
        return [f"{band}-{month}-{i}" for i in range(self.per_window)]

    def factory(self, runner):
        def make(out_dir, filters, max_replays, known):
            site = FakeSite({None: self.ids_for(filters)})
            return Crawler(out_dir, filters, transport=site, key="test-key-value", max_replays=max_replays, known_ids=known,
                           ledger_only=True, should_stop=runner._should_stop, quiet=True, log=lambda m: None,
                           sleep=self.clock.sleep, clock=self.clock.now)
        return make

    def runner(self, transport=None, decoder=None):
        transport = transport or LocalTransport(self.cfg, self.lab)
        r = cloud.BatchRunner(self.cfg, transport=transport, decoder=decoder or fake_decoder(),
                              sleep=self.sleeps.append, log=lambda m: None)
        r.crawler_factory = self.factory(r)
        return r, transport

    def test_full_run_pushes_validates_and_cleans(self):
        runner, transport = self.runner()
        self.assertEqual(runner.run(), 0)
        expected = 2 * 2 * self.per_window
        self.assertEqual(runner.saved_total, expected)
        npz = sorted(self.lab.glob("decoded/ranked-doubles/*/*/*.npz"))
        self.assertEqual(len(npz), expected)
        for band in ("diamond", "gc"):
            for month in ("2025-07", "2025-08"):
                ledger = self.lab / "meta" / "ranked-doubles" / band / f"{month}.jsonl"
                self.assertEqual(len(lab_validate.ledger_ids(ledger)), self.per_window)
                self.assertTrue((self.lab / "meta" / "ranked-doubles" / band / f"{month}.state.json").exists())
        manifests = sorted(self.lab.glob("batches/*.manifest.json"))
        self.assertGreaterEqual(len(manifests), expected // self.cfg.batch_size)
        for m in manifests:
            self.assertTrue(m.with_name(m.name.replace(".manifest.json", ".ok")).exists(), m.name)
        self.assertEqual(runner.unvalidated(), [])
        # the box keeps only ledger and state: no raws, no decoded files
        self.assertEqual(list(self.cfg.work.glob("crawl/**/*.replay")), [])
        self.assertEqual(list(self.cfg.work.glob("crawl/**/*.npz")), [])
        self.assertEqual(len(list(self.cfg.work.glob("crawl/**/metadata.json"))), 4)
        self.assertEqual(runner.current, {"items": [], "failed": []})
        status = json.loads((self.lab / "boxes" / "testbox" / "status.json").read_text())
        self.assertEqual(status["phase"], "all windows complete")
        self.assertTrue(all(w["listing_complete"] for w in status["windows"]))

    def test_second_run_from_the_lab_ledger_fetches_nothing(self):
        runner, _ = self.runner()
        runner.run()
        # a fresh box: wipe the work dir, keep the lab
        shutil.rmtree(self.cfg.work)
        runner2, _ = self.runner()
        self.assertEqual(runner2.run(), 0)
        self.assertEqual(runner2.saved_total, 0)
        self.assertEqual(len(runner2.known), 2 * 2 * self.per_window)
        self.assertEqual(len(list(self.lab.glob("batches/*.manifest.json"))),
                         len(list(self.lab.glob("batches/*.ok"))))

    def test_stop_and_pause_from_the_lab_control_file(self):
        runner, transport = self.runner()
        control = self.lab / "boxes" / "testbox" / "control.json"
        control.parent.mkdir(parents=True)
        control.write_text(json.dumps({"command": "pause"}))
        runner.pull_ledger()
        self.assertTrue(runner.cycle())
        self.assertEqual(runner.phase, "paused")
        self.assertEqual(runner.saved_total, 0)
        control.write_text(json.dumps({"command": "run"}))
        self.assertTrue(runner.cycle())
        self.assertGreater(runner.saved_total, 0)
        control.write_text(json.dumps({"command": "stop"}))
        self.assertFalse(runner.cycle())
        runner.finish()
        # the partial batch was sealed, pushed and validated; cursor kept for the next box
        self.assertEqual(runner.unvalidated(), [])
        self.assertEqual(len(list(self.lab.glob("decoded/**/*.npz"))), runner.saved_total)
        self.assertEqual(list(self.cfg.work.glob("crawl/**/*.replay")), [])

    def test_failed_validation_keeps_files_and_retries(self):
        transport = LocalTransport(self.cfg, self.lab)
        transport.fail_validations = 1
        runner, _ = self.runner(transport)
        runner.pull_ledger()
        while len(runner.sealed()) == 0:
            self.assertTrue(runner.cycle())
        name = runner.sealed()[0]
        runner.validating[name].join()
        self.assertEqual(transport.validations, [name])
        self.assertEqual(runner.unvalidated(), [name])
        self.assertGreater(len(list(self.cfg.work.glob("crawl/**/*.npz"))), 0)  # nothing deleted
        runner.cycle()  # retry_after is 0, so the stale batch is pushed and validated again
        for thread in list(runner.validating.values()):
            thread.join()
        self.assertEqual(transport.validations[:2], [name, name])
        runner.mirror_marks()
        runner.cleanup()
        self.assertNotIn(name, runner.unvalidated())
        runner.finish()
        self.assertEqual(runner.unvalidated(), [])

    def test_decode_failure_is_recorded_and_never_refetched(self):
        runner, _ = self.runner(decoder=fake_decoder(fail_ids={"diamond-2025-07-3"}))
        runner.run()
        manifests = [json.loads(p.read_text()) for p in self.lab.glob("batches/*.manifest.json")]
        failed = [f for m in manifests for f in m["failed"]]
        self.assertEqual([f["id"] for f in failed], ["diamond-2025-07-3"])
        self.assertEqual(len(list(self.lab.glob("decoded/**/*.npz"))), 2 * 2 * self.per_window - 1)
        ledger = self.lab / "meta" / "ranked-doubles" / "diamond" / "2025-07.jsonl"
        self.assertIn("diamond-2025-07-3", lab_validate.ledger_ids(ledger))
        self.assertFalse((self.cfg.work / "crawl/ranked-doubles/diamond/2025-07/diamond-2025-07-3.replay").exists())

    def test_broken_decoder_parks_the_box_and_keeps_raws(self):
        """Every replay of a chunk failing is the decoder's fault: no ledger burn, no deleted raws."""
        runner, _ = self.runner(decoder=lambda paths, out: [(Path(p).stem, "failed", "no decoder") for p in paths])
        runner.pull_ledger()
        with patch.object(cloud, "DECODER_BROKEN_MIN", 3):
            self.assertTrue(runner.cycle())
            self.assertTrue(runner.phase.startswith("decoder failing"))
            self.assertEqual(runner.current["failed"], [])
            self.assertEqual(runner.current["items"], [])
            raws = len(list(self.cfg.work.glob("crawl/ranked-doubles/diamond/2025-07/*.replay")))
            self.assertGreaterEqual(raws, 3)
            self.assertTrue(runner.cycle())  # stays parked, fetches nothing more
            self.assertEqual(len(list(self.cfg.work.glob("crawl/**/*.replay"))), raws)
        runner.finish()
        self.assertEqual(list(self.lab.glob("batches/*.manifest.json")), [])

    def test_disk_guard_pauses_crawling(self):
        runner, _ = self.runner()
        runner.pull_ledger()
        runner.disk_ok = lambda: False
        self.assertTrue(runner.cycle())
        self.assertEqual(runner.phase, "waiting for disk")
        self.assertEqual(runner.saved_total, 0)

    def test_per_window_cap(self):
        self.cfg.per_window = 3
        runner, _ = self.runner()
        runner.run()
        self.assertEqual(runner.saved_total, 4 * 3)


class LabValidateTests(unittest.TestCase):
    def test_bad_hash_writes_bad_and_ok_only_when_clean(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            dec = root / "decoded" / "p" / "b" / "2025-07"
            dec.mkdir(parents=True)
            (dec / "x.npz").write_bytes(b"abc")
            (root / "meta" / "p" / "b").mkdir(parents=True)
            (root / "meta" / "p" / "b" / "2025-07.jsonl").write_text(json.dumps({"id": "x"}) + "\n")
            (root / "batches").mkdir()
            good = {"id": "x", "playlist": "p", "band": "b", "month": "2025-07", "size": 3,
                    "sha256": lab_validate.sha256_of(dec / "x.npz")}
            (root / "batches" / "b1.manifest.json").write_text(json.dumps({"items": [good]}))
            self.assertTrue(lab_validate.validate(root, "b1")["ok"])
            self.assertTrue((root / "batches" / "b1.ok").exists())
            bad = dict(good, sha256="0" * 64)
            (root / "batches" / "b2.manifest.json").write_text(json.dumps({"items": [bad]}))
            result = lab_validate.validate(root, "b2")
            self.assertFalse(result["ok"])
            self.assertEqual(result["problems"][0]["error"], "sha256")
            self.assertTrue((root / "batches" / "b2.bad").exists())
            self.assertFalse((root / "batches" / "b2.ok").exists())
            missing_ledger = dict(good, id="y")
            (dec / "y.npz").write_bytes(b"abc")
            (root / "batches" / "b3.manifest.json").write_text(json.dumps({"items": [missing_ledger]}))
            self.assertEqual(lab_validate.validate(root, "b3")["problems"][0]["error"], "ids missing from ledger")
            self.assertEqual(lab_validate.main([str(root), "b1"]), 0)
            self.assertEqual(lab_validate.main([str(root), "b2"]), 1)


class ConfigTests(unittest.TestCase):
    def test_months_and_windows(self):
        self.assertEqual(cloud.months_between("2025-11", "2026-02"), ["2025-11", "2025-12", "2026-01", "2026-02"])
        self.assertEqual(cloud.month_bounds("2025-12"), ("2025-12-01T00:00:00Z", "2026-01-01T00:00:00Z"))
        cfg = cloud.Config(box="b", work=Path("w"), lab_remote="lab:/x", lab_ssh=None, lab_validate="v", lab_root="/x",
                           bands=[{"name": "d", "min": "diamond-1", "max": "diamond-3"}], months=["2025-07"])
        w = cfg.windows()[0]
        self.assertEqual(w.key, "ranked-doubles/d/2025-07")
        self.assertEqual(w.filters()["replay-date-before"], "2025-08-01T00:00:00Z")
        self.assertEqual(cfg.lab_meta(w), "meta/ranked-doubles/d/2025-07.jsonl")

    def test_transport_splits_command_strings(self):
        """The config carries rclone/ssh as command lines with flags; argv must be split, not one element."""
        cfg = cloud.Config(box="b", work=Path("w"), lab_remote="lab:/x", lab_ssh="u@lab", lab_validate="v",
                           lab_root="/x", rclone="rclone --config /etc/rlreplay/rclone.conf",
                           ssh="ssh -i /etc/rlreplay/lab_key -o StrictHostKeyChecking=accept-new")
        calls = []
        fake = SimpleNamespace(returncode=1, stdout="", stderr="")
        with patch.object(cloud.subprocess, "run", side_effect=lambda argv, **kw: calls.append(argv) or fake):
            t = cloud.Transport(cfg)
            t.copy_from("meta/a.jsonl", Path(tempfile.mkdtemp()) / "a.jsonl")
            t.listdir("batches")
            t.validate("b1")
        self.assertEqual(calls[0][:3], ["rclone", "--config", "/etc/rlreplay/rclone.conf"])
        self.assertEqual(calls[1][:3], ["rclone", "--config", "/etc/rlreplay/rclone.conf"])
        self.assertEqual(calls[2][:3], ["ssh", "-i", "/etc/rlreplay/lab_key"])
        self.assertIn("u@lab", calls[2])


if __name__ == "__main__":
    unittest.main()
