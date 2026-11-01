"""Long-running, resumable crawler for ballchasing.com replays at chosen ranks.

Writes the same layout the Kaggle loader already reads:

    <out>/<name>/<replay_id>.replay          the raw replay binary
    <out>/<name>/metadata.json               one JSON object per line, see ``metadata_line``
    <out>/<name>/crawl_state.json            cursor + finished ids, so a restart continues

Only the standard library is used for HTTP. The API key is read from the environment
variable ``BALLCHASING_API_KEY`` and is never logged or written to disk.

Rate limits (https://ballchasing.com/doc/api, per endpoint, per Patreon tier) are enforced
client-side by ``Pacer``: a per-call minimum interval plus a sliding one-hour budget for each
endpoint, and a shared back-off whenever the server answers 429.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

API_ROOT = "https://ballchasing.com/api"
KEY_ENV = "BALLCHASING_API_KEY"
USER_AGENT = "rlreplay/0.1 (+https://github.com/JoshNotWright/RocketLeague-Analytics)"
STATE_FILE = "crawl_state.json"
METADATA_FILE = "metadata.json"

# Limits published at https://ballchasing.com/doc/api (per endpoint, per Patreon tier),
# as (calls per second, calls per hour or None when the doc gives no hourly cap).
#   List/filter replays   GC 16/s | Champion 8/s | Diamond 4/s, 2000/h | Gold 2/s, 1000/h | others 2/s, 500/h
#   Get replay            GC 16/s | Champion 8/s | Diamond 4/s, 5000/h | Gold 2/s, 2000/h | others 2/s, 1000/h
#   Download replay file  GC 2/s  | Champion 2/s, 2000/h | Diamond 2/s, 1000/h | Gold 2/s, 400/h | others 1/s, 200/h
TIERS = {
    "free":     {"list": (2, 500),  "replay": (2, 1000),  "file": (1, 200)},
    "gold":     {"list": (2, 1000), "replay": (2, 2000),  "file": (2, 400)},
    "diamond":  {"list": (4, 2000), "replay": (4, 5000),  "file": (2, 1000)},
    "champion": {"list": (8, None), "replay": (8, None),  "file": (2, 2000)},
    "gc":       {"list": (16, None), "replay": (16, None), "file": (2, None)},
}
DEFAULT_TIER = "free"
# Stay a little under the published numbers: the server counts, we only estimate.
SAFETY = 0.9

RANKS = ("unranked", "bronze-1", "bronze-2", "bronze-3", "silver-1", "silver-2", "silver-3",
         "gold-1", "gold-2", "gold-3", "platinum-1", "platinum-2", "platinum-3",
         "diamond-1", "diamond-2", "diamond-3", "champion-1", "champion-2", "champion-3",
         "grand-champion", "grand-champion-1", "grand-champion-2", "grand-champion-3",
         "supersonic-legend")
RETRYABLE = {500, 502, 503, 504}


class CrawlError(RuntimeError):
    """Diagnostic that never carries credentials or response bodies."""


class MissingKeyError(CrawlError):
    """No API key in the environment and no offline transport injected."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CrawlError("HTTP redirect refused")


def _http(url: str, *, headers: dict, timeout: float) -> bytes:
    """Default transport: GET ``url`` and return the body bytes. Raises HTTPError/URLError."""
    with build_opener(_NoRedirect()).open(Request(url, headers=headers), timeout=timeout) as response:
        return response.read()


def _now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_url(url: str, expected_path: str = "/api/replays") -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise CrawlError("unsafe pagination URL") from None
    if (parsed.scheme != "https" or parsed.netloc != "ballchasing.com" or parsed.username
            or parsed.password or parsed.fragment or parsed.path != expected_path):
        raise CrawlError("unsafe pagination URL")
    return url


def _atomic_json(path: Path, payload) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _log(message: str) -> None:
    print(f"[crawl {_now_text()}] {message}", file=sys.stderr, flush=True)


