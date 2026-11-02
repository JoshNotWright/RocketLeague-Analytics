"""/api/v1 and lab/client.py: routes, the optional bearer token, and the client's commands."""
import io
import json
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from http.client import HTTPConnection
from pathlib import Path

from lab import client, server


class ApiTests(unittest.TestCase):
    def start(self, token: str = ""):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "lab"
        meta = self.root / "meta" / "ranked-doubles" / "diamond"
        meta.mkdir(parents=True)
        (meta / "2025-07.jsonl").write_text("".join(json.dumps({"id": f"d{i}"}) + "\n" for i in range(3)))
        self.repo = base / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        (self.repo / "scripts" / "crawler_up.sh").write_text("#!/usr/bin/env bash\necho up \"$@\"\n")
        (self.repo / "scripts" / "crawler_down.sh").write_text("#!/usr/bin/env bash\necho down\n")
        (self.repo / "scripts" / "crawler_image.sh").write_text("#!/usr/bin/env bash\necho image\n")
        env = base / "lab.env"
        env.write_text(f"LAB_ROOT={self.root}\nLAB_REPO={self.repo}\nCRAWLER_NAME=rl-crawler\nLAB_API_TOKEN={token}\n")
        self.httpd = server.serve(env, bind="127.0.0.1", port=0)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)

    def raw(self, method, path, body=None, headers=None):
        conn = HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        conn.request(method, path, body=json.dumps(body) if body is not None else None,
                     headers={"Content-Type": "application/json", **(headers or {})})
        resp = conn.getresponse()
        data = json.loads(resp.read())
        conn.close()
        return resp.status, data

    def wait_ops(self, c):
        for _ in range(200):
            if all(o["returncode"] is not None for o in c.get("/ops")["ops"]):
                return
            time.sleep(0.05)

    def test_routes_without_token(self):
        self.start()
        c = client.LabClient(self.url, "")
        self.assertEqual(c.status()["totals"]["replays"], 3)
        self.assertEqual(c.jobs(), [])
        self.assertIn("/api/v1/jobs/{id}/{action}", c.get("/openapi.json")["paths"])
        j = c.create(kind="crawl", preset="smoke", params={"per_window": 50})
        self.assertEqual((j["state"], j["params"]["per_window"]), ("draft", 50))
        self.assertEqual(c.act(j["id"], "update", name="Renamed")["name"], "Renamed")
        self.assertEqual(c.post("/estimate", {"params": {"per_window": 10, "months": {"first": "2025-07", "last": "2025-07"}}})["max_replays"], 30)
        self.assertEqual(c.act(j["id"], "start")["state"], "running")
        self.wait_ops(c)
        detail = c.job(j["id"])
        self.assertEqual(detail["job"]["runs"][-1]["action"], "up")
        self.assertIn("up --job", c.log(detail["ops"][-1]["id"]))
        self.assertEqual(c.get("/windows")["windows"][0]["replays"], 3)
        self.assertEqual(c.get("/batches")["batches"], [])
        self.assertEqual(c.post("/ops", {"action": "image"})["action"], "image")
        self.wait_ops(c)
        with self.assertRaises(SystemExit) as cm:
            c.act(j["id"], "explode")
        self.assertIn("400", str(cm.exception))
        self.assertEqual(self.raw("GET", "/api/v1/nothing")[0], 404)
        with self.assertRaises(SystemExit):
            c.post("/ops", {"action": "down"})

    def test_token_required_when_set(self):
        self.start(token="s3cret-token")
        self.assertEqual(self.raw("GET", "/api/v1/status")[0], 401)
        self.assertEqual(self.raw("GET", "/api/v1/status", headers={"Authorization": "Bearer wrong"})[0], 401)
        self.assertEqual(self.raw("GET", "/api/v1/status", headers={"Authorization": "Bearer s3cret-token"})[0], 200)
        self.assertEqual(self.raw("POST", "/api/v1/jobs", {"kind": "build"})[0], 401)
        self.assertEqual(self.raw("GET", "/api/overview")[0], 200)          # the page's own endpoints stay tailnet-only
        _, o = self.raw("GET", "/api/overview")
        self.assertTrue(o["secrets"]["LAB_API_TOKEN"])
        self.assertNotIn("s3cret-token", json.dumps(o))
        with self.assertRaises(SystemExit) as cm:
            client.LabClient(self.url, "").status()
        self.assertIn("401", str(cm.exception))
        self.assertEqual(client.LabClient(self.url, "s3cret-token").status()["totals"]["replays"], 3)

    def test_cli(self):
        self.start()
        def run(*argv):
            out = io.StringIO()
            with redirect_stdout(out):
                code = client.main(["--url", self.url, *argv])
            return code, out.getvalue()
        code, text = run("create", "--kind", "build", "--name", "T", "--param", "tables=frame,touches", "--param", "bands=diamond", "--param", "force=true")
        self.assertEqual(code, 0)
        j = json.loads(text)
        self.assertEqual((j["kind"], j["params"]["tables"], j["params"]["bands"], j["params"]["force"]), ("build", ["frame", "touches"], "diamond", True))
        code, text = run("create", "--preset", "smoke", "--param", "months.first=2025-08", "--param", "months.last=2025-08")
        self.assertEqual(json.loads(text)["params"]["months"], {"first": "2025-08", "last": "2025-08"})
        code, text = run("--summary", "jobs")
        self.assertIn("draft", text)
        self.assertIn("T", text)
        code, text = run("--summary", "status")
        self.assertIn("3 replays", text)
        code, text = run("endpoints")
        self.assertIn("/api/v1/status", text)
        self.assertEqual(client._params(["a.b=1", "c=null", "d=False"]), {"a": {"b": "1"}, "c": None, "d": False})
        with self.assertRaises(SystemExit):
            run("job", "nope")


if __name__ == "__main__":
    unittest.main()
