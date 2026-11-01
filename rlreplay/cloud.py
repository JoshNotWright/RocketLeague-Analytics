"""The ephemeral cloud crawler's batch loop: crawl -> decode -> manifest -> push -> validate -> cleanup.

Runs on the box as ``rlreplay-crawler.service`` (``scripts/crawler_batch.py run``). The box is
disposable: the ledger (metadata lines + crawl state) and the decoded arrays live on the lab,
the box only ever holds the batch it is working on plus any batch the lab has not validated
yet. See docs/HANDOFF_SEEDBOX_PIPELINE.md and docs/CLOUD_CRAWLER.md.

Layout on the box (``work``)::

    crawl/<playlist>/<band>/<yyyy-mm>/      metadata.json, crawl_state.json, <id>.replay,
                                            decoded/<id>.npz (+ <id>.meta.json from rldecode)
    batches/<batch>.manifest.json           sealed batches; <batch>.ok / .bad mirrored from the lab
    batches/current.json                    ids decoded but not yet sealed
    status.json                             what the lab UI shows; pushed every cycle
    control.json                            pulled from the lab every cycle: {"command": run|pause|stop}

Layout on the lab (``lab.remote``)::

    decoded/<playlist>/<band>/<yyyy-mm>/<id>.npz
    meta/<playlist>/<band>/<yyyy-mm>.jsonl           the ledger (master copy)
    meta/<playlist>/<band>/<yyyy-mm>.state.json      crawl cursor per window
    batches/<batch>.manifest.json, .ok, .bad
    boxes/<box>/status.json, control.json

Nothing on the box is deleted until the lab has written ``<batch>.ok`` for it, and the ledger
is never deleted at all. The loop is cooperative: SIGTERM, ``control.json`` and the disk guard
are all polled between crawl pages, so a stop leaves the window cursor on its page.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .crawl import METADATA_FILE, STATE_FILE, Crawler, MissingKeyError, build_filters, metadata_ids

LEDGER_ONLY = True
PAGE = 200  # replays per crawl call; the control file and the disk guard are checked between calls


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(message: str) -> None:
    print(f"[batch {_now()}] {message}", file=sys.stderr, flush=True)


def sha256_of(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def month_bounds(month: str) -> tuple[str, str]:
    """'2025-07' -> ('2025-07-01T00:00:00Z', '2025-08-01T00:00:00Z')."""
    year, mon = (int(x) for x in month.split("-"))
    nxt = f"{year + 1}-01" if mon == 12 else f"{year}-{mon + 1:02d}"
    return f"{month}-01T00:00:00Z", f"{nxt}-01T00:00:00Z"


def months_between(first: str, last: str) -> list[str]:
    out = []
    y, m = (int(x) for x in first.split("-"))
    ly, lm = (int(x) for x in last.split("-"))
    while (y, m) <= (ly, lm):
        out.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


# ---- configuration -------------------------------------------------------------------------

@dataclass
class Window:
    playlist: str
    band: str
    month: str
    min_rank: str | None
    max_rank: str | None

    @property
    def key(self) -> str:
        return f"{self.playlist}/{self.band}/{self.month}"

    def filters(self) -> dict:
        after, before = month_bounds(self.month)
        return build_filters(playlist=self.playlist, min_rank=self.min_rank, max_rank=self.max_rank,
                             after=after, before=before, sort_dir="desc")


@dataclass
class Config:
    box: str
    work: Path
    lab_remote: str                # rclone remote path, e.g. "lab:/tank/rl"
    lab_ssh: str | None            # "user@host" over the tailnet; None = no validation trigger
    lab_validate: str              # command on the lab, given "<lab_root> <batch>" as arguments
    lab_root: str                  # the lab-side path the validator reads (same tree as lab_remote)
    playlist: str = "ranked-doubles"
    bands: list = field(default_factory=list)   # [{"name", "min", "max"}]
    months: list = field(default_factory=list)  # ["2025-07", ...]
    per_window: int | None = None  # cap on replays saved per window; None = everything listed
    batch_size: int = 20000
    workers: int = 4
    tier: str = "gc"
    max_unvalidated: int = 4
    min_free_gb: float = 25.0
    status_every: float = 30.0
    retry_after: float = 600.0     # seconds before an unvalidated batch is pushed again
    rclone: str = "rclone"
    ssh: str = "ssh"

    @classmethod
    def load(cls, path) -> "Config":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        lab = raw.get("lab", {})
        months = raw.get("months")
        if isinstance(months, dict):
            months = months_between(months["first"], months["last"])
        return cls(box=raw.get("box") or socket.gethostname(), work=Path(raw["work"]),
                   lab_remote=lab["remote"], lab_ssh=lab.get("ssh"),
                   lab_validate=lab.get("validate", "python3 /opt/rlreplay/scripts/lab_validate.py"),
                   lab_root=lab.get("root") or lab["remote"].split(":", 1)[-1],
                   playlist=raw.get("playlist", "ranked-doubles"), bands=raw.get("bands", []),
                   months=months or [], per_window=raw.get("per_window"),
                   batch_size=int(raw.get("batch_size", 20000)), workers=int(raw.get("workers", 4)),
                   tier=raw.get("tier", "gc"), max_unvalidated=int(raw.get("max_unvalidated", 4)),
                   min_free_gb=float(raw.get("min_free_gb", 25.0)), status_every=float(raw.get("status_every", 30.0)),
                   retry_after=float(raw.get("retry_after", 600.0)),
                   rclone=raw.get("rclone", "rclone"), ssh=raw.get("ssh", "ssh"))

    def windows(self) -> list[Window]:
        """Months outer, bands inner, so every band advances together and an early stop is balanced."""
        return [Window(self.playlist, b["name"], month, b.get("min"), b.get("max"))
                for month in self.months for b in self.bands]

    def window_dir(self, w: Window) -> Path:
        return self.work / "crawl" / w.playlist / w.band / w.month

    @staticmethod
    def lab_meta(w: Window) -> str:
        return f"meta/{w.playlist}/{w.band}/{w.month}.jsonl"

    @staticmethod
    def lab_state(w: Window) -> str:
        return f"meta/{w.playlist}/{w.band}/{w.month}.state.json"

    @staticmethod
    def lab_decoded(w: Window) -> str:
        return f"decoded/{w.playlist}/{w.band}/{w.month}"


# ---- transport -------------------------------------------------------------------------------

class Transport:
    """rclone for files, ssh for the validation trigger. Every method raises on failure."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def _run(self, args: list, *, timeout: float = 3600.0) -> str:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        if completed.returncode != 0:
            raise RuntimeError(f"{args[0]} failed ({completed.returncode}): {completed.stderr.strip()[:500]}")
        return completed.stdout

    def copy_files(self, src_dir: Path, names: list, dest_subdir: str) -> None:
        if not names:
            return
        listing = src_dir / ".push_list"
        listing.write_text("".join(n + "\n" for n in names), encoding="utf-8")
        try:
            self._run([self.cfg.rclone, "copy", "--files-from", str(listing), str(src_dir),
                       f"{self.cfg.lab_remote}/{dest_subdir}", "--transfers", "8", "--checkers", "8"])
        finally:
            listing.unlink(missing_ok=True)

    def copy_to(self, src: Path, dest_path: str) -> None:
        self._run([self.cfg.rclone, "copyto", str(src), f"{self.cfg.lab_remote}/{dest_path}"])

    def copy_from(self, remote_path: str, dest: Path) -> bool:
        """False when the remote file does not exist."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run([self.cfg.rclone, "copyto", f"{self.cfg.lab_remote}/{remote_path}", str(dest)],
                                   capture_output=True, text=True, timeout=600)
        return completed.returncode == 0 and dest.exists()

    def listdir(self, remote_dir: str) -> list:
        completed = subprocess.run([self.cfg.rclone, "lsf", f"{self.cfg.lab_remote}/{remote_dir}"],
                                   capture_output=True, text=True, timeout=600)
        if completed.returncode != 0:
            return []
        return [line.strip() for line in completed.stdout.splitlines() if line.strip()]

    def validate(self, batch: str) -> bool:
        """Run the lab-side validator for one batch; True if it wrote the .ok."""
        if not self.cfg.lab_ssh:
            return False
        completed = subprocess.run([self.cfg.ssh, "-o", "BatchMode=yes", self.cfg.lab_ssh,
                                    f"{self.cfg.lab_validate} {self.cfg.lab_root} {batch}"],
                                   capture_output=True, text=True, timeout=7200)
        return completed.returncode == 0


# ---- the loop --------------------------------------------------------------------------------

class BatchRunner:
    def __init__(self, cfg: Config, *, transport: Transport | None = None, crawler_factory=None,
                 decoder=None, sleep=time.sleep, log=_log):
        self.cfg = cfg
        self.transport = transport or Transport(cfg)
        self.crawler_factory = crawler_factory or self._make_crawler
        self.decoder = decoder or self._decode
        self.sleep, self.log = sleep, log
        self.work = cfg.work
        self.batches = self.work / "batches"
        self.batches.mkdir(parents=True, exist_ok=True)
        self.stop_requested = False
        self.command = "run"
        self.phase = "starting"
        self.known: set[str] = set()
        self.validating: dict[str, threading.Thread] = {}
        self.attempts: dict[str, dict] = {}
        self.started = time.time()
        self.saved_total = 0
        self.last_status_push = 0.0
        self.last_crawl_summary = ""
        self.current = self._load_current()

    # -- defaults --

    def _make_crawler(self, out_dir: Path, filters: dict, max_replays: int, known: set) -> Crawler:
        return Crawler(out_dir, filters, tier=self.cfg.tier, max_replays=max_replays, workers=self.cfg.workers,
                       known_ids=known, ledger_only=LEDGER_ONLY, should_stop=self._should_stop, quiet=True,
                       log=self.log)

    @staticmethod
    def _decode(paths: list, out_dir: Path):
        from .decode import decode_batch
        return list(decode_batch(paths, str(out_dir)))

    # -- state files --

    def _load_current(self) -> dict:
        path = self.batches / "current.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        return {"items": [], "failed": []}

    def _save_current(self) -> None:
        tmp = self.batches / "current.json.tmp"
        tmp.write_text(json.dumps(self.current), encoding="utf-8")
        os.replace(tmp, self.batches / "current.json")

    def _should_stop(self) -> bool:
        return self.stop_requested or self.command != "run"

    # -- control and status --

    def pull_control(self) -> str:
        dest = self.work / "control.json"
        if self.transport.copy_from(f"boxes/{self.cfg.box}/control.json", dest):
            try:
                self.command = json.loads(dest.read_text(encoding="utf-8")).get("command", "run")
            except (OSError, ValueError):
                self.command = "run"
        return self.command

    def sealed(self) -> list:
        return sorted(p.name[: -len(".manifest.json")] for p in self.batches.glob("*.manifest.json"))

    def unvalidated(self) -> list:
        return [b for b in self.sealed() if not (self.batches / f"{b}.ok").exists()]

    def status(self) -> dict:
        usage = shutil.disk_usage(self.work)
        windows = []
        for w in self.cfg.windows():
            d = self.cfg.window_dir(w)
            state = {}
            if (d / STATE_FILE).exists():
                try:
                    state = json.loads((d / STATE_FILE).read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    state = {}
            windows.append({"key": w.key, "done": len(metadata_ids(d / METADATA_FILE)) if d.exists() else 0,
                            "listing_complete": bool(state.get("listing_complete")),
                            "last_replay_date": state.get("last_replay_date")})
        sealed = []
        for b in self.sealed():
            try:
                man = json.loads((self.batches / f"{b}.manifest.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                man = {}
            bad = self.batches / f"{b}.bad"
            sealed.append({"name": b, "count": len(man.get("items", [])), "bytes": man.get("bytes", 0),
                           "created": man.get("created"), "validated": (self.batches / f"{b}.ok").exists(),
                           "bad": json.loads(bad.read_text(encoding="utf-8")) if bad.exists() else None,
                           "attempts": self.attempts.get(b, {}).get("n", 0),
                           "cleaned": man.get("cleaned")})
        elapsed = max(1.0, time.time() - self.started)
        return {"box": self.cfg.box, "updated": _now(), "phase": self.phase, "command": self.command,
                "current": {"count": len(self.current["items"]), "failed": len(self.current["failed"])},
                "batch_size": self.cfg.batch_size, "sealed": sealed, "unvalidated": self.unvalidated(),
                "saved_this_run": self.saved_total, "per_minute": round(60.0 * self.saved_total / elapsed, 1),
                "uptime_s": int(elapsed), "disk": {"free": usage.free, "total": usage.total},
                "windows": windows, "crawler": self.last_crawl_summary}

    def push_status(self, force: bool = False) -> None:
        if not force and time.time() - self.last_status_push < self.cfg.status_every:
            return
        path = self.work / "status.json"
        path.write_text(json.dumps(self.status(), indent=1), encoding="utf-8")
        try:
            self.transport.copy_to(path, f"boxes/{self.cfg.box}/status.json")
        except Exception as exc:  # noqa: BLE001 - status is best effort
            self.log(f"status push failed: {exc}")
        self.last_status_push = time.time()

    # -- ledger --

    def pull_ledger(self) -> int:
        """Fetch every window's metadata and state from the lab (the master copy). Returns files pulled."""
        n = 0
        for w in self.cfg.windows():
            d = self.cfg.window_dir(w)
            if self.transport.copy_from(self.cfg.lab_meta(w), d / METADATA_FILE):
                n += 1
            if self.transport.copy_from(self.cfg.lab_state(w), d / STATE_FILE):
                n += 1
        self.known = self.known_ids()
        return n

    def known_ids(self) -> set:
        ids = set()
        for w in self.cfg.windows():
            ids |= metadata_ids(self.cfg.window_dir(w) / METADATA_FILE)
        return ids

    def push_ledger(self, windows=None) -> None:
        for w in windows or self.cfg.windows():
            d = self.cfg.window_dir(w)
            if (d / METADATA_FILE).exists():
                self.transport.copy_to(d / METADATA_FILE, self.cfg.lab_meta(w))
            if (d / STATE_FILE).exists():
                self.transport.copy_to(d / STATE_FILE, self.cfg.lab_state(w))

    # -- crawl + decode --

    def next_window(self) -> Window | None:
        for w in self.cfg.windows():
            d = self.cfg.window_dir(w)
            state_path = d / STATE_FILE
            if state_path.exists():
                try:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    state = {}
                if state.get("listing_complete") and state.get("filters") == w.filters():
                    continue
            if self.cfg.per_window is not None and len(metadata_ids(d / METADATA_FILE)) >= self.cfg.per_window:
                continue
            return w
        return None

    def crawl_chunk(self, w: Window) -> int:
        room = self.cfg.batch_size - len(self.current["items"])
        if self.cfg.per_window is not None:
            room = min(room, self.cfg.per_window - len(metadata_ids(self.cfg.window_dir(w) / METADATA_FILE)))
        if room <= 0:
            return 0
        crawler = self.crawler_factory(self.cfg.window_dir(w), w.filters(), min(PAGE, room), self.known)
        result = crawler.run()
        self.last_crawl_summary = crawler.summary()
        self.known |= crawler.meta_ids
        saved = result["stats"]["saved"]
        self.saved_total += saved
        if result["interrupted"]:
            self.stop_requested = True
        return saved

    def decode_window(self, w: Window) -> int:
        d = self.cfg.window_dir(w)
        dec = d / "decoded"
        dec.mkdir(exist_ok=True)
        pending = sorted(p for p in d.glob("*.replay") if not (dec / f"{p.stem}.npz").exists())
        if not pending:
            return 0
        added = 0
        for replay_id, status, error in self.decoder([str(p) for p in pending], dec):
            raw = d / f"{replay_id}.replay"
            if status in ("ok", "cached"):
                npz = dec / f"{replay_id}.npz"
                self.current["items"].append({"id": replay_id, "playlist": w.playlist, "band": w.band,
                                              "month": w.month, "size": npz.stat().st_size, "sha256": sha256_of(npz)})
                added += 1
            else:
                # Both decoders refused it: nothing to keep. The ledger line stays so it is never refetched.
                self.current["failed"].append({"id": replay_id, "window": w.key, "error": error})
                raw.unlink(missing_ok=True)
        self._save_current()
        return added

    # -- seal / push / validate / cleanup --

    def seal(self) -> str | None:
        if not self.current["items"] and not self.current["failed"]:
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        n = sum(1 for b in self.sealed() if b.startswith(stamp)) + 1
        name = f"{stamp}-{n:03d}"
        items = self.current["items"]
        windows = sorted({(i["playlist"], i["band"], i["month"]) for i in items})
        manifest = {"batch": name, "box": self.cfg.box, "created": _now(), "items": items,
                    "failed": self.current["failed"], "bytes": sum(i["size"] for i in items),
                    "ledger": [{"meta": f"meta/{p}/{b}/{m}.jsonl", "state": f"meta/{p}/{b}/{m}.state.json",
                                "playlist": p, "band": b, "month": m} for p, b, m in windows]}
        (self.batches / f"{name}.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.current = {"items": [], "failed": []}
        self._save_current()
        self.log(f"sealed {name}: {len(items)} replays, {manifest['bytes'] / 1e9:.2f} GB")
        return name

    def push_batch(self, name: str) -> None:
        manifest = json.loads((self.batches / f"{name}.manifest.json").read_text(encoding="utf-8"))
        groups: dict = {}
        for item in manifest["items"]:
            groups.setdefault((item["playlist"], item["band"], item["month"]), []).append(item["id"] + ".npz")
        for (p, b, m), names in groups.items():
            w = Window(p, b, m, None, None)
            self.transport.copy_files(self.cfg.window_dir(w) / "decoded", names, self.cfg.lab_decoded(w))
        self.push_ledger([Window(e["playlist"], e["band"], e["month"], None, None) for e in manifest["ledger"]])
        self.transport.copy_to(self.batches / f"{name}.manifest.json", f"batches/{name}.manifest.json")
        self.log(f"pushed {name}")

    def start_validation(self, name: str) -> None:
        if name in self.validating and self.validating[name].is_alive():
            return
        rec = self.attempts.setdefault(name, {"n": 0, "last": 0.0})
        rec["n"] += 1
        rec["last"] = time.time()

        def work():
            try:
                self.push_batch(name)
                ok = self.transport.validate(name)
                self.log(f"validate {name}: {'ok' if ok else 'not ok'}")
            except Exception as exc:  # noqa: BLE001 - retried on the next cycle
                self.log(f"push/validate {name} failed: {exc}")

        thread = threading.Thread(target=work, name=f"validate-{name}", daemon=True)
        self.validating[name] = thread
        thread.start()

    def mirror_marks(self) -> None:
        """Copy <batch>.ok / <batch>.bad from the lab for every unvalidated batch."""
        for name in self.unvalidated():
            for ext in ("ok", "bad"):
                self.transport.copy_from(f"batches/{name}.{ext}", self.batches / f"{name}.{ext}")

    def cleanup(self) -> int:
        """Delete raws and decoded files of every validated batch. Metadata and state are never touched."""
        removed = 0
        for name in self.sealed():
            path = self.batches / f"{name}.manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if manifest.get("cleaned") or not (self.batches / f"{name}.ok").exists():
                continue
            for item in manifest["items"]:
                d = self.cfg.window_dir(Window(item["playlist"], item["band"], item["month"], None, None))
                for f in (d / f"{item['id']}.replay", d / "decoded" / f"{item['id']}.npz",
                          d / "decoded" / f"{item['id']}.meta.json"):
                    if f.exists():
                        f.unlink()
                        removed += 1
            manifest["cleaned"] = _now()
            path.write_text(json.dumps(manifest), encoding="utf-8")
            self.validating.pop(name, None)
        if removed:
            self.log(f"cleanup removed {removed} files")
        return removed

    def retry_stale(self) -> None:
        now = time.time()
        for name in self.unvalidated():
            thread = self.validating.get(name)
            if thread is not None and thread.is_alive():
                continue
            rec = self.attempts.get(name, {"n": 0, "last": 0.0})
            if rec["n"] == 0 or now - rec["last"] >= self.cfg.retry_after:
                (self.batches / f"{name}.bad").unlink(missing_ok=True)
                self.start_validation(name)

    def disk_ok(self) -> bool:
        return shutil.disk_usage(self.work).free >= self.cfg.min_free_gb * 1e9

    # -- the loop --

    def cycle(self) -> bool:
        """One pass. Returns False when there is nothing left to do."""
        self.pull_control()
        self.mirror_marks()
        self.cleanup()
        self.retry_stale()
        if self.command == "stop" or self.stop_requested:
            self.phase = "stopping"
            return False
        if self.command == "pause":
            self.phase = "paused"
            self.push_status()
            self.sleep(30)
            return True
        if len(self.unvalidated()) >= self.cfg.max_unvalidated or not self.disk_ok():
            self.phase = "waiting for validation" if self.disk_ok() else "waiting for disk"
            self.push_status()
            self.sleep(60)
            return True
        w = self.next_window()
        if w is None:
            self.phase = "all windows complete"
            return False
        self.phase = f"crawling {w.key}"
        self.push_status()
        self.crawl_chunk(w)
        self.phase = f"decoding {w.key}"
        self.decode_window(w)
        if len(self.current["items"]) >= self.cfg.batch_size:
            name = self.seal()
            if name:
                self.start_validation(name)
        self.push_status()
        return True

    def finish(self) -> None:
        """Seal whatever is decoded, push it with the ledger, and start its validation."""
        final = self.phase if self.phase == "all windows complete" else "stopped"
        self.phase = "final push"
        name = self.seal()
        if name:
            self.start_validation(name)
        self.push_ledger()
        for thread in list(self.validating.values()):
            thread.join()
        self.mirror_marks()
        self.cleanup()
        self.phase = final
        self.push_status(force=True)

    def run(self) -> int:
        def on_term(signum, frame):  # noqa: ARG001
            self.stop_requested = True
            self.log("SIGTERM: finishing the current page")
        try:
            signal.signal(signal.SIGTERM, on_term)
        except (ValueError, OSError):
            pass
        self.log(f"pulling the ledger from {self.cfg.lab_remote}")
        self.pull_ledger()
        self.log(f"{len(self.known)} replays already in the ledger")
        try:
            while self.cycle():
                pass
        except KeyboardInterrupt:
            self.stop_requested = True
        except MissingKeyError as exc:
            self.log(f"aborted: {exc}")
            return 2
        finally:
            self.finish()
        left = self.unvalidated()
        self.log(f"done: phase={self.phase} unvalidated={left}")
        return 0


# ---- command line (scripts/crawler_batch.py) ----------------------------------------------------

def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="crawler_batch", description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", default=os.environ.get("RLREPLAY_CRAWLER_CONFIG", "/etc/rlreplay/crawler.json"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="the service loop: crawl, decode, seal, push, validate, cleanup until stopped")
    sub.add_parser("pull-ledger", help="fetch the ledger from the lab (done by `run` on start)")
    sub.add_parser("push", help="seal and push what is decoded, push the ledger, wait for validation")
    sub.add_parser("status", help="print status.json")
    sub.add_parser("unvalidated", help="list batches without .ok; exit 1 if any")
    args = parser.parse_args(argv)
    cfg = Config.load(args.config)
    runner = BatchRunner(cfg)
    if args.command == "run":
        return runner.run()
    if args.command == "pull-ledger":
        print(f"pulled {runner.pull_ledger()} ledger files")
        return 0
    if args.command == "push":
        runner.finish()
        left = runner.unvalidated()
        print(f"unvalidated: {left}")
        return 1 if left else 0
    if args.command == "status":
        print(json.dumps(runner.status(), indent=1))
        return 0
    if args.command == "unvalidated":
        left = runner.unvalidated()
        for name in left:
            print(name)
        return 1 if left else 0
    return 2