class Pacer:
    """Client-side rate limiter for one endpoint class plus shared 429 back-off.

    ``acquire`` blocks (via the injected ``sleep``) until a call is allowed: at least
    ``1/per_second`` after the previous call, no more than ``per_hour`` calls inside any
    sliding hour, and not before the shared back-off deadline. ``clock`` must be monotonic.
    """

    def __init__(self, per_second: float, per_hour: int | None, *, safety: float = SAFETY,
                 clock=time.monotonic, sleep=time.sleep, backoff: "Backoff | None" = None):
        self.interval = 1.0 / (per_second * safety)
        self.per_hour = None if per_hour is None else max(1, int(per_hour * safety))
        self.clock, self.sleep = clock, sleep
        self.backoff = backoff or Backoff(clock=clock)
        self.calls: deque[float] = deque()
        self.last = None
        self.lock = threading.Lock()

    def wait_time(self) -> float:
        now = self.clock()
        delay = 0.0
        if self.last is not None:
            delay = max(delay, self.last + self.interval - now)
        if self.per_hour is not None:
            while self.calls and now - self.calls[0] >= 3600.0:
                self.calls.popleft()
            if len(self.calls) >= self.per_hour:
                delay = max(delay, self.calls[0] + 3600.0 - now)
        delay = max(delay, self.backoff.remaining())
        return delay

    def acquire(self) -> None:
        while True:
            with self.lock:
                delay = self.wait_time()
                if delay <= 0:
                    now = self.clock()
                    self.last = now
                    self.calls.append(now)
                    return
            self.sleep(min(delay, 30.0))

    def hourly_used(self) -> int:
        return len(self.calls)


class Backoff:
    """Shared 429 handling: every endpoint waits together, pause grows per consecutive refusal."""

    def __init__(self, base: float = 10.0, cap: float = 3600.0, clock=time.monotonic):
        self.base, self.cap, self.clock = base, cap, clock
        self.level = 0
        self.resume_at = 0.0
        self.lock = threading.Lock()

    def remaining(self) -> float:
        with self.lock:
            return self.resume_at - self.clock()

    def refused(self, retry_after: float | None = None) -> float:
        with self.lock:
            self.level += 1
            if retry_after is not None and retry_after > 0:
                delay = min(self.cap, retry_after)
            else:
                delay = min(self.cap, self.base * 2 ** (self.level - 1))
            self.resume_at = max(self.resume_at, self.clock() + delay)
            return delay

    def succeeded(self) -> None:
        with self.lock:
            self.level = 0


def _retry_after(exc: HTTPError) -> float | None:
    try:
        value = exc.headers.get("Retry-After") if exc.headers is not None else None
    except AttributeError:
        value = None
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:  # HTTP-date form
        from email.utils import parsedate_to_datetime
        when = parsedate_to_datetime(value)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, IndexError):
        return None


def player_ids(detail: dict) -> list[str]:
    """'platform:id' strings for every player, blue then orange, as in the Kaggle metadata."""
    ids = []
    for color in ("blue", "orange"):
        for player in ((detail.get(color) or {}).get("players") or []):
            pid = player.get("id") or {}
            ids.append(f"{pid.get('platform')}:{pid.get('id')}")
    return ids


def metadata_line(detail: dict) -> str:
    """One metadata.json line in the Kaggle dataset's shape (score/gameplay_duration unknown)."""
    record = {"id": detail["id"], "score": None, "gameplay_duration": None,
              "players": player_ids(detail), "data": detail}
    return json.dumps(record, separators=(",", ":"), ensure_ascii=False)


def build_filters(*, playlist="ranked-doubles", min_rank="diamond-1", max_rank="champion-3",
                  season=None, after=None, before=None, count=200, sort_by="replay-date",
                  sort_dir="asc", extra=None) -> dict:
    if not 1 <= int(count) <= 200:
        raise ValueError("count must be between 1 and 200")
    if sort_by not in ("replay-date", "upload-date") or sort_dir not in ("asc", "desc"):
        raise ValueError("sort-by must be replay-date|upload-date and sort-dir asc|desc")
    for rank in (min_rank, max_rank):
        if rank and rank not in RANKS:
            _log(f"warning: rank {rank!r} is not in the documented list; passing it through")
    filters = {"playlist": playlist, "count": int(count), "sort-by": sort_by, "sort-dir": sort_dir}
    if min_rank:
        filters["min-rank"] = min_rank
    if max_rank:
        filters["max-rank"] = max_rank
    if season:
        filters["season"] = season
    if after:
        filters["replay-date-after"] = after
    if before:
        filters["replay-date-before"] = before
    if extra:
        filters.update(extra)
    return filters


