"""lab/build.py against a fake archive: window discovery, incremental planning, progress and stop;
and build jobs through the lab API with a stub lab_build.sh."""
import json
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from pathlib import Path

from lab import build, jobs, server


def make_archive(root: Path):
    for band, month, n in (("diamond", "2025-07", 3), ("gc", "2025-07", 2), ("diamond", "2025-08", 4)):
        d = root / "decoded" / "ranked-doubles" / band / month
        d.mkdir(parents=True)
        for i in range(n):
            (d / f"{band}{month}{i}.npz").write_bytes(b"x")
        meta = root / "meta" / "ranked-doubles" / band
        meta.mkdir(parents=True, exist_ok=True)
        (meta / f"{month}.jsonl").write_text("".join(json.dumps({"id": f"{band}{month}{i}"}) + "\n" for i in range(n)))
    (root / "decoded" / "ranked-doubles" / "champion" / "2025-07").mkdir(parents=True)   # no replays: ignored


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        make_archive(self.root)
        self.calls = []

    def runner(self, args):
        """Stands in for `python -m rlreplay ...`: records the call and writes the output it would."""
        self.calls.append(args)
        out = Path(args[args.index("--out") + 1])
        if args[0] == "decisions-build":
            out.mkdir(parents=True, exist_ok=True)
            (out / "r.parquet").write_bytes(b"p")
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"p")

    def test_windows_and_plan(self):
        self.assertEqual(build.find_windows(self.root),
                         [("ranked-doubles", "diamond", "2025-07"), ("ranked-doubles", "gc", "2025-07"), ("ranked-doubles", "diamond", "2025-08")])
        self.assertEqual(build.find_windows(self.root, bands=["gc"]), [("ranked-doubles", "gc", "2025-07")])
        self.assertEqual(build.find_windows(self.root, months=["2025-08"]), [("ranked-doubles", "diamond", "2025-08")])
        b = build.Builder(self.root, "j1", ["frame", "table"], runner=self.runner, log=lambda m: None)
        steps = b.plan(build.find_windows(self.root))
        self.assertEqual(len(steps), 6)
        self.assertEqual(steps[0], ("frame", ("ranked-doubles", "diamond", "2025-07")))

    def test_run_is_incremental_and_reports_progress(self):
        b = build.Builder(self.root, "j1", build.TABLES, horizon=2.5, workers=3, runner=self.runner, log=lambda m: None)
        self.assertEqual(b.run(build.find_windows(self.root)), 0)
        self.assertEqual(len(self.calls), 12)
        extract = next(c for c in self.calls if c[0] == "extract")
        self.assertIn("--decoded", extract)
        self.assertEqual(extract[extract.index("--workers") + 1], "3")
        decisions = next(c for c in self.calls if c[0] == "decisions-build")
        self.assertEqual(decisions[decisions.index("--horizon") + 1], "2.5")
        self.assertTrue((self.root / "features" / "frame" / "ranked-doubles" / "diamond" / "2025-07.parquet").exists())
        self.assertTrue((self.root / "features" / "decisions" / "ranked-doubles" / "gc" / "2025-07" / "r.parquet").exists())
        prog = json.loads((self.root / "builds" / "j1.json").read_text())
        self.assertEqual((prog["phase"], prog["done"], prog["total"], prog["errors"]), ("complete", 12, 12, []))
        self.assertEqual(prog["windows"][0]["tables"]["touches"], "done")
        state = json.loads((self.root / "features" / "build_state.json").read_text())
        self.assertEqual(state["ranked-doubles/diamond/2025-07"]["frame"]["count"], 3)

        # nothing changed: nothing to do
        self.calls.clear()
        b2 = build.Builder(self.root, "j2", build.TABLES, runner=self.runner, log=lambda m: None)
        self.assertEqual(b2.run(build.find_windows(self.root)), 0)
        self.assertEqual(self.calls, [])
        # a new replay lands in one window: only that window's three replay tables rebuild
        (self.root / "decoded" / "ranked-doubles" / "gc" / "2025-07" / "new.npz").write_bytes(b"x")
        b3 = build.Builder(self.root, "j3", build.TABLES, runner=self.runner, log=lambda m: None)
        b3.run(build.find_windows(self.root))
        self.assertEqual(sorted(c[0] for c in self.calls), ["decisions-build", "extract", "touches"])
        # force rebuilds everything selected
        self.calls.clear()
        b4 = build.Builder(self.root, "j4", ["frame"], force=True, runner=self.runner, log=lambda m: None)
        b4.run(build.find_windows(self.root))
        self.assertEqual(len(self.calls), 3)

    def test_failures_are_recorded_and_the_rest_continues(self):
        def flaky(args):
            if args[0] == "touches" and "gc" in args[2]:
                raise RuntimeError("boom")
            self.runner(args)
        b = build.Builder(self.root, "j1", ["touches"], runner=flaky, log=lambda m: None)
        self.assertEqual(b.run(build.find_windows(self.root)), 1)
        prog = json.loads((self.root / "builds" / "j1.json").read_text())
        self.assertEqual(prog["phase"], "failed")
        self.assertEqual(len(prog["errors"]), 1)
        self.assertEqual(prog["done"], 3)
        self.assertEqual(prog["windows"][1]["tables"]["touches"], "failed")

    def test_stop_between_steps(self):
        b = build.Builder(self.root, "j1", ["frame"], runner=self.runner, log=lambda m: None)
        original = b.build_one

        def one(table, w):
            original(table, w)
            b.stop_requested = True
        b.build_one = one
        self.assertEqual(b.run(build.find_windows(self.root)), 0)
        prog = json.loads((self.root / "builds" / "j1.json").read_text())
        self.assertEqual((prog["phase"], prog["done"]), ("stopped", 1))

    def test_build_params(self):
        p = jobs.clean_build_params({"tables": "frame,touches", "months": {"first": "2025-07", "last": "2025-08"}, "bands": "diamond, gc", "force": "true"})
        self.assertEqual(p["tables"], ["frame", "touches"])
        self.assertEqual(p["bands"], "diamond,gc")
        self.assertTrue(p["force"])
        self.assertEqual(jobs.build_args(p), ["--tables", "frame,touches", "--horizon", "2.5", "--hz", "5.0", "--bands", "diamond,gc",
                                               "--months", "2025-07..2025-08", "--force"])
        self.assertIsNone(jobs.clean_build_params({"months": ""})["months"])
        with self.assertRaises(ValueError):
            jobs.clean_build_params({"tables": "frames"})


