"""Jobs: a named, saved set of crawl parameters with a lifecycle, stored under <LAB_ROOT>/jobs.

A job is the unit you start, pause, stop and continue. The box is disposable; the job is not.
Resumption works because the ledger (meta/) is per window and lives on the lab: a job that is
continued starts a new box, which pulls the ledger and carries on where the previous box got to.

    jobs/<id>.json     {"id", "name", "state", "params", "runs", "created", "updated", "note"}

States
    draft      saved, never started
    running    a box is up (or being created) and crawling
    paused     the box is up but not fetching (control.json = pause)
    stopping   crawler_down.sh is running: final push, validation, delete
    stopped    no box; can be continued or cancelled
    finishing  the box reported every window complete; crawler_down.sh is running
    complete   every window done, box deleted
    failed     the last start or stop script failed; see the run's outcome

Stop destroys the box (safely: it pushes and validates first). Pause leaves it running.
Only one job may be running, paused, stopping or finishing at a time: one box, one key.
"""
from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

ACTIVE_STATES = ("running", "paused", "stopping", "finishing")
SERVER_PRICE_PER_HOUR = {"cpx22": 0.032, "cpx32": 0.064, "cpx42": 0.125}   # USD, approximate
REPLAYS_PER_HOUR = 6500          # a GC key sustains 2 files/s; this allows for listing and decode gaps
BAND_PRESETS = {
    "diamond": ("diamond-1", "diamond-3"),
    "champion": ("champion-1", "champion-3"),
    "gc": ("grand-champion-1", "grand-champion-3"),
    "ssl": ("supersonic-legend", "supersonic-legend"),
    "platinum": ("platinum-1", "platinum-3"),
}
DEFAULT_PARAMS = {
    "playlist": "ranked-doubles",
    "bands": "diamond:diamond-1:diamond-3,champion:champion-1:champion-3,gc:grand-champion-1:grand-champion-3",
    "months": {"first": "2025-07", "last": "2026-10"},
    "per_window": None,
    "batch_size": 20000,
    "workers": 4,
    "server_type": "cpx32",
    "location": "fsn1",
}
PRESETS = {
    "smoke": {"name": "Smoke test", "params": {**DEFAULT_PARAMS, "months": {"first": "2025-07", "last": "2025-07"},
                                                "per_window": 300, "batch_size": 500}},
    "full": {"name": "Full crawl", "params": dict(DEFAULT_PARAMS)},
}
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_ID_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # no 0/o/1/l/i: ids are read aloud and typed


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def months_between(first: str, last: str) -> list:
    y, m = int(first[:4]), int(first[5:7])
    ly, lm = int(last[:4]), int(last[5:7])
    out = []
    while (y, m) <= (ly, lm):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def parse_bands(text: str) -> list:
    """'diamond:diamond-1:diamond-3,gc:...' -> [{"name", "min", "max"}]; a bare preset name expands."""
    out = []
    for raw in str(text).split(","):
        raw = raw.strip()
        if not raw:
            continue
        parts = raw.split(":")
        if len(parts) == 1 and parts[0] in BAND_PRESETS:
            lo, hi = BAND_PRESETS[parts[0]]
            out.append({"name": parts[0], "min": lo, "max": hi})
        elif len(parts) == 3 and all(parts):
            out.append({"name": parts[0], "min": parts[1], "max": parts[2]})
        else:
            raise ValueError(f"band '{raw}' must be name:min-rank:max-rank")
    if not out:
        raise ValueError("at least one rank band")
    names = [b["name"] for b in out]
    if len(set(names)) != len(names):
        raise ValueError("band names must be unique")
    return out