def list_url(filters: dict) -> str:
    return API_ROOT + "/replays?" + urlencode(filters)


class Crawler:
    """Resumable crawl into ``out_dir``. Use ``run()``; everything else is a step of it."""

    def __init__(self, out_dir, filters: dict, *, tier: str = DEFAULT_TIER, max_replays: int | None = None,
                 key: str | None = None, transport=None, sleep=time.sleep, clock=time.monotonic,
                 timeout: float = 120.0, max_retries: int = 6, max_429: int = 24, log=_log,
                 progress_every: int = 25):
        if tier not in TIERS:
            raise ValueError(f"tier must be one of {sorted(TIERS)}")
        self.out = Path(out_dir)
        self.filters = dict(filters)
        self.tier = tier
        self.max_replays = max_replays
        self.transport = transport or _http
        self.sleep, self.clock = sleep, clock
        self.timeout, self.max_retries, self.max_429 = timeout, max_retries, max_429
        self.log = log
        self.progress_every = progress_every
        if transport is None:
            key = key if key is not None else os.environ.get(KEY_ENV)
            if not key:
                raise MissingKeyError(f"{KEY_ENV} is not set; create a key at https://ballchasing.com/upload")
        self.headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if key:
            self.headers["Authorization"] = key
        self.backoff = Backoff(clock=clock)
        self.pacers = {name: Pacer(per_second, per_hour, clock=clock, sleep=sleep, backoff=self.backoff)
                       for name, (per_second, per_hour) in TIERS[tier].items()}
        self.state_path = self.out / STATE_FILE
        self.meta_path = self.out / METADATA_FILE
        self.state = self._load_state()
        self.stats = {"saved": 0, "skipped_done": 0, "deferred": 0, "failed": 0, "pages": 0, "requests": 0,
                      "waits_429": 0}

    # ---- state -------------------------------------------------------------------------

    def _load_state(self) -> dict:
        state = {"version": 1, "filters": self.filters, "cursor": None, "listing_complete": False,
                 "last_replay_date": None, "done": [], "failed": {}, "deferred": {}, "started": _now_text(),
                 "updated": None}
        if self.state_path.exists():
            try:
                saved = json.loads(self.state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                saved = {}
            if isinstance(saved, dict):
                if saved.get("filters") not in (None, self.filters):
                    self.log("warning: filters differ from the saved state; the cursor is discarded")
                    saved.pop("cursor", None)
                    saved["listing_complete"] = False
                state.update({k: v for k, v in saved.items() if k in state})
                state["filters"] = self.filters
        self.done = set(state["done"])
        # Idempotence against the files themselves: a replay with a metadata line and a file is done.
        in_meta = self._metadata_ids()
        for path in self.out.glob("*.replay"):
            if path.stat().st_size > 0 and path.stem in in_meta:
                self.done.add(path.stem)
        self.meta_ids = in_meta
        return state

    def _metadata_ids(self) -> set[str]:
        ids = set()
        if self.meta_path.exists():
            with open(self.meta_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ids.add(json.loads(line)["id"])
                    except (ValueError, KeyError, TypeError):
                        continue
        return ids

    def save_state(self) -> None:
        self.state["done"] = sorted(self.done)
        self.state["updated"] = _now_text()
        self.state["stats_last_run"] = dict(self.stats)
        self.out.mkdir(parents=True, exist_ok=True)
        _atomic_json(self.state_path, self.state)

    # ---- http -------------------------------------------------------------------------

    def request(self, endpoint: str, url: str) -> bytes:
        pacer = self.pacers[endpoint]
        attempt = refusals = 0
        while True:
            pacer.acquire()
            self.stats["requests"] += 1
            try:
                body = self.transport(url, headers=dict(self.headers), timeout=self.timeout)
                self.backoff.succeeded()
                return body
            except HTTPError as exc:
                if exc.code == 429:
                    refusals += 1
                    if refusals > self.max_429:
                        raise CrawlError(f"HTTP 429 on {endpoint} {refusals} times in a row") from None
                    delay = self.backoff.refused(_retry_after(exc))
                    self.stats["waits_429"] += 1
                    self.log(f"429 on {endpoint}; pausing {delay:.0f}s")
                    continue  # does not count against max_retries: the quota comes back
                attempt += 1
                if exc.code not in RETRYABLE or attempt > self.max_retries:
                    raise CrawlError(f"HTTP {exc.code} on {endpoint}; attempts={attempt}") from None
                self.sleep(min(60, 2 ** attempt))
            except (URLError, TimeoutError, OSError):
                attempt += 1
                if attempt > self.max_retries:
                    raise CrawlError(f"network failure on {endpoint}; attempts={attempt}") from None
                self.sleep(min(60, 2 ** attempt))

    def request_json(self, endpoint: str, url: str) -> dict:
        body = self.request(endpoint, url)
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            raise CrawlError(f"invalid JSON from {endpoint}") from None
        if not isinstance(payload, dict):
            raise CrawlError(f"{endpoint} response is not a JSON object")
        return payload

    # ---- work -------------------------------------------------------------------------

    def _next_url(self, current: str, next_url) -> str | None:
        if not next_url:
            return None
        if not isinstance(next_url, str):
            raise CrawlError("invalid pagination link")
        parsed = urlsplit(_safe_url(urljoin(current, next_url)))
        query = parse_qs(parsed.query)
        # Keep our filters authoritative even if the server rewrites the query.
        query.update({key: [str(value)] for key, value in self.active_filters.items()})
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query, doseq=True), ""))

    def _start_url(self) -> str:
        """First page for this run; also fixes ``active_filters`` (the ones pagination must keep)."""
        cursor = self.state.get("cursor")
        self.active_filters = dict(self.filters)
        if cursor:
            _safe_url(cursor)
            saved_after = parse_qs(urlsplit(cursor).query).get("replay-date-after")
            if saved_after:  # an incremental run was interrupted mid-listing: keep its window
                self.active_filters["replay-date-after"] = saved_after[0]
            return cursor
        if self.state["listing_complete"] and self.state.get("last_replay_date") \
                and self.filters.get("sort-by") == "replay-date" and self.filters.get("sort-dir") == "asc":
            # Earlier run exhausted the listing: continue from the newest replay date seen.
            self.active_filters["replay-date-after"] = self.state["last_replay_date"]
        return list_url(self.active_filters)

    def budget_left(self) -> bool:
        return self.max_replays is None or self.stats["saved"] < self.max_replays

    def fetch_one(self, replay_id: str) -> str:
        """Fetch detail + file for one replay. Returns 'saved', 'deferred' or 'failed'."""
        dest = self.out / f"{replay_id}.replay"
        detail = self.request_json("replay", API_ROOT + "/replays/" + quote(replay_id, safe=""))
        if detail.get("id") != replay_id:
            raise CrawlError("replay detail id differs from the requested id")
        status = detail.get("status")
        if status != "ok":
            self.state["deferred"][replay_id] = status or "unknown"
            return "deferred"
        if not (dest.exists() and dest.stat().st_size > 0):
            body = self.request("file", API_ROOT + "/replays/" + quote(replay_id, safe="") + "/file")
            if not body:
                raise CrawlError("empty replay file")
            tmp = dest.with_suffix(dest.suffix + ".part")
            try:
                with open(tmp, "wb") as handle:
                    handle.write(body)
                os.replace(tmp, dest)
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
        if replay_id not in self.meta_ids:
            with open(self.meta_path, "a", encoding="utf-8") as handle:
                handle.write(metadata_line(detail) + "\n")
            self.meta_ids.add(replay_id)
        self.done.add(replay_id)
        self.state["deferred"].pop(replay_id, None)
        self.state["failed"].pop(replay_id, None)
        date = detail.get("date")
        if date and (self.state["last_replay_date"] is None or date > self.state["last_replay_date"]):
            self.state["last_replay_date"] = date
        return "saved"

    def process(self, replay_id: str) -> None:
        if replay_id in self.done:
            self.stats["skipped_done"] += 1
            return
        try:
            outcome = self.fetch_one(replay_id)
        except CrawlError as exc:
            outcome = "failed"
            self.state["failed"][replay_id] = str(exc)
        self.stats[outcome] += 1
        self.save_state()
        if outcome == "saved" and self.stats["saved"] % self.progress_every == 0:
            self.log(self.summary())

    def run(self) -> dict:
        self.out.mkdir(parents=True, exist_ok=True)
        interrupted = False
        current = None
        try:
            current = self._start_url()
            self.log(f"tier={self.tier} out={self.out} done={len(self.done)} "
                     f"max_replays={self.max_replays} filters={self.active_filters}")
            while current and self.budget_left():
                _safe_url(current)
                self.state["cursor"] = current
                self.state["listing_complete"] = False
                self.save_state()
                page = self.request_json("list", current)
                items = page.get("list")
                if not isinstance(items, list):
                    raise CrawlError("list page has no 'list'")
                self.stats["pages"] += 1
                ids = []
                for item in items:
                    if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
                        if item["id"] not in ids:
                            ids.append(item["id"])
                for replay_id in ids:
                    if not self.budget_left():
                        break
                    self.process(replay_id)
                if not self.budget_left():
                    break  # cursor stays on this page; its remaining ids are picked up next run
                current = self._next_url(current, page.get("next"))
                self.state["cursor"] = current
                if current is None:
                    self.state["listing_complete"] = True
                self.save_state()
        except KeyboardInterrupt:
            interrupted = True
            self.log("interrupted; state saved")
        except CrawlError as exc:
            self.state.setdefault("errors", []).append({"when": _now_text(), "reason": str(exc)})
            self.log(f"stopped: {exc}")
        finally:
            self.save_state()
        self.log(self.summary())
        return {"interrupted": interrupted, "stats": dict(self.stats), "done": len(self.done),
                "listing_complete": self.state["listing_complete"], "state_path": str(self.state_path)}

    def summary(self) -> str:
        used = ", ".join(f"{name}={p.hourly_used()}/{p.per_hour or 'inf'}" for name, p in self.pacers.items())
        return (f"saved={self.stats['saved']} total_done={len(self.done)} skipped={self.stats['skipped_done']} "
                f"deferred={self.stats['deferred']} failed={self.stats['failed']} pages={self.stats['pages']} "
                f"requests={self.stats['requests']} waits_429={self.stats['waits_429']} hourly_used[{used}]")


