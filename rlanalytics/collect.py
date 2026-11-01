"""Auditable, bounded Ballchasing collection using only the standard library."""
from __future__ import annotations
import copy
import hashlib
import json
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from .data import DataValidationError, atomic_json, from_ballchasing, write_matches

API_ROOT = "https://ballchasing.com/api"
FILTERS = {"playlist", "season", "min-rank", "max-rank", "pro", "uploader",
           "player-id", "replay-date-after", "replay-date-before",
           "created-after", "created-before", "map"}
CONFIG_KEYS = {"replay_ids", "group_ids", "filters", "expected_players_per_team",
               "sample_size", "seed", "max_retries", "timeout", "group_series_map",
               "replay_series_map", "replay_metadata", "request_interval_seconds", "max_pages"}
RETRYABLE = {429, 500, 502, 503, 504}

class CollectionError(RuntimeError):
    """Safe diagnostic without credentials or response bodies."""

class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CollectionError("HTTP redirect refused")

def _http_json(url, *, headers, timeout):
    with build_opener(_NoRedirect()).open(Request(url, headers=headers), timeout=timeout) as response:
        return json.load(response)

def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def _safe_url(url, expected_path=None):
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise CollectionError("unsafe pagination URL") from None
    if (parsed.scheme != "https" or parsed.netloc != "ballchasing.com"
            or parsed.username or parsed.password or parsed.fragment
            or not parsed.path.startswith("/api/")
            or (expected_path and parsed.path != expected_path)):
        raise CollectionError("unsafe pagination URL")
    return url

def _core(record):
    result = copy.deepcopy(record)
    for key in ("replay_ids", "source_rocket_league_ids", "match_id_basis", "series_id"):
        result.pop(key, None)
    for player in result["players"]:
        player.pop("name", None)
        player.pop("team_name", None)
    return result

