"""Offline tests for rlreplay.crawl: a fake transport stands in for ballchasing.com."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from rlreplay import crawl as crawl_mod
from rlreplay import meta
from rlreplay.crawl import (KEY_ENV, Backoff, Crawler, CrawlError, MissingKeyError, Pacer, TIERS,
                            build_filters, metadata_line)

API = "https://ballchasing.com/api"


def detail(replay_id, status="ok", date="2025-03-01T12:00:00Z"):
    def player(pid, goals, tier):
        return {"name": f"p{pid}", "id": {"platform": "steam", "id": pid}, "mvp": False,
                "rank": {"id": "diamond-2", "tier": tier, "division": 2},
                "stats": {"core": {"goals": goals, "shots": 2, "score": 300}, "boost": {"bpm": 350},
                          "movement": {"avg_speed": 1400}, "positioning": {"percent_behind_ball": 60.0},
                          "demo": {"inflicted": 0, "taken": 1}}}
    return {"id": replay_id, "status": status, "match_guid": "G" + replay_id, "date": date,
            "playlist_id": "ranked-doubles", "team_size": 2, "season": 18, "match_type": "Online",
            "map_code": "cs_p", "overtime": False, "duration": 320,
            "blue": {"players": [player("1", 1, 14), player("2", 1, 14)], "stats": {"core": {"goals": 2}}},
            "orange": {"players": [player("3", 0, 15), player("4", 1, 14)], "stats": {"core": {"goals": 1}}}}


class FakeClock:
    """Monotonic clock whose sleep advances time instead of waiting."""

    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


class FakeSite:
    """Pages keyed by the list URL's 'after' cursor; detail/file served for any known id."""

    def __init__(self, pages, statuses=None, fail_file_429_once=()):
        self.pages = pages  # {None: [...ids], "p2": [...ids]}; each page may link to the next
        self.statuses = statuses or {}
        self.calls = []
        self.file_429 = set(fail_file_429_once)

    def __call__(self, url, *, headers, timeout):
        assert headers.get("Authorization") in (None, "test-key-value"), "key must be passed verbatim"
        parsed = urlsplit(url)
        self.calls.append(url)
        if parsed.path == "/api/replays":
            query = parse_qs(parsed.query)
            cursor = query.get("after", [None])[0]
            keys = list(self.pages)
            ids = self.pages[cursor]
            body = {"count": 999, "list": [{"id": i, "status": "ok"} for i in ids]}
            index = keys.index(cursor)
            if index + 1 < len(keys):
                body["next"] = f"{API}/replays?after={keys[index + 1]}&count=200"
            return json.dumps(body).encode()
        parts = parsed.path.split("/")
        if parts[-1] == "file":
            rid = parts[-2]
            if rid in self.file_429:
                self.file_429.discard(rid)
                raise HTTPError(url, 429, "slow down (private body)", {"Retry-After": "7"}, None)
            return b"REPLAYBYTES-" + rid.encode()
        rid = parts[-1]
        return json.dumps(detail(rid, status=self.statuses.get(rid, "ok"))).encode()


class CrawlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "2v2_dc"
        self.clock = FakeClock()
        self.logs = []

    def crawler(self, site, **kwargs):
        kwargs.setdefault("filters", build_filters())
        return Crawler(self.out, kwargs.pop("filters"), transport=site, key="test-key-value",
                       sleep=self.clock.sleep, clock=self.clock.now, log=self.logs.append, **kwargs)

    def test_pagination_dedup_and_output_layout(self):
        site = FakeSite({None: ["a", "b"], "p2": ["b", "c"]})
        result = self.crawler(site).run()
        self.assertEqual(result["stats"]["saved"], 3)
        self.assertEqual(result["stats"]["skipped_done"], 1)
        self.assertTrue(result["listing_complete"])
        self.assertEqual(sorted(p.name for p in self.out.glob("*.replay")),
                         ["a.replay", "b.replay", "c.replay"])
        self.assertEqual((self.out / "b.replay").read_bytes(), b"REPLAYBYTES-b")
        lines = (self.out / "metadata.json").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(l)["id"] for l in lines], ["a", "b", "c"])
        # The filters survive into the pagination URL and the detail/file endpoints are hit once per id.
        list_calls = [u for u in site.calls if urlsplit(u).path == "/api/replays"]
        self.assertEqual(len(list_calls), 2)
        self.assertEqual(parse_qs(urlsplit(list_calls[1]).query)["min-rank"], ["diamond-1"])
        self.assertEqual(sum(u.endswith("/file") for u in site.calls), 3)
        self.assertNotIn("after=", list_calls[0])

    def test_worker_pool_matches_sequential_output(self):
        ids1 = [f"r{i:02d}" for i in range(20)]
        ids2 = [f"r{i:02d}" for i in range(18, 30)]  # overlap with page one is skipped, not refetched
        site = FakeSite({None: ids1, "p2": ids2})
        result = self.crawler(site, workers=4).run()
        self.assertEqual(result["stats"]["saved"], 30)
        self.assertEqual(result["stats"]["skipped_done"], 2)
        self.assertEqual(result["stats"]["failed"], 0)
        self.assertTrue(result["listing_complete"])
        self.assertEqual(sorted(p.stem for p in self.out.glob("*.replay")), sorted(set(ids1 + ids2)))
        lines = (self.out / "metadata.json").read_text(encoding="utf-8").splitlines()
        meta_ids = [json.loads(l)["id"] for l in lines]
        self.assertEqual(sorted(meta_ids), sorted(set(ids1 + ids2)))
        self.assertEqual(len(meta_ids), len(set(meta_ids)))
        self.assertEqual(sum(u.endswith("/file") for u in site.calls), 30)
        state = json.loads((self.out / "crawl_state.json").read_text(encoding="utf-8"))
        self.assertEqual(len(state["done"]), 30)

    def test_worker_pool_respects_max_replays_and_resumes(self):
        ids = [f"r{i:02d}" for i in range(12)]
        site = FakeSite({None: ids})
        first = self.crawler(site, workers=3, max_replays=5).run()
        self.assertEqual(first["stats"]["saved"], 5)
        self.assertFalse(first["listing_complete"])
        second = self.crawler(site, workers=3).run()
        self.assertEqual(second["stats"]["saved"], 7)
        self.assertEqual(sorted(p.stem for p in self.out.glob("*.replay")), ids)

    def test_metadata_lines_match_kaggle_shape(self):
        line = metadata_line(detail("x"))
        record = json.loads(line)
        self.assertEqual(set(record), {"id", "score", "gameplay_duration", "players", "data"})
        self.assertIsNone(record["score"])
        self.assertIsNone(record["gameplay_duration"])
        self.assertEqual(record["players"], ["steam:1", "steam:2", "steam:3", "steam:4"])
        rows = meta.player_rows(record)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["replay_id"], "x")
        self.assertEqual(rows[0]["player_id"], "steam:1")
        self.assertEqual((rows[0]["team_goals"], rows[0]["opp_goals"]), (2, 1))
        self.assertEqual(rows[2]["rank_points"], 61)
        self.assertEqual(rows[0]["played_at"].isoformat(), "2025-03-01T12:00:00+00:00")
        self.assertIsNone(rows[0]["dataset_score"])

    def test_resume_from_state_file_after_max_replays(self):
        site = FakeSite({None: ["a", "b"], "p2": ["c"]})
        first = self.crawler(site, max_replays=2).run()
        self.assertEqual(first["stats"]["saved"], 2)
        self.assertFalse(first["listing_complete"])
        state = json.loads((self.out / "crawl_state.json").read_text())
        self.assertEqual(state["done"], ["a", "b"])
        self.assertIn("/api/replays?", state["cursor"])
        self.assertNotIn("test-key-value", json.dumps(state))
        second = self.crawler(site).run()
        self.assertEqual(second["stats"]["saved"], 1)
        self.assertEqual(second["done"], 3)
        self.assertTrue(second["listing_complete"])
        # a and b were listed again (cursor pointed at the unfinished page) but never re-fetched.
        self.assertEqual(sum(u.endswith("/a/file") for u in site.calls), 1)
        self.assertEqual(sum(u.endswith("/replays/a") for u in site.calls), 1)
        lines = (self.out / "metadata.json").read_text().splitlines()
        self.assertEqual(len(lines), 3)
        # A third run with a complete listing continues after the newest replay date instead of page 1.
        self.crawler(site).run()
        last_list = [u for u in site.calls if urlsplit(u).path == "/api/replays"][-1]
        self.assertEqual(parse_qs(urlsplit(last_list).query)["replay-date-after"], ["2025-03-01T12:00:00Z"])

    def test_existing_files_on_disk_are_not_redownloaded(self):
        self.out.mkdir(parents=True)
        (self.out / "a.replay").write_bytes(b"already here")
        site = FakeSite({None: ["a", "b"]})
        self.crawler(site).run()
        self.assertEqual(sum(u.endswith("/a/file") for u in site.calls), 0)
        self.assertEqual((self.out / "a.replay").read_bytes(), b"already here")
        self.assertEqual([json.loads(l)["id"] for l in (self.out / "metadata.json").read_text().splitlines()],
                         ["a", "b"])

    def test_ledger_only_skips_replays_whose_file_is_gone(self):
        self.out.mkdir(parents=True)
        (self.out / "metadata.json").write_text(json.dumps({"id": "a"}) + "\n", encoding="utf-8")
        site = FakeSite({None: ["a", "b"]})
        self.crawler(site).run()  # default: no file, so "a" is fetched again
        self.assertEqual(sum(u.endswith("/a/file") for u in site.calls), 1)
        (self.out / "a.replay").unlink()
        site2 = FakeSite({None: ["a", "b"]})
        result = self.crawler(site2, ledger_only=True).run()
        self.assertEqual(sum(u.endswith("/a/file") for u in site2.calls), 0)
        self.assertFalse((self.out / "a.replay").exists())
        self.assertEqual(result["stats"]["skipped_done"], 2)

    def test_known_from_folders_and_files(self):
        other = self.out.parent / "other"
        other.mkdir(parents=True)
        (other / "metadata.json").write_text(json.dumps({"id": "a"}) + "\n", encoding="utf-8")
        self.assertEqual(crawl_mod.known_ids_from([other, other / "metadata.json", other / "missing"]), {"a"})
        site = FakeSite({None: ["a", "b"]})
        self.crawler(site, known_ids=crawl_mod.known_ids_from([other])).run()
        self.assertEqual(sum(u.endswith("/replays/a") or u.endswith("/a/file") for u in site.calls), 0)
        self.assertTrue((self.out / "b.replay").exists())

    def test_should_stop_keeps_cursor_on_the_page(self):
        calls = []
        site = FakeSite({None: ["a", "b", "c"]})
        crawler = self.crawler(site, should_stop=lambda: len(calls) > 0)
        original = crawler.fetch_one

        def fetch(replay_id):
            calls.append(replay_id)
            return original(replay_id)
        crawler.fetch_one = fetch
        result = crawler.run()
        self.assertEqual(result["stats"]["saved"], 1)
        self.assertFalse(result["listing_complete"])
        self.assertIsNotNone(json.loads((self.out / "crawl_state.json").read_text())["cursor"])
        site2 = FakeSite({None: ["a", "b", "c"]})
        self.crawler(site2).run()
        self.assertEqual(sorted(p.stem for p in self.out.glob("*.replay")), ["a", "b", "c"])

    def test_429_honours_retry_after_and_recovers(self):
        site = FakeSite({None: ["a", "b"]}, fail_file_429_once=("a",))
        result = self.crawler(site).run()
        self.assertEqual(result["stats"]["saved"], 2)
        self.assertEqual(result["stats"]["waits_429"], 1)
        self.assertIn(7.0, self.clock.sleeps)
        self.assertEqual(sum(u.endswith("/a/file") for u in site.calls), 2)
        self.assertTrue(any("429" in line for line in self.logs))
        self.assertFalse(any("private body" in line for line in self.logs))

    def test_persistent_429_stops_cleanly_without_leaking(self):
        def always_429(url, *, headers, timeout):
            raise HTTPError(url, 429, "private body", {}, None)
        result = self.crawler(always_429, max_429=3).run()
        self.assertEqual(result["stats"]["saved"], 0)
        state = json.loads((self.out / "crawl_state.json").read_text())
        self.assertIn("429", state["errors"][0]["reason"])
        self.assertNotIn("private body", json.dumps(state))

    def test_pending_replays_are_deferred_not_done(self):
        site = FakeSite({None: ["a", "b"]}, statuses={"a": "pending"})
        result = self.crawler(site).run()
        self.assertEqual(result["stats"]["deferred"], 1)
        state = json.loads((self.out / "crawl_state.json").read_text())
        self.assertEqual(state["deferred"], {"a": "pending"})
        self.assertEqual(state["done"], ["b"])
        self.assertFalse((self.out / "a.replay").exists())

    def test_unsafe_next_url_refused(self):
        def fake(url, *, headers, timeout):
            return json.dumps({"list": [], "next": "https://unrelated.invalid/api/replays?after=x"}).encode()
        result = self.crawler(fake).run()
        state = json.loads((self.out / "crawl_state.json").read_text())
        self.assertIn("unsafe", state["errors"][0]["reason"])
        self.assertFalse(result["listing_complete"])

    def test_missing_key_fails_cleanly(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(KEY_ENV, None)
            with self.assertRaises(MissingKeyError):
                Crawler(self.out, build_filters())
            from rlreplay.cli import main
            with mock.patch("sys.stderr") as err:
                code = main(["crawl", "--out", self.tmp.name, "--name", "x"])
            self.assertEqual(code, 2)
            text = "".join(str(c.args[0]) for c in err.write.call_args_list if c.args)
            self.assertIn(KEY_ENV, text)
            self.assertFalse((Path(self.tmp.name) / "x").exists())

    def test_build_filters(self):
        f = build_filters(season="f20", after="2025-01-01T00:00:00Z")
        self.assertEqual(f["playlist"], "ranked-doubles")
        self.assertEqual((f["min-rank"], f["max-rank"]), ("diamond-1", "champion-3"))
        self.assertEqual((f["season"], f["count"], f["sort-by"], f["sort-dir"]),
                         ("f20", 200, "replay-date", "asc"))
        with self.assertRaises(ValueError):
            build_filters(count=500)


class PacerTests(unittest.TestCase):
    def test_free_tier_file_budget_is_200_per_hour_with_margin(self):
        clock = FakeClock()
        per_second, per_hour = TIERS["free"]["file"]
        self.assertEqual((per_second, per_hour), (1, 200))
        pacer = Pacer(per_second, per_hour, clock=clock.now, sleep=clock.sleep)
        start = clock.now()
        for _ in range(pacer.per_hour):
            pacer.acquire()
        self.assertEqual(pacer.per_hour, 180)
        self.assertLess(clock.now() - start, 3600)  # the per-second spacing alone does not take an hour
        pacer.acquire()  # the 181st call must wait for the oldest one to leave the sliding hour
        self.assertGreaterEqual(clock.now() - start, 3600)

    def test_per_second_spacing(self):
        clock = FakeClock()
        pacer = Pacer(2, None, clock=clock.now, sleep=clock.sleep)
        pacer.acquire(); pacer.acquire(); pacer.acquire()
        self.assertAlmostEqual(clock.now() - 1000.0, 2 * (1 / 1.8), places=6)

    def test_backoff_shared_and_capped(self):
        clock = FakeClock()
        backoff = Backoff(base=10, cap=100, clock=clock.now)
        a = Pacer(16, None, clock=clock.now, sleep=clock.sleep, backoff=backoff)
        b = Pacer(16, None, clock=clock.now, sleep=clock.sleep, backoff=backoff)
        self.assertEqual(backoff.refused(None), 10)
        self.assertEqual(backoff.refused(None), 20)
        self.assertEqual(backoff.refused(5000), 100)
        before = clock.now()
        b.acquire()  # waits for the shared deadline even though b never saw a 429
        self.assertGreaterEqual(clock.now() - before, 100 - 1e-9)
        a.acquire()
        self.assertEqual(backoff.remaining() <= 0, True)


if __name__ == "__main__":
    unittest.main()