class BuildJobTests(unittest.TestCase):
    def setUp(self):
        # a terminated stub may hold its log open for a moment on Windows
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "lab"
        make_archive(self.root)
        self.repo = base / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        (self.repo / "scripts" / "lab_build.sh").write_text(
            "#!/usr/bin/env bash\necho build \"$@\"\nfor i in 1 2 3 4 5 6 7 8 9 10; do sleep 0.3; done\n")
        (self.repo / "scripts" / "crawler_up.sh").write_text("#!/usr/bin/env bash\necho up\n")
        self.env_path = base / "lab.env"
        self.env_path.write_text(f"LAB_ROOT={self.root}\nLAB_REPO={self.repo}\nCRAWLER_NAME=rl-crawler\n")
        self.httpd = server.serve(self.env_path, bind="127.0.0.1", port=0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)

    def call(self, method, path, body=None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, body=json.dumps(body) if body is not None else None,
                     headers={"Content-Type": "application/json"} if body is not None else {})
        resp = conn.getresponse()
        data = json.loads(resp.read())
        conn.close()
        return resp.status, data

    def job(self, action, **kw):
        return self.call("POST", "/api/jobs", {"action": action, **kw})

    def wait_ops(self):
        for _ in range(400):
            _, o = self.call("GET", "/api/overview")
            if all(op["returncode"] is not None for op in o["ops"]):
                return o
            time.sleep(0.05)
        raise AssertionError("ops did not finish")

    def test_build_job_lifecycle(self):
        status, j = self.job("create", preset="build")
        self.assertEqual((status, j["kind"], j["state"], j["params"]["tables"]), (200, "build", "draft", list(build.TABLES)))
        jid = j["id"]
        self.assertEqual(self.job("update", id=jid, params={"tables": "frame,table", "horizon": 1.0})[1]["params"]["tables"], ["frame", "table"])
        self.assertEqual(self.job("pause", id=jid)[0], 400)
        status, j = self.job("start", id=jid)
        self.assertEqual((status, j["state"]), (200, "running"))
        # a crawl job may run at the same time; a second build may not
        _, crawl = self.job("create", preset="smoke")
        self.assertEqual(self.job("start", id=crawl["id"])[0], 200)
        _, other = self.job("create", preset="build")
        self.assertEqual(self.job("start", id=other["id"])[0], 400)
        _, o = self.call("GET", "/api/overview")
        self.assertEqual(o["active_build"], jid)
        self.assertEqual(o["active_job"], crawl["id"])
        self.assertNotIn("_proc", json.dumps(o))
        # the build progress file written by the script (here: by hand) shows up in the detail
        (self.root / "builds").mkdir(exist_ok=True)
        (self.root / "builds" / f"{jid}.json").write_text(json.dumps({"job": jid, "phase": "running", "total": 4, "done": 1, "current": "frame x", "windows": [], "errors": []}))
        status, d = self.call("GET", f"/api/job?id={jid}")
        self.assertEqual(status, 200, d)
        self.assertEqual((d["build"]["done"], d["progress"]["fraction"]), (1, 0.25))
        # stop terminates the script; the job ends stopped, not failed
        self.assertEqual(self.job("stop", id=jid)[1]["state"], "stopping")
        self.wait_ops()
        _, d = self.call("GET", f"/api/job?id={jid}")
        self.assertEqual(d["job"]["state"], "stopped")
        self.assertEqual(d["job"]["runs"][-1]["outcome"], "stopped")
        self.assertIn("--tables frame,table", open(d["ops"][-1]["log"]).read())
        # start again, let it finish: complete
        (self.repo / "scripts" / "lab_build.sh").write_text("#!/usr/bin/env bash\necho build \"$@\"\n")
        self.assertEqual(self.job("start", id=jid)[1]["state"], "running")
        self.wait_ops()
        self.assertEqual(self.call("GET", f"/api/job?id={jid}")[1]["job"]["state"], "complete")
        self.assertEqual(self.job("cancel", id=jid)[0], 200)


if __name__ == "__main__":
    unittest.main()
