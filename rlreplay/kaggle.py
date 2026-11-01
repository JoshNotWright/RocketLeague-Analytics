"""Anonymous per-file access to the Kaggle 'High-Level Rocket League Replay Dataset'.

Kaggle's public v1 API serves individual files of a public dataset without
credentials when the file is requested via ``?file_name=``. The whole-dataset
archive (160 GB) is never requested. Files are written atomically and skipped
when already present, so a download run is idempotent and resumable.
"""
from __future__ import annotations

import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DATASET = "rolvarild/high-level-rocket-league-replay-dataset"
BASE = f"https://www.kaggle.com/api/v1/datasets/download/{DATASET}"
MODES = ("1v1", "2v2", "3v3")
USER_AGENT = "rlreplay/0.1 (+https://github.com/JoshNotWright/RocketLeague-Analytics)"


def file_url(name: str) -> str:
    return BASE + "?" + urllib.parse.urlencode({"file_name": name})


def replay_name(mode: str, replay_id: str) -> str:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    return f"high_level/{mode}/{replay_id}.replay"


def metadata_name(mode: str) -> str:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    return f"high_level/{mode}/metadata.json"


class _Throttle:
    """Shared back-off: when Kaggle starts refusing files, every worker waits together.

    Kaggle answers a throttled per-file request with HTTP 404 (not 429), so a 404 for a
    file that exists in the listing is treated as "slow down", with the pause doubling on
    each consecutive refusal and resetting after a success.
    """

    def __init__(self, base: float = 5.0, cap: float = 900.0):
        self.base, self.cap = base, cap
        self.level = 0
        self.resume_at = 0.0
        self.lock = threading.Lock()

    def wait(self, sleep=time.sleep):
        while True:
            with self.lock:
                delay = self.resume_at - time.monotonic()
            if delay <= 0:
                return
            sleep(min(delay, 5.0))

    def refused(self):
        with self.lock:
            if time.monotonic() >= self.resume_at:  # first refusal of a new episode
                self.level += 1
                self.resume_at = time.monotonic() + min(self.cap, self.base * 2 ** (self.level - 1))

    def succeeded(self):
        with self.lock:
            self.level = 0


THROTTLE = _Throttle()


def download(name: str, dest: Path, *, retries: int = 8, timeout: int = 120,
             opener=None, sleep=time.sleep, throttle: _Throttle | None = None) -> Path:
    """Download one dataset file to ``dest`` unless it already exists.

    A response that is not ``application/octet-stream`` (Kaggle returns an HTML page when
    it refuses a request) is never written to disk; it triggers the shared back-off.
    """
    throttle = throttle or THROTTLE
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    opener = opener or urllib.request.build_opener()
    last_error: Exception | None = None
    for attempt in range(retries):
        throttle.wait(sleep)
        try:
            request = urllib.request.Request(file_url(name), headers={"User-Agent": USER_AGENT})
            with opener.open(request, timeout=timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                if "octet-stream" not in content_type and "json" not in content_type:
                    raise _Refused(f"unexpected content type {content_type!r} for {name}")
                with open(tmp, "wb") as handle:
                    while True:
                        chunk = response.read(1 << 20)
                        if not chunk:
                            break
                        handle.write(chunk)
            os.replace(tmp, dest)
            throttle.succeeded()
            return dest
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in (404, 429, 503):
                throttle.refused()
            else:
                sleep(min(60, 2 ** attempt))
        except _Refused as exc:
            last_error = exc
            throttle.refused()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            sleep(min(60, 2 ** attempt))
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
    raise RuntimeError(f"failed to download {name}: {last_error}")


class _Refused(RuntimeError):
    """Kaggle answered, but not with the file."""


def download_replays(mode: str, replay_ids, raw_dir: Path, *, workers: int = 8,
                     progress=None) -> dict:
    """Download many replays concurrently. Returns {'ok': [...], 'failed': {id: error}}."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    raw_dir = Path(raw_dir)
    result = {"ok": [], "failed": {}}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(download, replay_name(mode, rid), raw_dir / f"{rid}.replay"): rid
                   for rid in replay_ids}
        for n, future in enumerate(as_completed(futures), 1):
            rid = futures[future]
            try:
                future.result()
                result["ok"].append(rid)
            except Exception as exc:  # noqa: BLE001 - record and continue
                result["failed"][rid] = str(exc)
            if progress and n % 50 == 0:
                progress(n, len(futures), len(result["failed"]))
    return result
