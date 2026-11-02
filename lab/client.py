"""Command-line client for the lab's /api/v1, standard library only.

    export RLLAB_URL=http://<lab tailnet ip>:8731        (RLLAB_TOKEN too, if the lab sets LAB_API_TOKEN)
    python -m lab.client status
    python -m lab.client jobs
    python -m lab.client job <id>
    python -m lab.client create --kind crawl --preset smoke [--name "..."] [--param per_window=300 --param months.first=2025-07]
    python -m lab.client create --kind build [--param tables=frame,touches --param bands=diamond]
    python -m lab.client start|pause|resume|stop|force-stop|cancel <id>
    python -m lab.client log <op-id> [--tail 200]        python -m lab.client ops
    python -m lab.client estimate --param per_window=300 --param months.first=2025-07 --param months.last=2025-07
    python -m lab.client image                            python -m lab.client endpoints

Every command prints JSON; --summary prints a few lines for status / jobs / job instead.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


class LabClient:
    def __init__(self, url: str | None = None, token: str | None = None, timeout: float = 30.0):
        self.url = (url or os.environ.get("RLLAB_URL") or "").rstrip("/")
        self.token = token if token is not None else os.environ.get("RLLAB_TOKEN", "")
        self.timeout = timeout
        if not self.url:
            raise SystemExit("set RLLAB_URL (for example http://100.88.235.105:8731) or pass --url")

    def call(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.url}/api/v1{path}", data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read() or b"{}")
            except ValueError:
                payload = {"error": exc.reason}
            raise SystemExit(f"{exc.code}: {payload.get('error', payload)}") from None
        except urllib.error.URLError as exc:
            raise SystemExit(f"cannot reach {self.url}: {exc.reason}") from None

    def get(self, path: str):
        return self.call("GET", path)

    def post(self, path: str, body: dict | None = None):
        return self.call("POST", path, body or {})

    # convenience
    def status(self): return self.get("/status")
    def jobs(self): return self.get("/jobs")["jobs"]
    def job(self, job_id: str): return self.get(f"/jobs/{job_id}")
    def create(self, **body): return self.post("/jobs", body)
    def act(self, job_id: str, action: str, **body): return self.post(f"/jobs/{job_id}/{action}", body)
    def log(self, op_id: str, tail: int = 200): return self.get(f"/ops/{op_id}/log?tail={tail}")["log"]


def _params(items: list) -> dict:
    """--param a=b --param months.first=2025-07 --param tables=frame,touches -> nested dict."""
    out: dict = {}
    for item in items or []:
        key, _, value = item.partition("=")
        if not key:
            raise SystemExit(f"bad --param {item!r}; use key=value")
        node = out
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        if value.lower() in ("true", "false"):
            node[parts[-1]] = value.lower() == "true"
        elif value == "" or value.lower() == "null":
            node[parts[-1]] = None
        else:
            node[parts[-1]] = value
    return out


def _summary(command: str, data) -> str:
    if command == "status":
        t = data["totals"]
        lines = [f"{t['replays']:,} replays in the ledger, {len(data['windows'])} windows, "
                 f"{sum(1 for b in data['batches'] if b['validated'])} batches validated"]
        for j in data["jobs"]:
            if j["state"] in ("running", "paused", "stopping", "finishing"):
                p = j["progress"]
                lines.append(f"  {j['kind']} '{j['name']}' ({j['id']}): {j['state']}, {100 * p['fraction']:.0f}%")
        box = next((b for b in data["boxes"] if b.get("status")), None)
        if box and box["status_age_s"] is not None and box["status_age_s"] < 600:
            s = box["status"]
            lines.append(f"  box {box['name']}: {s['phase']} · {s['per_minute']}/min · reported {box['status_age_s']} s ago")
        return "\n".join(lines)
    if command == "jobs":
        return "\n".join(f"{j['id']}  {j['kind']:5} {j['state']:9} {100 * j['progress']['fraction']:3.0f}%  {j['name']}" for j in data) or "no jobs"
    if command == "job":
        j = data["job"]; p = data["progress"]
        head = f"{j['id']}  {j.get('kind', 'crawl')} '{j['name']}': {j['state']}  {100 * p['fraction']:.0f}%"
        runs = [f"  {r['action']} {r['started']} -> {r.get('outcome') or 'running'}" for r in j["runs"][-5:]]
        return "\n".join([head, *runs])
    return json.dumps(data, indent=1)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="rlreplay lab API client")
    parser.add_argument("--url", default=None, help="lab base URL; default $RLLAB_URL")
    parser.add_argument("--token", default=None, help="bearer token; default $RLLAB_TOKEN")
    parser.add_argument("--summary", action="store_true", help="a few lines instead of JSON (status, jobs, job)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status"); sub.add_parser("jobs"); sub.add_parser("ops"); sub.add_parser("endpoints"); sub.add_parser("image")
    sub.add_parser("windows"); sub.add_parser("batches")
    p = sub.add_parser("job"); p.add_argument("id")
    p = sub.add_parser("create"); p.add_argument("--kind", default="crawl", choices=["crawl", "build"])
    p.add_argument("--preset", default=None); p.add_argument("--name", default=None); p.add_argument("--param", action="append", default=[])
    p.add_argument("--start", action="store_true", help="start it right away")
    for action in ("start", "pause", "resume", "stop", "force-stop", "cancel"):
        p = sub.add_parser(action); p.add_argument("id")
    p = sub.add_parser("update"); p.add_argument("id"); p.add_argument("--name", default=None); p.add_argument("--param", action="append", default=[])
    p = sub.add_parser("log"); p.add_argument("op"); p.add_argument("--tail", type=int, default=200)
    p = sub.add_parser("estimate"); p.add_argument("--param", action="append", default=[])
    args = parser.parse_args(argv)
    client = LabClient(args.url, args.token)
    cmd = args.command
    if cmd == "status":
        data = client.status()
    elif cmd == "jobs":
        data = client.jobs()
    elif cmd == "job":
        data = client.job(args.id)
    elif cmd == "create":
        body = {"kind": args.kind, "params": _params(args.param)}
        if args.preset:
            body["preset"] = args.preset
        if args.name:
            body["name"] = args.name
        data = client.create(**body)
        if args.start:
            data = client.act(data["id"], "start")
    elif cmd in ("start", "pause", "resume", "stop", "force-stop", "cancel"):
        data = client.act(args.id, cmd.replace("-", "_"))
    elif cmd == "update":
        body = {"params": _params(args.param)} if args.param else {}
        if args.name:
            body["name"] = args.name
        data = client.act(args.id, "update", **body)
    elif cmd == "log":
        print(client.log(args.op, args.tail))
        return 0
    elif cmd == "estimate":
        data = client.post("/estimate", {"params": _params(args.param)})
    elif cmd == "ops":
        data = client.get("/ops")["ops"]
    elif cmd == "windows":
        data = client.get("/windows")["windows"]
    elif cmd == "batches":
        data = client.get("/batches")["batches"]
    elif cmd == "image":
        data = client.post("/ops", {"action": "image"})
    elif cmd == "endpoints":
        data = client.get("/openapi.json")["paths"]
    else:
        parser.error("unknown command")
    print(_summary(cmd, data) if args.summary else json.dumps(data, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