def crawl(out_dir, filters: dict, **kwargs) -> dict:
    """Convenience wrapper: build a Crawler and run it."""
    return Crawler(out_dir, filters, **kwargs).run()


def add_arguments(parser) -> None:
    parser.add_argument("--out", required=True, help="dataset root; files go under <out>/<name>/")
    parser.add_argument("--name", default="2v2_diamond_champion", help="subfolder name, like the Kaggle '2v2'")
    parser.add_argument("--playlist", default="ranked-doubles")
    parser.add_argument("--min-rank", default="diamond-1")
    parser.add_argument("--max-rank", default="champion-3")
    parser.add_argument("--season", default=None, help="e.g. f20 for free-to-play season 20")
    parser.add_argument("--after", default=None, help="replay-date-after, RFC3339 e.g. 2025-06-01T00:00:00Z")
    parser.add_argument("--before", default=None, help="replay-date-before, RFC3339")
    parser.add_argument("--count", type=int, default=200, help="replays per list page (1-200)")
    parser.add_argument("--sort-by", default="replay-date", choices=("replay-date", "upload-date"))
    parser.add_argument("--sort-dir", default="asc", choices=("asc", "desc"))
    parser.add_argument("--tier", default=DEFAULT_TIER, choices=sorted(TIERS),
                        help="ballchasing Patreon tier that owns the key; sets the quotas")
    parser.add_argument("--max-replays", type=int, default=None, help="stop after saving this many new replays")
    parser.add_argument("--timeout", type=float, default=120.0)


def main_from_args(args) -> int:
    filters = build_filters(playlist=args.playlist, min_rank=args.min_rank, max_rank=args.max_rank,
                            season=args.season, after=args.after, before=args.before, count=args.count,
                            sort_by=args.sort_by, sort_dir=args.sort_dir)
    out = Path(args.out) / args.name
    try:
        crawler = Crawler(out, filters, tier=args.tier, max_replays=args.max_replays, timeout=args.timeout)
    except MissingKeyError as exc:
        print(f"crawl aborted: {exc}", file=sys.stderr, flush=True)
        print(f"summary: saved=0 out={out} (nothing fetched)", file=sys.stderr, flush=True)
        return 2
    result = crawler.run()
    print(f"saved={result['stats']['saved']} total_done={result['done']} "
          f"listing_complete={result['listing_complete']} state={result['state_path']}", flush=True)
    if result["interrupted"]:
        return 130
    return 0 if not result["stats"]["failed"] else 1
