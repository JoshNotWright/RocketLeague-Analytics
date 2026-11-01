import hashlib
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from rlanalytics.collect import CollectionError, _NoRedirect, collect
from rlanalytics.data import load_matches

def payload(replay_id, match_id=None):
    result = {"id": replay_id, "rocket_league_id": match_id or "MATCH-" + replay_id,
              "status": "ok", "playlist_id": "ranked-doubles",
              "date": "2024-01-01T00:00:00Z", "duration": 300}
    for color, offset, score in (("blue", 0, 2), ("orange", 2, 1)):
        result[color] = {"stats": {"core": {"goals": score}}, "players": [
            {"id": {"platform": "steam", "id": str(76561198000000000 + offset + index)},
             "name": "Player", "start_time": 0, "end_time": 300, "stats": {}}
            for index in range(2)]}
    return result

class CollectorTests(unittest.TestCase):
    def run_collection(self, config, transport):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        manifest = collect({**config, "request_interval_seconds": 0}, directory.name,
                           transport=transport, sleep=lambda seconds: None)
        return manifest, Path(directory.name)

    def test_direct_child_pages_and_filters_preserved(self):
        def fake(url, **kwargs):
            parsed, query = urlsplit(url), parse_qs(urlsplit(url).query)
            if parsed.path.endswith("/groups"):
                return {"list": [{"id": "child"}] if query["group"] == ["root"] else []}
            if parsed.path.endswith("/replays"):
                self.assertEqual(query["playlist"], ["ranked-doubles"])
                self.assertEqual(query["season"], ["f20"])
                self.assertEqual(query["min-rank"], ["diamond-1"])
                self.assertEqual(query["pro"], ["true"])
                if query["group"] == ["root"] and "after" not in query:
                    return {"list": [{"id": "a"}], "next": "https://ballchasing.com/api/replays?after=page2"}
                return {"list": [{"id": "b" if query["group"] == ["root"] else "c"}]}
            return payload(parsed.path.rsplit("/", 1)[1])
        manifest, directory = self.run_collection(
            {"group_ids": ["root"], "filters": {"playlist": "ranked-doubles", "season": "f20",
             "min-rank": "diamond-1", "pro": True}, "group_series_map": {"root": "series-1"}}, fake)
        self.assertTrue(manifest["complete"])
        self.assertEqual(manifest["match_count"], 3)
        self.assertEqual({m["series_id"] for m in load_matches(directory / "matches.jsonl")}, {"series-1"})
        self.assertGreaterEqual(len(list((directory / "raw" / "pages").glob("*.json"))), 5)
        self.assertEqual(manifest["matches_sha256"], hashlib.sha256((directory / "matches.jsonl").read_bytes()).hexdigest())
        for entry in manifest["replays"].values():
            self.assertEqual(entry["sha256"], hashlib.sha256((directory / entry["raw_path"]).read_bytes()).hexdigest())

    def test_duplicate_upload_merge_and_distinct_games(self):
        def fake(url, **kwargs):
            rid = url.rsplit("/", 1)[1]
            return payload(rid, "same" if rid in ("a", "b") else "different")
        manifest, directory = self.run_collection({"replay_ids": ["a", "b", "c"]}, fake)
        self.assertTrue(manifest["complete"])
        matches = load_matches(directory / "matches.jsonl")
        self.assertEqual(len(matches), 2)
        self.assertEqual(next(m for m in matches if m["match_id"] == "same")["replay_ids"], ["a", "b"])

    def test_match_guid_and_partial_series_metadata_merge(self):
        def fake(url, **kwargs):
            raw = payload(url.rsplit("/", 1)[1])
            raw["match_guid"] = "same-guid"
            return raw
        manifest, directory = self.run_collection(
            {"replay_ids": ["a", "b"], "replay_series_map": {"b": "s1"}}, fake)
        self.assertTrue(manifest["complete"])
        matches = load_matches(directory / "matches.jsonl")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["source_rocket_league_ids"], ["MATCH-a", "MATCH-b"])
        self.assertEqual(matches[0]["series_id"], "s1")

    def test_conflicting_series_quarantined(self):
        manifest, directory = self.run_collection(
            {"replay_ids": ["a", "b"], "replay_series_map": {"a": "s1", "b": "s2"}},
            lambda url, **kwargs: payload(url.rsplit("/", 1)[1], "same"))
        self.assertFalse(manifest["complete"])
        self.assertEqual(load_matches(directory / "matches.jsonl"), [])

    def test_conflicting_uploads_quarantine_entire_match(self):
        def fake(url, **kwargs):
            rid = url.rsplit("/", 1)[1]
            raw = payload(rid, "same")
            if rid == "b":
                raw["blue"]["stats"]["core"]["goals"] = 9
            return raw
        manifest, directory = self.run_collection({"replay_ids": ["a", "b"]}, fake)
        self.assertEqual(manifest["status"], "incomplete")
        self.assertEqual(load_matches(directory / "matches.jsonl"), [])
        self.assertEqual(manifest["rejected_or_failed_count"], 2)
        self.assertEqual(len(list((directory / "raw").glob("*.json"))), 2)

    def test_retry_exhaustion_safe_error(self):
        calls = []
        def fake(url, **kwargs):
            calls.append(url)
            raise HTTPError(url, 503, "sensitive body", {"Retry-After": "0"}, None)
        manifest, directory = self.run_collection({"replay_ids": ["a"], "max_retries": 2}, fake)
        self.assertEqual(len(calls), 3)
        self.assertFalse(manifest["complete"])
        self.assertNotIn("sensitive body", (directory / "manifest.json").read_text())
        self.assertIn("attempts=3", manifest["replays"]["a"]["reason"])

    def test_retry_then_success(self):
        calls = []
        def fake(url, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                raise URLError("private diagnostic")
            return payload("a")
        manifest, _ = self.run_collection({"replay_ids": ["a"]}, fake)
        self.assertTrue(manifest["complete"])
        self.assertEqual(len(calls), 2)

    def test_retry_after_bounded_credentials_not_saved(self):
        calls, sleeps = [], []
        def fake(url, **kwargs):
            self.assertEqual(kwargs["headers"]["Authorization"], "sentinel-private-token")
            calls.append(url)
            if len(calls) == 1:
                raise HTTPError(url, 429, "private reason", {"Retry-After": "100000"}, None)
            return payload("a")
        with tempfile.TemporaryDirectory() as directory:
            manifest = collect({"replay_ids": ["a"], "request_interval_seconds": 0}, directory,
                               transport=fake, token="sentinel-private-token", sleep=sleeps.append)
            self.assertTrue(manifest["complete"])
            self.assertEqual(max(sleeps), 60)
            self.assertNotIn("sentinel-private-token", (Path(directory) / "manifest.json").read_text())

    def test_unsafe_or_malformed_pagination_refused(self):
        for next_url in ("https://unrelated.invalid/api/replays?after=x", "https://[broken/api/replays",
                         "https://ballchasing.com/api/groups"):
            calls = []
            def fake(url, **kwargs):
                calls.append(url)
                return {"list": [], "next": next_url}
            manifest, _ = self.run_collection({"filters": {"playlist": "ranked-doubles"}}, fake)
            self.assertEqual(len(calls), 1)
            self.assertFalse(manifest["complete"])
            self.assertIn("unsafe", manifest["errors"][0]["reason"])

    def test_redirects_refused(self):
        with self.assertRaisesRegex(CollectionError, "redirect refused"):
            _NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://unrelated.invalid")

    def test_deterministic_sample_after_enumeration(self):
        def fake(url, **kwargs):
            if urlsplit(url).path.endswith("/replays"):
                return {"list": [{"id": str(i)} for i in reversed(range(20))]}
            return payload(url.rsplit("/", 1)[1])
        config = {"sample_size": 5, "seed": 42}
        a, _ = self.run_collection(config, fake)
        b, _ = self.run_collection(config, fake)
        self.assertEqual(len(a["candidates"]), 20)
        self.assertEqual(len(a["selected_replay_ids"]), 5)
        self.assertEqual(a["selected_replay_ids"], b["selected_replay_ids"])

    def test_broken_enumeration_never_samples_partial_pool(self):
        manifest, _ = self.run_collection({"sample_size": 1, "seed": 1},
            lambda *a, **kw: {"list": [{"id": "a"}], "next": "https://unrelated.invalid/api/replays"})
        self.assertEqual(manifest["selected_replay_ids"], [])
        self.assertFalse(manifest["complete"])

    def test_pagination_cycle_bounded(self):
        manifest, _ = self.run_collection({}, lambda url, **kw: {"list": [], "next": url})
        self.assertFalse(manifest["complete"])
        self.assertIn("cycle", manifest["errors"][0]["reason"])

    def test_required_data_rejection_preserves_raw(self):
        def fake(url, **kwargs):
            raw = payload("a")
            raw.pop("rocket_league_id")
            return raw
        manifest, directory = self.run_collection({"replay_ids": ["a"]}, fake)
        self.assertEqual(manifest["replays"]["a"]["status"], "rejected")
        self.assertTrue((directory / manifest["replays"]["a"]["raw_path"]).exists())

    def test_existing_output_and_unverified_filters(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "keep.txt"
            marker.write_text("keep")
            with self.assertRaises(ValueError):
                collect({"replay_ids": ["a"]}, directory, transport=lambda *a, **kw: payload("a"))
            self.assertEqual(marker.read_text(), "keep")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "cannot be combined"):
                collect({"replay_ids": ["a"], "filters": {"season": "f1"}}, directory,
                        transport=lambda *a, **kw: payload("a"))

    def test_keyboard_interrupt_marks_incomplete(self):
        def fake(url, **kwargs):
            raise KeyboardInterrupt()
        manifest, _ = self.run_collection({"replay_ids": ["a"]}, fake)
        self.assertEqual(manifest["status"], "incomplete")
        self.assertEqual(manifest["replays"]["a"]["reason"], "interrupted")
