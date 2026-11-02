"""Build the model tables from the archive, per window, incrementally. Runs on the lab.

    .venv/bin/python -m lab.build --root <LAB_ROOT> --job <id> [--tables frame,touches,decisions,table]
        [--horizon 2.5] [--hz 5] [--playlist ranked-doubles] [--bands diamond,gc] [--months 2025-07..2025-09]
        [--force] [--workers N]

For every window ``decoded/<playlist>/<band>/<month>/`` that holds decoded replays, the tables:

    features/frame/<p>/<b>/<month>.parquet        per-player frame features   (rlreplay extract --decoded)
    features/touches/<p>/<b>/<month>.parquet      touch rows for xG and value (rlreplay touches --decoded)
    features/decisions/<p>/<b>/<month>/           decision rows, one parquet per replay (rlreplay decisions-build)
    features/table/<p>/<b>/<month>.parquet        player-game rows from the ledger (rlreplay meta-table)

Incremental: a window's table is rebuilt only when the number of decoded replays (or ledger
lines, for ``table``) differs from what ``features/build_state.json`` recorded, and extract /
touches / decisions keep a per-replay cache, so a rebuild only parses what is new. ``--force``
drops the caches and rebuilds everything selected.

Progress goes to ``builds/<job>.json`` after every step so the lab UI can show it. SIGTERM
finishes the current step and exits with the phase ``stopped``.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

TABLES = ("frame", "touches", "decisions", "table")


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


def find_windows(root: Path, playlist: str | None = None, bands: list | None = None, months: list | None = None) -> list:
    """(playlist, band, month) for every decoded window, filtered; months outer, bands inner."""
    out = []
    decoded = root / "decoded"
    if not decoded.exists():
        return out
    for p in sorted(decoded.iterdir()):
        if not p.is_dir() or (playlist and p.name != playlist):
            continue
        for b in sorted(p.iterdir()):
            if not b.is_dir() or (bands and b.name not in bands):
                continue
            for m in sorted(b.iterdir()):
                if not m.is_dir() or (months and m.name not in months):
                    continue
                if any(f.suffix == ".npz" for f in m.iterdir()):
                    out.append((p.name, b.name, m.name))
    out.sort(key=lambda w: (w[2], w[0], w[1]))
    return out


def count_npz(window_dir: Path) -> int:
    return sum(1 for f in os.scandir(window_dir) if f.is_file() and f.name.endswith(".npz"))


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    n = 0
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            n += chunk.count(b"\n")
    return n


class Builder:
    def __init__(self, root: Path, job: str | None, tables, *, horizon: float = 2.5, hz: float = 5.0,
                 workers: int | None = None, force: bool = False, python: str | None = None, log=print,
                 runner=None):
        self.root = Path(root)
        self.job = job
        self.tables = [t for t in TABLES if t in tables]
        self.horizon, self.hz, self.workers, self.force = horizon, hz, workers, force
        self.python = python or sys.executable
        self.log = log
        self.runner = runner or self._run_cli      # tests inject a fake
        self.features = self.root / "features"
        self.state_path = self.features / "build_state.json"
        self.state = self._load_state()
        self.progress_path = self.root / "builds" / f"{job}.json" if job else None
        self.progress = {"job": job, "phase": "starting", "started": _now(), "updated": _now(), "total": 0, "done": 0,
                         "current": None, "windows": [], "errors": [], "tables": self.tables}
        self.stop_requested = False

    # -- state --

    def _load_state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        self.features.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.state, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _save_progress(self, phase: str | None = None) -> None:
        if phase:
            self.progress["phase"] = phase
        self.progress["updated"] = _now()
        if self.progress_path:
            self.progress_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.progress_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.progress, indent=1), encoding="utf-8")
            os.replace(tmp, self.progress_path)

    # -- paths --

    def window_dir(self, w) -> Path:
        return self.root / "decoded" / w[0] / w[1] / w[2]

    def ledger(self, w) -> Path:
        return self.root / "meta" / w[0] / w[1] / f"{w[2]}.jsonl"

    def out_path(self, table: str, w) -> Path:
        base = self.features / table / w[0] / w[1]
        return base / w[2] if table == "decisions" else base / f"{w[2]}.parquet"

    def key(self, w) -> str:
        return f"{w[0]}/{w[1]}/{w[2]}"

    # -- what is stale --

    def source_count(self, table: str, w) -> int:
        return count_lines(self.ledger(w)) if table == "table" else count_npz(self.window_dir(w))

    def is_current(self, table: str, w) -> bool:
        if self.force:
            return False
        rec = (self.state.get(self.key(w)) or {}).get(table)
        return bool(rec and rec.get("count") == self.source_count(table, w) and self.out_path(table, w).exists())

    # -- the steps --

    def _run_cli(self, args: list) -> None:
        cmd = [self.python, "-m", "rlreplay", *args]
        self.log("$ " + " ".join(str(a) for a in cmd))
        completed = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        tail = "\n".join(completed.stdout.strip().splitlines()[-5:])
        if tail:
            self.log(tail)
        if completed.returncode != 0:
            raise RuntimeError(f"rlreplay {args[0]} failed ({completed.returncode})")

    def build_one(self, table: str, w) -> None:
        out = self.out_path(table, w)
        out.parent.mkdir(parents=True, exist_ok=True)
        workers = ["--workers", str(self.workers)] if self.workers else []
        if self.force:
            parts = out.with_name(out.stem + ".parts") if table != "decisions" else out
            if parts.exists():
                shutil.rmtree(parts, ignore_errors=True)
        if table == "frame":
            self.runner(["extract", "--decoded", str(self.window_dir(w)), "--out", str(out), *workers])
        elif table == "touches":
            self.runner(["touches", "--decoded", str(self.window_dir(w)), "--out", str(out), *workers])
        elif table == "decisions":
            self.runner(["decisions-build", "--decoded", str(self.window_dir(w)), "--out", str(out),
                         "--horizon", str(self.horizon), "--hz", str(self.hz), *workers])
        elif table == "table":
            self.runner(["meta-table", "--meta", str(self.ledger(w)), "--out", str(out)])
        self.state.setdefault(self.key(w), {})[table] = {"count": self.source_count(table, w), "built": _now()}
        self._save_state()

    def plan(self, windows: list) -> list:
        """(table, window) steps still to do, windows outer so every table of a month lands together."""
        steps = []
        for w in windows:
            for table in self.tables:
                if table == "table" and not self.ledger(w).exists():
                    continue
                if not self.is_current(table, w):
                    steps.append((table, w))
        return steps

    def run(self, windows: list) -> int:
        steps = self.plan(windows)
        self.progress.update({"total": len(steps), "done": 0,
                              "windows": [{"key": self.key(w), "replays": count_npz(self.window_dir(w)),
                                           "tables": {t: ("current" if self.is_current(t, w) else "pending") for t in self.tables}}
                                          for w in windows]})
        self._save_progress("running")
        self.log(f"{len(windows)} windows, {len(steps)} table builds to do")
        by_key = {row["key"]: row for row in self.progress["windows"]}
        for table, w in steps:
            if self.stop_requested:
                self._save_progress("stopped")
                self.log("stopped before " + f"{table} {self.key(w)}")
                return 0
            self.progress["current"] = f"{table} {self.key(w)}"
            by_key[self.key(w)]["tables"][table] = "building"
            self._save_progress()
            started = time.time()
            try:
                self.build_one(table, w)
                by_key[self.key(w)]["tables"][table] = "done"
                self.log(f"{table} {self.key(w)}: {time.time() - started:.0f}s")
            except Exception as exc:  # noqa: BLE001 - record, carry on with the next step
                by_key[self.key(w)]["tables"][table] = "failed"
                self.progress["errors"].append({"step": f"{table} {self.key(w)}", "error": str(exc)[:300]})
                self.log(f"{table} {self.key(w)} FAILED: {exc}")
            self.progress["done"] += 1
            self._save_progress()
        self.progress["current"] = None
        self._save_progress("failed" if self.progress["errors"] else "complete")
        self.log(f"done: {self.progress['done']} steps, {len(self.progress['errors'])} failed")
        return 1 if self.progress["errors"] else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="build model tables from the lab archive")
    parser.add_argument("--root", required=True)
    parser.add_argument("--job", default=None)
    parser.add_argument("--tables", default=",".join(TABLES))
    parser.add_argument("--horizon", type=float, default=2.5)
    parser.add_argument("--hz", type=float, default=5.0)
    parser.add_argument("--playlist", default=None)
    parser.add_argument("--bands", default=None, help="comma-separated band names; default all")
    parser.add_argument("--months", default=None, help="first..last; default all")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args(argv)
    tables = [t.strip() for t in args.tables.split(",") if t.strip()]
    bad = [t for t in tables if t not in TABLES]
    if bad:
        parser.error(f"unknown tables {bad}; choose from {', '.join(TABLES)}")
    months = None
    if args.months:
        first, _, last = args.months.partition("..")
        months = months_between(first, last or first)
    bands = [b.strip() for b in args.bands.split(",") if b.strip()] if args.bands else None
    builder = Builder(Path(args.root), args.job, tables, horizon=args.horizon, hz=args.hz, workers=args.workers,
                      force=args.force, log=lambda m: print(f"[build {_now()}] {m}", flush=True))

    def on_term(signum, frame):  # noqa: ARG001
        builder.stop_requested = True
        builder.log("SIGTERM: finishing the current step")
    try:
        signal.signal(signal.SIGTERM, on_term)
    except (ValueError, OSError):
        pass
    windows = find_windows(builder.root, args.playlist, bands, months)
    return builder.run(windows)


if __name__ == "__main__":
    sys.exit(main())
