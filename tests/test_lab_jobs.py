"""Jobs: parameter validation, progress against the ledger, and the lifecycle through the lab API
with stub crawler_up.sh / crawler_down.sh scripts."""
import json
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from pathlib import Path

from lab import jobs, server


class JobParamTests(unittest.TestCase):
    def test_bands_and_months(self):
        self.assertEqual(jobs.parse_bands("diamond,gc")[1], {"name": "gc", "min": "grand-champion-1", "max": "grand-champion-3"})
        self.assertEqual(jobs.parse_bands("x:a-1:a-3")[0]["min"], "a-1")
        for bad in ("", "diamond:only-two", "diamond,diamond"):
            with self.assertRaises(ValueError):
                jobs.parse_bands(bad)
        self.assertEqual(jobs.months_between("2025-11", "2026-02"), ["2025-11", "2025-12", "2026-01", "2026-02"])
        with self.assertRaises(ValueError):
            jobs.clean_params({"months": {"first": "2026-01", "last": "2025-01"}})
        with self.assertRaises(ValueError):
            jobs.clean_params({"months": {"first": "2025-13", "last": "2025-13"}})

    def test_clean_params_and_estimate(self):
        p = jobs.clean_params({"per_window": "300", "batch_size": "500", "months": {"first": "2025-07", "last": "2025-07"}})
        self.assertEqual((p["per_window"], p["batch_size"], p["workers"], p["server_type"]), (300, 500, 4, "cpx32"))
        self.assertIsNone(jobs.clean_params({"per_window": ""})["per_window"])
        with self.assertRaises(ValueError):
            jobs.clean_params({"batch_size": 1})
        with self.assertRaises(ValueError):
            jobs.clean_params({"playlist": "Ranked Doubles"})
        e = jobs.estimate(p)
        self.assertEqual((e["windows"], e["max_replays"]), (3, 900))
        self.assertGreater(e["cost_usd"], 0)
        self.assertIsNone(jobs.estimate(jobs.DEFAULT_PARAMS)["max_replays"])

    def test_shell_exports(self):
        store = jobs.JobStore(Path(tempfile.mkdtemp()))
        job = store.create("Smoke test", jobs.PRESETS["smoke"]["params"])
        text = jobs.shell_exports(job)
        self.assertIn("export CRAWLER_JOB='smoke-test'", text)
        self.assertIn("export CRAWLER_MONTHS='2025-07..2025-07'", text)
        self.assertIn("export CRAWLER_PER_WINDOW='300'", text)
        self.assertIn("export CRAWLER_TYPE='cpx32'", text)
        second = store.create("Smoke test", {})
        self.assertEqual(second["id"], "smoke-test-2")


class JobLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "lab"
        for band, month, n, complete in (("diamond", "2025-07", 3, False), ("diamond", "2025-08", 5, False), ("gc", "2025-07", 2, True)):
            meta = self.root / "meta" / "ranked-doubles" / band
            meta.mkdir(parents=True, exist_ok=True)
            (meta / f"{month}.jsonl").write_text("".join(json.dumps({"id": f"{band}{month}{i}"}) + "\n" for i in range(n)))
            (meta / f"{month}.state.json").write_text(json.dumps({"listing_complete": complete}))
        self.repo = base / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        (self.repo / "scripts" / "crawler_up.sh").write_text("#!/usr/bin/env bash\necho up \"$@\"\n")
        # down fails unless --force is given or the 'validated' marker exists
        (self.repo / "scripts" / "crawler_down.sh").write_text(
            "#!/usr/bin/env bash\necho down \"$@\"\n[[ \"$*\" == *--force* || -f \"$DOWN_OK\" ]] && exit 0\necho unvalidated; exit 1\n")
        self.down_ok = base / "validated"
        self.env_path = base / "lab.env"
        self.env_path.write_text(f"LAB_ROOT={self.root}\nLAB_REPO={self.repo}\nCRAWLER_NAME=rl-crawler\n")
        import os
        os.environ["DOWN_OK"] = str(self.down_ok)
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

    def state(self, job_id):
        return self.call("GET", f"/api/job?id={job_id}")[1]["job"]["state"]

    def control(self):
        return json.loads((self.root / "boxes" / "rl-crawler" / "control.json").read_text())["command"]

    def test_progress_against_the_ledger(self):
        status, j = self.job("create", name="Two bands", params={"bands": "diamond,gc", "months": {"first": "2025-07", "last": "2025-08"}, "per_window": 3})
        self.assertEqual(status, 200)
        _, d = self.call("GET", f"/api/job?id={j['id']}")
        rows = {(r["band"], r["month"]): r for r in d["progress"]["windows"]}
        self.assertEqual(len(rows), 4)
        self.assertTrue(rows[("diamond", "2025-07")]["done"])      # reached the cap
        self.assertTrue(rows[("diamond", "2025-08")]["done"])      # over the cap
        self.assertTrue(rows[("gc", "2025-07")]["done"])           # listing complete below the cap
        self.assertFalse(rows[("gc", "2025-08")]["done"])
        self.assertEqual(d["progress"]["replays"], 10)
        self.assertEqual(d["progress"]["target"], 12)
        self.assertAlmostEqual(d["progress"]["fraction"], 8 / 12, places=3)
        self.assertEqual(d["progress"]["estimate"]["windows"], 4)
        status, e = self.job("estimate", params={"per_window": 300, "months": {"first": "2025-07", "last": "2025-07"}})
        self.assertEqual((status, e["max_replays"]), (200, 900))

    def test_lifecycle(self):
        status, j = self.job("create", preset="smoke")
        self.assertEqual((status, j["state"], j["name"], j["params"]["per_window"]), (200, "draft", "Smoke test", 300))
        jid = j["id"]
        _, o = self.call("GET", "/api/overview")
        self.assertEqual([x["id"] for x in o["jobs"]], [jid])
        self.assertIsNone(o["active_job"])

        status, j = self.job("start", id=jid)
        self.assertEqual((status, j["state"]), (200, "running"))
        self.assertEqual(self.control(), "run")
        o = self.wait_ops()
        self.assertEqual(o["ops"][-1]["args"], ["--job", jid])
        self.assertEqual(o["ops"][-1]["job"], jid)
        self.assertEqual(o["active_job"], jid)
        self.assertEqual(self.state(jid), "running")

        # one box, one job
        _, other = self.job("create", name="Other", params={})
        self.assertEqual(self.job("start", id=other["id"])[0], 400)
        self.assertEqual(self.job("update", id=jid, params={"per_window": 10})[0], 400)
        self.assertEqual(self.job("cancel", id=jid)[0], 400)
        self.assertEqual(self.job("update", id=jid, name="Smoke test (renamed)")[0], 200)

        self.assertEqual(self.job("pause", id=jid)[1]["state"], "paused")
        self.assertEqual(self.control(), "pause")
        self.assertEqual(self.job("pause", id=jid)[0], 400)
        self.assertEqual(self.job("resume", id=jid)[1]["state"], "running")
        self.assertEqual(self.control(), "run")

        # stop fails (unvalidated batches): the job is failed, the box is still there, force stop clears it
        self.assertEqual(self.job("stop", id=jid)[1]["state"], "stopping")
        self.wait_ops()
        _, d = self.call("GET", f"/api/job?id={jid}")
        self.assertEqual(d["job"]["state"], "failed")
        self.assertIn("stop failed", d["job"]["runs"][-1]["outcome"])
        self.assertEqual(self.job("force_stop", id=jid)[1]["state"], "stopping")
        o = self.wait_ops()
        self.assertEqual(o["ops"][-1]["args"], ["--force"])
        self.assertEqual(self.state(jid), "stopped")
        self.assertIsNone(o["active_job"])

        # continue: a new box, same job; a clean stop this time
        self.assertEqual(self.job("start", id=jid)[1]["state"], "running")
        self.wait_ops()
        self.down_ok.write_text("")
        self.assertEqual(self.job("stop", id=jid)[1]["state"], "stopping")
        self.wait_ops()
        _, d = self.call("GET", f"/api/job?id={jid}")
        self.assertEqual(d["job"]["state"], "stopped")
        self.assertEqual(len(d["job"]["runs"]), 5)
        self.assertEqual([r["action"] for r in d["job"]["runs"]], ["up", "down", "down", "up", "down"])

        self.assertEqual(self.job("cancel", id=jid)[0], 200)
        self.assertEqual(self.call("GET", f"/api/job?id={jid}")[0], 400)

    def test_box_reporting_all_windows_complete_finishes_the_job(self):
        _, j = self.job("create", preset="smoke")
        self.job("start", id=j["id"])
        self.wait_ops()
        box = self.root / "boxes" / "rl-crawler"
        (box / "status.json").write_text(json.dumps({"box": "rl-crawler", "job": j["id"], "updated": server._now(),
                                                     "phase": "all windows complete", "per_minute": 0}))
        self.down_ok.write_text("")
        _, o = self.call("GET", "/api/overview")
        self.assertIn([x["state"] for x in o["jobs"] if x["id"] == j["id"]][0], ("finishing", "complete"))
        self.wait_ops()
        _, d = self.call("GET", f"/api/job?id={j['id']}")
        self.assertEqual(d["job"]["state"], "complete")
        self.assertEqual(d["job"]["runs"][-1]["action"], "down")
        self.assertEqual([h.get("job") for h in d["history"]], [j["id"]])

    def test_start_failure_marks_the_job_failed(self):
        (self.repo / "scripts" / "crawler_up.sh").write_text("#!/usr/bin/env bash\necho boom; exit 3\n")
        _, j = self.job("create", preset="smoke")
        self.job("start", id=j["id"])
        self.wait_ops()
        _, d = self.call("GET", f"/api/job?id={j['id']}")
        self.assertEqual(d["job"]["state"], "failed")
        self.assertIn("exit 3", d["job"]["runs"][-1]["outcome"])
        self.assertEqual(self.job("start", id=j["id"])[0], 200)   # a failed job can be started again


if __name__ == "__main__":
    unittest.main()