def _config(config):
    if not isinstance(config, dict):
        raise ValueError("config must be an object")
    if set(config) - CONFIG_KEYS:
        raise ValueError("unsupported config keys; credentials belong in the environment")
    result = copy.deepcopy(config)
    filters = result.setdefault("filters", {})
    if not isinstance(filters, dict) or set(filters) - FILTERS:
        raise ValueError("unsupported filter; accepted filters use provider query names")
    for key, value in filters.items():
        if not isinstance(value, (str, int, bool)) or value == "":
            raise ValueError(f"invalid filter value for {key}")
    for key in ("replay_ids", "group_ids"):
        values = result.setdefault(key, [])
        if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
            raise ValueError(f"{key} must be a list of nonempty strings")
        result[key] = sorted(set(values))
    if result["replay_ids"] and filters:
        raise ValueError("replay_ids cannot be combined with filters; use filtered enumeration or an unfiltered explicit list")
    for key in ("group_series_map", "replay_series_map"):
        values = result.setdefault(key, {})
        if not isinstance(values, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not v for k, v in values.items()):
            raise ValueError(f"{key} must map IDs to nonempty series IDs")
    metadata = result.setdefault("replay_metadata", {})
    if not isinstance(metadata, dict) or any(not isinstance(k, str) or not isinstance(v, dict) or set(v) - {"played_at", "series_id"} for k, v in metadata.items()):
        raise ValueError("replay_metadata maps replay IDs to played_at/series_id overrides")
    for key, default, maximum in (("max_retries", 3, 10), ("max_pages", 10000, 1000000)):
        value = result.setdefault(key, default)
        if type(value) is not int or value < (1 if key == "max_pages" else 0) or value > maximum:
            raise ValueError(f"invalid {key}")
    for key, default in (("timeout", 30), ("request_interval_seconds", 0.6)):
        value = result.setdefault(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 300 or (key == "timeout" and value == 0):
            raise ValueError(f"invalid {key}")
    size = result.get("sample_size")
    if size is not None:
        if type(size) is not int or size < 1 or type(result.get("seed")) is not int:
            raise ValueError("sampling requires a positive sample_size and integer seed")
    expected = result.get("expected_players_per_team")
    if expected is not None and (type(expected) is not int or expected < 1):
        raise ValueError("expected_players_per_team must be a positive integer")
    return result

def collect(config, output_dir, *, transport=None, token=None, sleep=time.sleep):
    """Collect into a new directory and return its durable manifest.

    Offline injectable transports need no token. Production uses BALLCHASING_TOKEN.
    Sampling uses fully enumerated upload IDs, not a population-random claim.
    An incomplete run retains raw evidence and valid partial matches.
    """
    config = _config(config)
    directory = Path(output_dir)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("output_dir must be empty; use a new directory for every collection run")
    token = token if token is not None else os.environ.get("BALLCHASING_TOKEN")
    if transport is None and not token:
        raise ValueError("BALLCHASING_TOKEN is required for live collection")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "raw" / "pages").mkdir(parents=True, exist_ok=True)
    fetch = transport or _http_json
    headers = {"Accept": "application/json", "User-Agent": "rlanalytics/0.2"}
    if token:
        headers["Authorization"] = token
    manifest = {
        "schema_version": 1, "started_at": _now(), "finished_at": None,
        "status": "running", "complete": False, "config": config,
        "candidates": [], "selected_replay_ids": [], "replays": {},
        "errors": [], "enumeration_complete": False,
        "sample_unit": "replay_upload_id", "match_count": 0,
    }
    matches, conflicts, candidates, series = {}, set(), set(config["replay_ids"]), {}
    last_request = None

    def checkpoint():
        manifest["candidates"] = sorted(candidates)
        manifest["match_count"] = len(matches)
        dataset = directory / "matches.jsonl"
        write_matches(dataset, list(matches.values()))
        manifest["matches_sha256"] = hashlib.sha256(dataset.read_bytes()).hexdigest()
        atomic_json(directory / "manifest.json", manifest)

    def request(url):
        nonlocal last_request
        _safe_url(url)
        for attempt in range(config["max_retries"] + 1):
            if last_request is not None:
                sleep(config["request_interval_seconds"])
            last_request = time.monotonic()
            try:
                result = fetch(url, headers=dict(headers), timeout=config["timeout"])
                if not isinstance(result, dict):
                    raise CollectionError("response is not a JSON object")
                return result
            except HTTPError as exc:
                if exc.code not in RETRYABLE or attempt == config["max_retries"]:
                    raise CollectionError(f"HTTP {exc.code}; attempts={attempt + 1}") from None
                try:
                    delay = float(exc.headers.get("Retry-After", 2 ** attempt))
                except (TypeError, ValueError, AttributeError):
                    delay = 2 ** attempt
                sleep(max(0, min(delay, 60)))
            except (URLError, TimeoutError, OSError):
                if attempt == config["max_retries"]:
                    raise CollectionError(f"network failure; attempts={attempt + 1}") from None
                sleep(min(2 ** attempt, 60))
            except (ValueError, TypeError):
                raise CollectionError("invalid JSON response") from None
        raise CollectionError("request retries exhausted")

    def pages(endpoint, params):
        path = "/api/" + endpoint
        params = {key: str(value).lower() if isinstance(value, bool) else value for key, value in params.items()}
        current = API_ROOT + "/" + endpoint + "?" + urlencode(params)
        seen = set()
        for _ in range(config["max_pages"]):
            _safe_url(current, path)
            if current in seen:
                raise CollectionError("pagination cycle detected")
            seen.add(current)
            response = request(current)
            digest = hashlib.sha256(current.encode()).hexdigest()
            atomic_json(directory / "raw" / "pages" / f"{digest}.json", response)
            items = response.get("list")
            if not isinstance(items, list):
                raise CollectionError("page has no list")
            yield items
            next_url = response.get("next")
            if not next_url:
                return
            if not isinstance(next_url, str):
                raise CollectionError("invalid pagination link")
            try:
                parsed = urlsplit(_safe_url(urljoin(current, next_url), path))
            except ValueError:
                raise CollectionError("unsafe pagination URL") from None
            query = parse_qs(parsed.query)
            query.update({key: [str(value)] for key, value in params.items()})
            current = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query, doseq=True), ""))
        raise CollectionError("pagination page limit reached")

    def enumerate_replays(group=None, inherited_series=None):
        params = {**config["filters"], "count": 200}
        if group is not None:
            params["group"] = group
        for items in pages("replays", params):
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                    raise CollectionError("replay list item has no ID")
                replay_id = item["id"]
                candidates.add(replay_id)
                if inherited_series:
                    old = series.get(replay_id)
                    if old and old != inherited_series:
                        raise CollectionError("replay belongs to conflicting series mappings")
                    series[replay_id] = inherited_series
            checkpoint()

    checkpoint()
    interrupted = False
    try:
        if config["group_ids"]:
            pending = [(group, config["group_series_map"].get(group)) for group in config["group_ids"]]
            visited = set()
            while pending:
                group, inherited = pending.pop(0)
                if group in visited:
                    continue
                visited.add(group)
                inherited = config["group_series_map"].get(group, inherited)
                enumerate_replays(group, inherited)
                for items in pages("groups", {"group": group, "count": 200}):
                    for item in items:
                        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                            raise CollectionError("group list item has no ID")
                        pending.append((item["id"], inherited))
        elif not config["replay_ids"]:
            enumerate_replays()
        manifest["enumeration_complete"] = True
    except KeyboardInterrupt:
        interrupted = True
        manifest["errors"].append({"stage": "enumeration", "reason": "interrupted"})
    except (CollectionError, DataValidationError) as exc:
        manifest["errors"].append({"stage": "enumeration", "reason": str(exc)})
    selected = sorted(candidates)
    if config.get("sample_size") is not None:
        if not manifest["enumeration_complete"]:
            selected = []
        elif len(selected) < config["sample_size"]:
            manifest["errors"].append({"stage": "sampling", "reason": "fewer candidates than requested sample_size"})
        else:
            selected = sorted(random.Random(config["seed"]).sample(selected, config["sample_size"]))
    manifest["selected_replay_ids"] = selected
    checkpoint()
    if not interrupted:
        for replay_id in selected:
            entry = {"status": "pending"}
            manifest["replays"][replay_id] = entry
            try:
                payload = request(API_ROOT + "/replays/" + quote(replay_id, safe=""))
                digest = hashlib.sha256(replay_id.encode()).hexdigest()
                raw_path = directory / "raw" / f"{digest}.json"
                atomic_json(raw_path, payload)
                entry.update(raw_path=str(raw_path.relative_to(directory)).replace("\\", "/"),
                             sha256=hashlib.sha256(raw_path.read_bytes()).hexdigest())
                if payload.get("id") != replay_id:
                    raise DataValidationError("provider replay ID differs from requested ID")
                metadata = dict(config["replay_metadata"].get(replay_id, {}))
                series_id = config["replay_series_map"].get(replay_id, series.get(replay_id))
                if series_id:
                    metadata["series_id"] = series_id
                record = from_ballchasing(payload, metadata=metadata,
                                          expected_players_per_team=config.get("expected_players_per_team"))
                expected_playlist = config["filters"].get("playlist")
                if expected_playlist and record["playlist"] not in str(expected_playlist).split(","):
                    raise DataValidationError("playlist differs from enumeration filter")
                mid = record["match_id"]
                entry["match_id"] = mid
                if mid in conflicts:
                    raise DataValidationError("match_id already quarantined after conflicting uploads")
                series_conflict = (mid in matches and matches[mid].get("series_id") and record.get("series_id")
                                   and matches[mid]["series_id"] != record["series_id"])
                if mid in matches and (_core(matches[mid]) != _core(record) or series_conflict):
                    conflicts.add(mid)
                    del matches[mid]
                    for previous in manifest["replays"].values():
                        if previous.get("match_id") == mid:
                            previous.update(status="rejected", reason="conflicting uploads for the same match_id")
                    raise DataValidationError("conflicting uploads for the same match_id")
                if mid in matches:
                    if record.get("series_id"):
                        matches[mid]["series_id"] = record["series_id"]
                    matches[mid]["replay_ids"] = sorted(set(matches[mid]["replay_ids"] + record["replay_ids"]))
                    matches[mid]["source_rocket_league_ids"] = sorted(set(matches[mid].get("source_rocket_league_ids", []) + record.get("source_rocket_league_ids", [])))
                    entry["status"] = "duplicate_upload"
                else:
                    matches[mid] = record
                    entry["status"] = "accepted"
            except KeyboardInterrupt:
                entry.update(status="error", reason="interrupted")
                interrupted = True
            except DataValidationError as exc:
                entry.update(status="rejected", reason=str(exc))
            except CollectionError as exc:
                entry.update(status="error", reason=str(exc))
            checkpoint()
            if interrupted:
                break
    failures = sum(entry["status"] in ("error", "rejected", "pending") for entry in manifest["replays"].values())
    manifest["complete"] = (manifest["enumeration_complete"] and not manifest["errors"]
                            and not interrupted and not failures
                            and len(manifest["replays"]) == len(selected))
    manifest["status"] = "complete" if manifest["complete"] else "incomplete"
    manifest["finished_at"] = _now()
    manifest["accepted_replay_count"] = sum(e["status"] in ("accepted", "duplicate_upload") for e in manifest["replays"].values())
    manifest["rejected_or_failed_count"] = failures
    checkpoint()
    return manifest