def clean_params(raw: dict) -> dict:
    """Validate user-supplied parameters; unknown keys are dropped, missing ones take the defaults."""
    p = dict(DEFAULT_PARAMS)
    p["months"] = dict(DEFAULT_PARAMS["months"])
    raw = raw or {}
    if "playlist" in raw:
        v = str(raw["playlist"]).strip()
        if not re.fullmatch(r"[a-z0-9-]{3,40}", v):
            raise ValueError("playlist must be a ballchasing playlist id such as ranked-doubles")
        p["playlist"] = v
    if "bands" in raw:
        bands = parse_bands(raw["bands"]) if isinstance(raw["bands"], str) else raw["bands"]
        for b in bands:
            if not all(re.fullmatch(r"[a-z0-9-]{1,40}", str(b.get(k, ""))) for k in ("name", "min", "max")):
                raise ValueError("band names and ranks are lowercase ballchasing ids")
        p["bands"] = ",".join(f"{b['name']}:{b['min']}:{b['max']}" for b in bands)
    months = raw.get("months")
    if months:
        first, last = str(months.get("first", "")).strip(), str(months.get("last", "")).strip()
        if not (_MONTH.match(first) and _MONTH.match(last)) or first > last:
            raise ValueError("months must look like 2025-07 and be in order")
        if len(months_between(first, last)) > 60:
            raise ValueError("at most 60 months per job")
        p["months"] = {"first": first, "last": last}
    if "per_window" in raw:
        v = raw["per_window"]
        if v in (None, "", "null"):
            p["per_window"] = None
        else:
            v = int(v)
            if not 1 <= v <= 1_000_000:
                raise ValueError("cap per window must be between 1 and 1,000,000")
            p["per_window"] = v
    for key, lo, hi in (("batch_size", 10, 100_000), ("workers", 1, 16)):
        if key in raw:
            v = int(raw[key])
            if not lo <= v <= hi:
                raise ValueError(f"{key} must be between {lo} and {hi}")
            p[key] = v
    for key in ("server_type", "location"):
        if key in raw:
            v = str(raw[key]).strip()
            if not re.fullmatch(r"[a-z0-9]{2,12}", v):
                raise ValueError(f"{key} looks wrong")
            p[key] = v
    return p


def windows_of(params: dict) -> list:
    """Months outer, bands inner: the order the box crawls them."""
    bands = parse_bands(params["bands"])
    return [{"playlist": params["playlist"], "band": b["name"], "month": m}
            for m in months_between(params["months"]["first"], params["months"]["last"]) for b in bands]


def estimate(params: dict) -> dict:
    """What a job will roughly cost and take; None where the cap is open-ended."""
    wins = windows_of(params)
    cap = params.get("per_window")
    replays = cap * len(wins) if cap else None
    hours = replays / REPLAYS_PER_HOUR if replays else None
    price = SERVER_PRICE_PER_HOUR.get(params.get("server_type"), 0.064)
    return {"windows": len(wins), "bands": len(parse_bands(params["bands"])),
            "months": len(months_between(params["months"]["first"], params["months"]["last"])),
            "max_replays": replays, "hours": round(hours, 1) if hours else None,
            "cost_usd": round(hours * price + 0.05, 2) if hours else None,
            "price_per_hour": price, "decoded_gb": round(replays * 0.0008, 1) if replays else None}


def new_id() -> str:
    """Six characters, opaque: the id is for URLs and files, the name is for people."""
    return "".join(secrets.choice(_ID_ALPHABET) for _ in range(6))


