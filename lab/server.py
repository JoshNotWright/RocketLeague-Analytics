"""The lab's control-plane UI: one page, standard library only, bound to the tailnet.

    python -m lab.server            (reads /etc/rlreplay/lab.env, or $RLREPLAY_LAB_ENV)

Shows what the lab holds (replays per playlist, band and month, decoded bytes), the batches the
box has pushed and whether each validated, the live status the box writes every cycle, and the
jobs (lab/jobs.py): a job is a saved set of crawl parameters with a lifecycle; starting one
creates the box with those parameters, pause/resume write the box's control file, stop pushes,
validates and deletes the box, continue starts a new box that resumes from the ledger. The
lab-side scripts run in the background with their logs captured under <LAB_ROOT>/ops. Secrets
never leave the env file: the page only sees whether each is set, and the settings editor can
only change the infrastructure keys (box name, server type, location).

There is no login: the server binds to the tailnet address from lab.env (LAB_UI_BIND) and the
tailnet is the perimeter. Never bind it to a routable interface.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .jobs import ACTIVE_STATES, PRESETS, JobStore, clean_params, estimate

REPO = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
SECRET_KEYS = ("HCLOUD_TOKEN", "BALLCHASING_API_KEY", "TS_AUTHKEY")
EDITABLE_KEYS = ("CRAWLER_NAME", "CRAWLER_TYPE", "CRAWLER_LOCATION")
SHOWN_KEYS = EDITABLE_KEYS + ("LAB_ROOT", "LAB_USER", "LAB_TS_HOST", "LAB_REPO", "HCLOUD_SSH_KEY")
SCRIPTS = {"up": "crawler_up.sh", "down": "crawler_down.sh", "image": "crawler_image.sh"}


def _bash() -> str:
    """The lab is Linux; on a Windows dev box prefer Git's bash over the WSL launcher."""
    if sys.platform == "win32":
        for candidate in ("C:/Program Files/Git/bin/bash.exe", "C:/Program Files/Git/usr/bin/bash.exe"):
            if Path(candidate).exists():
                return candidate
    return "bash"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_env(path) -> dict:
    """KEY=value lines; comments and blanks skipped; values keep everything up to an inline ' #'."""
    env = {}
    path = Path(path)
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.split(" #", 1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        env[key.strip()] = value
    return env


def write_env_values(path, updates: dict) -> None:
    """Rewrite only the given EDITABLE keys in place, keeping comments and every other line."""
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen = set()
    out = []
    for line in lines:
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else None
        if key in updates:
            comment = ""
            if " #" in line:
                comment = "  #" + line.split(" #", 1)[1]
            out.append(f"{key}={updates[key]}{comment}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        shutil.copymode(path, tmp)
    except OSError:
        pass
    os.replace(tmp, path)


class Inventory:
    """Replay counts per ledger file and decoded bytes per window, cached by file size and mtime."""

    def __init__(self, root: Path):
        self.root = root
        self._lines: dict = {}
        self._dirs: dict = {}

    def _count_lines(self, path: Path) -> int:
        st = path.stat()
        key = (st.st_size, st.st_mtime_ns)
        cached = self._lines.get(path)
        if cached and cached[0] == key:
            return cached[1]
        n = 0
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                n += chunk.count(b"\n")
        self._lines[path] = (key, n)
        return n

    def _dir_size(self, path: Path) -> tuple[int, int]:
        """(files, bytes); refreshed at most every 60 s per directory."""
        cached = self._dirs.get(path)
        if cached and time.time() - cached[0] < 60:
            return cached[1]
        files = size = 0
        if path.exists():
            with os.scandir(path) as it:
                for entry in it:
                    if entry.is_file() and entry.name.endswith(".npz"):
                        files += 1
                        size += entry.stat().st_size
        self._dirs[path] = (time.time(), (files, size))
        return files, size

    def windows(self) -> list:
        out = []
        meta = self.root / "meta"
        if not meta.exists():
            return out
        for ledger in sorted(meta.glob("*/*/*.jsonl")):
            playlist, band, month = ledger.parent.parent.name, ledger.parent.name, ledger.stem
            files, size = self._dir_size(self.root / "decoded" / playlist / band / month)
            state = {}
            state_path = ledger.with_name(f"{month}.state.json")
            if state_path.exists():
                try:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    state = {}
            out.append({"playlist": playlist, "band": band, "month": month, "replays": self._count_lines(ledger),
                        "decoded": files, "bytes": size, "listing_complete": bool(state.get("listing_complete")),
                        "updated": state.get("updated")})
        return out


class Lab:
    def __init__(self, env_path):
        self.env_path = Path(env_path)
        self.lock = threading.Lock()
        self.ops: list = []
        self._history_last: dict = {}
        self.inventory = None
        self.jobs = None
        self._refresh_env()

    def _refresh_env(self):
        self.env = read_env(self.env_path)
        self.root = Path(self.env.get("LAB_ROOT", "/tank/rl"))
        self.repo = Path(self.env.get("LAB_REPO", str(REPO)))
        if self.inventory is None or self.inventory.root != self.root:
            self.inventory = Inventory(self.root)
        if self.jobs is None or self.jobs.dir != self.root / "jobs":
            self.jobs = JobStore(self.root)

    @property
    def box_name(self) -> str:
        return self.env.get("CRAWLER_NAME") or "rl-crawler"

    # -- read --

    def batches(self) -> list:
        out = []
        for manifest in sorted((self.root / "batches").glob("*.manifest.json"), reverse=True):
            name = manifest.name[: -len(".manifest.json")]
            try:
                man = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                man = {}
            ok = manifest.with_name(f"{name}.ok")
            bad = manifest.with_name(f"{name}.bad")
            entry = {"name": name, "box": man.get("box"), "job": man.get("job"), "created": man.get("created"),
                     "count": len(man.get("items", [])), "failed": len(man.get("failed", [])),
                     "bytes": man.get("bytes", 0), "windows": [f"{e['band']}/{e['month']}" for e in man.get("ledger", [])],
                     "validated": None, "problems": None}
            if ok.exists():
                try:
                    entry["validated"] = json.loads(ok.read_text(encoding="utf-8")).get("validated_at")
                except (OSError, ValueError):
                    entry["validated"] = "yes"
            elif bad.exists():
                try:
                    entry["problems"] = json.loads(bad.read_text(encoding="utf-8")).get("problems", [])[:5]
                except (OSError, ValueError):
                    entry["problems"] = [{"error": "unreadable .bad file"}]
            out.append(entry)
        return out

    def _record_history(self, name: str, status: dict) -> list:
        """Append one point per new status report to boxes/<box>/history.jsonl; return the recent tail."""
        path = self.root / "boxes" / name / "history.jsonl"
        point = {"t": status.get("updated"), "job": status.get("job"), "rate": status.get("per_minute"), "saved": status.get("saved_this_run"),
                 "batch": (status.get("current") or {}).get("count"), "phase": status.get("phase"),
                 "unvalidated": len(status.get("unvalidated") or [])}
        last = self._history_last.get(name)
        if last is None and path.exists():
            try:
                last = json.loads(path.read_text(encoding="utf-8").splitlines()[-1]).get("t")
            except (OSError, ValueError, IndexError):
                last = None
        if point["t"] and point["t"] != last:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(point) + "\n")
            self._history_last[name] = point["t"]
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[-720:]
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def boxes(self) -> list:
        out = []
        boxes = self.root / "boxes"
        names = sorted(p.name for p in boxes.iterdir() if p.is_dir()) if boxes.exists() else []
        for name in names:
            status = control = None
            for fname, target in (("status.json", "status"), ("control.json", "control")):
                path = boxes / name / fname
                if path.exists():
                    try:
                        value = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        value = None
                    if target == "status":
                        status = value
                    else:
                        control = value
            age = None
            if status and status.get("updated"):
                try:
                    age = int(time.time() - datetime.strptime(status["updated"], "%Y-%m-%dT%H:%M:%SZ")
                              .replace(tzinfo=timezone.utc).timestamp())
                except ValueError:
                    age = None
            history = self._record_history(name, status) if status else []
            out.append({"name": name, "status": status, "control": control, "status_age_s": age, "history": history})
        return out

    def _job_summary(self, job: dict, windows: list) -> dict:
        prog = self.jobs.progress(job, windows)
        prog.pop("windows", None)
        return {"id": job["id"], "name": job["name"], "state": job["state"], "params": job["params"],
                "created": job["created"], "updated": job["updated"], "note": job.get("note", ""),
                "last_run": job["runs"][-1] if job["runs"] else None, "runs": len(job["runs"]), "progress": prog}

    def overview(self) -> dict:
        self._refresh_env()
        usage = shutil.disk_usage(self.root) if self.root.exists() else None
        windows = self.inventory.windows()
        boxes = self.boxes()
        self._reconcile(boxes)
        jobs = [self._job_summary(j, windows) for j in self.jobs.all()]
        active = next((j for j in jobs if j["state"] in ACTIVE_STATES), None)
        return {"now": _now(), "root": str(self.root), "box_name": self.box_name,
                "disk": {"free": usage.free, "total": usage.total} if usage else None,
                "totals": {"replays": sum(w["replays"] for w in windows), "decoded": sum(w["decoded"] for w in windows),
                           "bytes": sum(w["bytes"] for w in windows)},
                "windows": windows, "batches": self.batches(), "boxes": boxes,
                "jobs": jobs, "active_job": active["id"] if active else None, "presets": PRESETS,
                "config": {k: self.env.get(k, "") for k in SHOWN_KEYS},
                "secrets": {k: bool(self.env.get(k)) for k in SECRET_KEYS},
                "ops": self.ops[-30:]}

    def job_detail(self, job_id: str) -> dict:
        self._refresh_env()
        job = self.jobs.get(job_id)
        windows = self.inventory.windows()
        boxes = self.boxes()
        box = next((b for b in boxes if b["name"] == self.box_name), boxes[0] if boxes else None)
        box_for_job = box if box and box.get("status") and box["status"].get("job") == job_id else None
        history = [h for h in (box["history"] if box else []) if h.get("job") == job_id]
        return {"job": job, "progress": self.jobs.progress(job, windows),
                "batches": [b for b in self.batches() if b.get("job") == job_id],
                "box": box_for_job, "box_any": box, "history": history,
                "ops": [o for o in self.ops if o.get("job") == job_id][-20:], "now": _now()}

    def _reconcile(self, boxes: list) -> None:
        """A running job whose box reports every window complete is finished for it: push and delete."""
        job = self.jobs.active()
        if not job or job["state"] != "running":
            return
        box = next((b for b in boxes if b["name"] == self.box_name), None)
        status = box["status"] if box else None
        if not status or status.get("job") != job["id"] or status.get("phase") != "all windows complete":
            return
        if box.get("status_age_s") is not None and box["status_age_s"] > 3600:
            return
        if any(o["returncode"] is None for o in self.ops):
            return
        try:
            self._run_for_job(job["id"], "finishing", "down", [], "complete", "finish failed; use Stop or Force stop")
        except ValueError:
            pass

    # -- jobs --

    def _run_for_job(self, job_id: str, state: str, action: str, args: list, ok_state: str, fail_note: str) -> dict:
        previous = self.jobs.get(job_id)["state"]
        self.jobs.transition(job_id, state, box=self.box_name, action=action)

        def done(rc: int) -> None:
            if rc == 0:
                self.jobs.transition(job_id, ok_state, outcome="ok")
            else:
                self.jobs.transition(job_id, "failed", outcome=f"{fail_note} (exit {rc})")
        try:
            op = self.run_script(action, args, job=job_id, on_done=done)
        except ValueError:
            self.jobs.transition(job_id, previous if previous not in ACTIVE_STATES else "failed",
                                 outcome="could not start the script")
            raise
        job = self.jobs.get(job_id)
        job["runs"][-1]["op"] = op["id"]
        self.jobs._write(job)
        return job

    def job_action(self, payload: dict) -> dict:
        self._refresh_env()
        action = payload.get("action")
        job_id = payload.get("id", "")
        if action == "create":
            preset = PRESETS.get(payload.get("preset") or "")
            params = {**(preset["params"] if preset else {}), **(payload.get("params") or {})}
            name = payload.get("name") or (preset["name"] if preset else "")
            return self.jobs.create(name, params, payload.get("note", ""))
        if action == "estimate":
            return estimate(clean_params(payload.get("params") or {}))
        if action == "update":
            return self.jobs.update(job_id, payload.get("name"), payload.get("params"), payload.get("note"))
        job = self.jobs.get(job_id)
        if action == "start":
            if job["state"] in ACTIVE_STATES:
                raise ValueError(f"the job is already {job['state']}")
            other = self.jobs.active()
            if other:
                raise ValueError(f"'{other['name']}' is {other['state']}; stop it first")
            self.set_control(self.box_name, "run")
            # The previous box's last report would otherwise be shown as this job's until the new
            # box reports; history.jsonl keeps the points, the status file is the live view only.
            (self.root / "boxes" / self.box_name / "status.json").unlink(missing_ok=True)
            self._history_last.pop(self.box_name, None)
            return self._run_for_job(job_id, "running", "up", ["--job", job_id], "running", "start failed")
        if action == "pause":
            if job["state"] != "running":
                raise ValueError("only a running job can be paused")
            self.set_control(self.box_name, "pause")
            return self.jobs.transition(job_id, "paused")
        if action == "resume":
            if job["state"] != "paused":
                raise ValueError("only a paused job can be resumed")
            self.set_control(self.box_name, "run")
            return self.jobs.transition(job_id, "running")
        if action in ("stop", "force_stop"):
            if job["state"] not in ("running", "paused", "failed", "stopping", "finishing"):
                raise ValueError(f"nothing to stop: the job is {job['state']}")
            args = ["--force"] if action == "force_stop" else []
            return self._run_for_job(job_id, "stopping", "down", args, "stopped",
                                     "stop failed: unvalidated batches remain or the box is unreachable; Force stop deletes it anyway")
        if action == "cancel":
            self.jobs.delete(job_id)
            return {"deleted": job_id}
        raise ValueError("unknown job action")

    # -- write --

    def set_control(self, box: str, command: str) -> dict:
        if command not in ("run", "pause", "stop"):
            raise ValueError("command must be run, pause or stop")
        if not box or "/" in box or box.startswith("."):
            raise ValueError("bad box name")
        path = self.root / "boxes" / box / "control.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"command": command, "set_at": _now(), "by": "lab-ui"}
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return payload

    def update_config(self, updates: dict) -> dict:
        clean = {}
        for key, value in updates.items():
            if key not in EDITABLE_KEYS:
                raise ValueError(f"{key} is not editable here")
            value = str(value).strip()
            if "\n" in value or "\r" in value:
                raise ValueError("no newlines")
            clean[key] = value
        with self.lock:
            write_env_values(self.env_path, clean)
            self._refresh_env()
        return {k: self.env.get(k, "") for k in SHOWN_KEYS}

    def run_script(self, action: str, args: list | None = None, job: str | None = None, on_done=None) -> dict:
        if action not in SCRIPTS:
            raise ValueError("unknown action")
        args = [str(a) for a in (args or []) if str(a).startswith("--") or str(a).replace("-", "").isalnum()]
        ops_dir = self.root / "ops"
        ops_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        log_path = ops_dir / f"{stamp}-{action}.log"
        script = self.repo / "scripts" / SCRIPTS[action]
        if not script.exists():
            raise ValueError(f"{script} is missing")
        env = dict(os.environ, RLREPLAY_LAB_ENV=str(self.env_path))
        with self.lock:
            running = [o for o in self.ops if o["returncode"] is None]
            if running:
                raise ValueError(f"{running[0]['action']} is still running")
            handle = open(log_path, "wb")
            proc = subprocess.Popen([_bash(), str(script), *args], stdout=handle, stderr=subprocess.STDOUT,
                                    env=env, cwd=str(self.repo))
            op = {"id": stamp, "action": action, "args": args, "job": job, "started": _now(), "finished": None,
                  "returncode": None, "log": str(log_path)}
            self.ops.append(op)

        def wait():
            proc.wait()
            handle.close()
            op["returncode"] = proc.returncode
            op["finished"] = _now()
            if on_done is not None:
                try:
                    on_done(proc.returncode)
                except Exception as exc:  # noqa: BLE001
                    sys.stderr.write(f"job callback failed: {exc}\n")
        threading.Thread(target=wait, daemon=True).start()
        return op

    def op_log(self, op_id: str, tail: int = 200) -> str:
        for op in self.ops:
            if op["id"] == op_id:
                try:
                    lines = Path(op["log"]).read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    return ""
                return "\n".join(lines[-tail:])
        raise ValueError("unknown op")


class Handler(BaseHTTPRequestHandler):
    lab: Lab = None  # set by serve()

    def log_message(self, fmt, *args):  # quieter than the default, still useful
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1 << 20:
            raise ValueError("body too large")
        raw = self.rfile.read(length) if length else b"{}"
        payload = json.loads(raw or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("expected an object")
        return payload

    def do_GET(self):
        url = urlsplit(self.path)
        try:
            if url.path in ("/", "/index.html"):
                body = (STATIC / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif url.path == "/api/overview":
                self._json(200, self.lab.overview())
            elif url.path == "/api/job":
                self._json(200, self.lab.job_detail(parse_qs(url.query).get("id", [""])[0]))
            elif url.path == "/api/log":
                q = parse_qs(url.query)
                self._json(200, {"log": self.lab.op_log(q.get("op", [""])[0], int(q.get("tail", ["200"])[0]))})
            else:
                self._json(404, {"error": "not found"})
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_POST(self):
        url = urlsplit(self.path)
        try:
            payload = self._read_json()
            if url.path == "/api/control":
                self._json(200, self.lab.set_control(payload.get("box", ""), payload.get("command", "")))
            elif url.path == "/api/jobs":
                self._json(200, self.lab.job_action(payload))
            elif url.path == "/api/config":
                self._json(200, self.lab.update_config(payload.get("values", {})))
            elif url.path == "/api/run":
                self._json(200, self.lab.run_script(payload.get("action", ""), payload.get("args", [])))
            else:
                self._json(404, {"error": "not found"})
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})


def serve(env_path, bind: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    lab = Lab(env_path)
    bind = bind or lab.env.get("LAB_UI_BIND") or "127.0.0.1"
    port = int(lab.env.get("LAB_UI_PORT") or 8731) if port is None else int(port)   # 0 = any free port (tests)
    if bind == "0.0.0.0":
        raise SystemExit("refusing to bind the lab UI to every interface; set LAB_UI_BIND to the tailnet address")
    Handler.lab = lab
    server = ThreadingHTTPServer((bind, port), Handler)
    server.daemon_threads = True
    return server


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="rlreplay lab UI")
    parser.add_argument("--env", default=os.environ.get("RLREPLAY_LAB_ENV", "/etc/rlreplay/lab.env"))
    parser.add_argument("--bind", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)
    server = serve(args.env, args.bind, args.port)
    host, port = server.server_address[:2]
    print(f"lab UI on http://{host}:{port}/ (env {args.env})", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
