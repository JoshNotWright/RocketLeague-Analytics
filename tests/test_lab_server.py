"""The lab UI server against a temporary lab tree: overview, control file, config edits, ops."""
import json
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from pathlib import Path

from lab import server


class LabServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "lab"
        for band, month, n in (("diamond", "2025-07", 3), ("diamond", "2025-08", 5), ("gc", "2025-07", 2)):
            meta = self.root / "meta" / "ranked-doubles" / band
            meta.mkdir(parents=True, exist_ok=True)
            (meta / f"{month}.jsonl").write_text("".join(json.dumps({"id": f"{band}{month}{i}"}) + "\n" for i in range(n)))
            (meta / f"{month}.state.json").write_text(json.dumps({"listing_complete": band == "gc", "updated": "x"}))
            dec = self.root / "decoded" / "ranked-doubles" / band / month
            dec.mkdir(parents=True)
            for i in range(n):
                (dec / f"{band}{month}{i}.npz").write_bytes(b"x" * 10)
        batches = self.root / "batches"
        batches.mkdir()
        (batches / "2026-11-01-001.manifest.json").write_text(json.dumps({"box": "rl-crawler", "created": "2026-11-01T10:00:00Z", "items": [{}] * 4, "failed": [], "bytes": 40, "ledger": [{"band": "diamond", "month": "2025-07"}]}))
        (batches / "2026-11-01-001.ok").write_text(json.dumps({"validated_at": "2026-11-01T10:05:00Z"}))
        (batches / "2026-11-01-002.manifest.json").write_text(json.dumps({"box": "rl-crawler", "created": "2026-11-01T11:00:00Z", "items": [{}] * 2, "failed": [{}], "bytes": 20, "ledger": []}))
        (batches / "2026-11-01-002.bad").write_text(json.dumps({"problems": [{"id": "q", "error": "sha256"}]}))
        box = self.root / "boxes" / "rl-crawler"
        box.mkdir(parents=True)
        (box / "status.json").write_text(json.dumps({"box": "rl-crawler", "updated": "2026-11-01T11:00:00Z", "phase": "crawling x"}))
        # a fake repo with a stub script so /api/run is exercised without hcloud
        self.repo = base / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        (self.repo / "scripts" / "crawler_up.sh").write_text("#!/usr/bin/env bash\necho up-called \"$@\"\necho env=$RLREPLAY_LAB_ENV\n")
        self.env_path = base / "lab.env"
        self.env_path.write_text(
            "# comment stays\nHCLOUD_TOKEN=secret-value\nBALLCHASING_API_KEY=\nTS_AUTHKEY=\n"
            f"LAB_ROOT={self.root}\nLAB_USER=rl\nLAB_REPO={self.repo}\nCRAWLER_NAME=rl-crawler   # the box name\n"
            "CRAWLER_BATCH=20000\n")
        self.httpd = server.serve(self.env_path, bind="127.0.0.1", port=0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)

    def call(self, method, path, body=None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, body=json.dumps(body) if body is not None else None,
                     headers={"Content-Type": "application/json"} if body is not None else {})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, (json.loads(data) if resp.getheader("Content-Type", "").startswith("application/json") else data)

    def test_overview(self):
        status, o = self.call("GET", "/api/overview")
        self.assertEqual(status, 200)
        self.assertEqual(o["totals"], {"replays": 10, "decoded": 10, "bytes": 100})
        self.assertEqual([(w["band"], w["month"], w["replays"], w["listing_complete"]) for w in o["windows"]],
                         [("diamond", "2025-07", 3, False), ("diamond", "2025-08", 5, False), ("gc", "2025-07", 2, True)])
        names = {b["name"]: b for b in o["batches"]}
        self.assertEqual(names["2026-11-01-001"]["validated"], "2026-11-01T10:05:00Z")
        self.assertEqual(names["2026-11-01-002"]["problems"][0]["error"], "sha256")
        self.assertEqual(o["boxes"][0]["status"]["phase"], "crawling x")
        self.assertEqual([h["phase"] for h in o["boxes"][0]["history"]], ["crawling x"])
        self.call("GET", "/api/overview")  # same report again: no second history point
        self.assertEqual(len((self.root / "boxes" / "rl-crawler" / "history.jsonl").read_text().splitlines()), 1)
        self.assertEqual(o["secrets"], {"HCLOUD_TOKEN": True, "BALLCHASING_API_KEY": False, "TS_AUTHKEY": False})
        self.assertNotIn("secret-value", json.dumps(o))
        self.assertEqual(o["config"]["CRAWLER_NAME"], "rl-crawler")
        status, page = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Replay lab", page)

    def test_control_and_config(self):
        status, r = self.call("POST", "/api/control", {"box": "rl-crawler", "command": "pause"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads((self.root / "boxes" / "rl-crawler" / "control.json").read_text())["command"], "pause")
        self.assertEqual(self.call("POST", "/api/control", {"box": "rl-crawler", "command": "explode"})[0], 400)
        self.assertEqual(self.call("POST", "/api/control", {"box": "../x", "command": "stop"})[0], 400)
        status, cfg = self.call("POST", "/api/config", {"values": {"CRAWLER_BATCH": "10000", "CRAWLER_TYPE": "cpx22"}})
        self.assertEqual(status, 200)
        self.assertEqual(cfg["CRAWLER_BATCH"], "10000")
        text = self.env_path.read_text()
        self.assertIn("# comment stays", text)
        self.assertIn("HCLOUD_TOKEN=secret-value", text)
        self.assertIn("CRAWLER_NAME=rl-crawler   # the box name", text)
        self.assertIn("CRAWLER_BATCH=10000", text)
        self.assertIn("CRAWLER_TYPE=cpx22", text)
        self.assertEqual(self.call("POST", "/api/config", {"values": {"HCLOUD_TOKEN": "x"}})[0], 400)
        self.assertIn("HCLOUD_TOKEN=secret-value", self.env_path.read_text())

    def test_run_script_logs(self):
        status, op = self.call("POST", "/api/run", {"action": "up", "args": ["--type", "cpx22"]})
        self.assertEqual(status, 200)
        for _ in range(400):
            _, o = self.call("GET", "/api/overview")
            if o["ops"] and o["ops"][-1]["returncode"] is not None:
                break
            time.sleep(0.05)
        self.assertEqual(o["ops"][-1]["returncode"], 0)
        _, log = self.call("GET", f"/api/log?op={op['id']}")
        self.assertIn("up-called --type cpx22", log["log"])
        self.assertIn(f"env={self.env_path}", log["log"])
        self.assertEqual(self.call("POST", "/api/run", {"action": "rm"})[0], 400)

    def test_refuses_all_interfaces(self):
        with self.assertRaises(SystemExit):
            server.serve(self.env_path, bind="0.0.0.0", port=0)


if __name__ == "__main__":
    unittest.main()