class JobStore:
    def __init__(self, root: Path):
        self.dir = Path(root) / "jobs"

    # -- files --

    def _path(self, job_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,60}", job_id or ""):
            raise ValueError("bad job id")
        return self.dir / f"{job_id}.json"

    def _write(self, job: dict) -> dict:
        job["updated"] = _now()
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self._path(job["id"])
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(job, indent=1), encoding="utf-8")
        os.replace(tmp, path)
        return job

    def get(self, job_id: str) -> dict:
        path = self._path(job_id)
        if not path.exists():
            raise ValueError(f"no job {job_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def all(self) -> list:
        out = []
        if self.dir.exists():
            for path in self.dir.glob("*.json"):
                try:
                    out.append(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    continue
        out.sort(key=lambda j: (j.get("state") not in ACTIVE_STATES, j.get("created", "")), reverse=False)
        # active first, then newest first
        active = [j for j in out if j.get("state") in ACTIVE_STATES]
        rest = sorted([j for j in out if j.get("state") not in ACTIVE_STATES], key=lambda j: j.get("created", ""), reverse=True)
        return active + rest

    def active(self) -> dict | None:
        for job in self.all():
            if job.get("state") in ACTIVE_STATES:
                return job
        return None

    # -- lifecycle --

    def create(self, name: str, params: dict, note: str = "") -> dict:
        name = str(name or "").strip()
        if not 1 <= len(name) <= 60:
            raise ValueError("a job needs a name of up to 60 characters")
        params = clean_params(params)
        job_id = new_id()
        while self._path(job_id).exists():
            job_id = new_id()
        job = {"id": job_id, "name": name, "state": "draft", "params": params, "runs": [],
               "created": _now(), "updated": None, "note": str(note or "")[:500]}
        return self._write(job)

    def update(self, job_id: str, name: str | None = None, params: dict | None = None, note: str | None = None) -> dict:
        job = self.get(job_id)
        if job["state"] in ACTIVE_STATES and params is not None:
            raise ValueError("stop the job before changing what it crawls")
        if name is not None:
            name = str(name).strip()
            if not 1 <= len(name) <= 60:
                raise ValueError("a job needs a name of up to 60 characters")
            job["name"] = name
        if params is not None:
            job["params"] = clean_params({**job["params"], **params})
        if note is not None:
            job["note"] = str(note)[:500]
        return self._write(job)

    def transition(self, job_id: str, state: str, outcome: str | None = None, **run_fields) -> dict:
        job = self.get(job_id)
        if state in ("running", "finishing", "stopping") and state != job["state"]:
            other = self.active()
            if other and other["id"] != job_id and state == "running":
                raise ValueError(f"'{other['name']}' is {other['state']}; stop it before starting another job")
        job["state"] = state
        if run_fields:
            job["runs"].append({"started": _now(), "ended": None, "outcome": None, **run_fields})
        if outcome is not None and job["runs"]:
            job["runs"][-1]["ended"] = _now()
            job["runs"][-1]["outcome"] = outcome
        return self._write(job)

    def delete(self, job_id: str) -> None:
        job = self.get(job_id)
        if job["state"] in ACTIVE_STATES:
            raise ValueError("stop the job before cancelling it")
        self._path(job_id).unlink()

    # -- progress against the ledger --

    def progress(self, job: dict, ledger_windows: list) -> dict:
        """Replays and completion per window from the lab's ledger; targets from the cap."""
        by_key = {(w["playlist"], w["band"], w["month"]): w for w in ledger_windows}
        cap = job["params"].get("per_window")
        rows = []
        for w in windows_of(job["params"]):
            have = by_key.get((w["playlist"], w["band"], w["month"]), {})
            replays = int(have.get("replays", 0))
            listing_complete = bool(have.get("listing_complete"))
            done = listing_complete or (cap is not None and replays >= cap)
            rows.append({**w, "replays": replays, "decoded": int(have.get("decoded", 0)), "bytes": int(have.get("bytes", 0)),
                         "target": cap, "done": done, "listing_complete": listing_complete})
        total = sum(r["replays"] for r in rows)
        target = cap * len(rows) if cap else None
        fraction = (min(1.0, sum(min(r["replays"], cap) for r in rows) / target) if target
                    else (sum(r["done"] for r in rows) / len(rows) if rows else 0.0))
        return {"windows": rows, "replays": total, "decoded": sum(r["decoded"] for r in rows),
                "bytes": sum(r["bytes"] for r in rows), "target": target,
                "windows_done": sum(r["done"] for r in rows), "fraction": round(fraction, 4),
                "estimate": estimate(job["params"])}


def shell_exports(job: dict) -> str:
    """The CRAWLER_* assignments crawler_up.sh needs, from a job file (consumed with eval)."""
    p = job["params"]
    vals = {"CRAWLER_JOB": job["id"], "CRAWLER_PLAYLIST": p["playlist"], "CRAWLER_BANDS": p["bands"],
            "CRAWLER_MONTHS": f"{p['months']['first']}..{p['months']['last']}",
            "CRAWLER_PER_WINDOW": "" if p.get("per_window") is None else str(p["per_window"]),
            "CRAWLER_BATCH": str(p["batch_size"]), "CRAWLER_WORKERS": str(p["workers"]),
            "CRAWLER_TYPE": p["server_type"], "CRAWLER_LOCATION": p["location"]}
    for v in vals.values():
        if not re.fullmatch(r"[A-Za-z0-9._:,-]*", v):
            raise ValueError("job parameters contain characters that are not allowed")
    return "\n".join(f"export {k}='{v}'" for k, v in vals.items()) + "\n"


def main(argv=None) -> int:
    """CLI used by crawler_up.sh:  python3 -m lab.jobs exports <LAB_ROOT> <job-id>"""
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 3 and argv[0] == "exports":
        store = JobStore(Path(argv[1]))
        sys.stdout.write(shell_exports(store.get(argv[2])))
        return 0
    print(main.__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
