"""Player-focused second crawl stage: choose repeat players from a band corpus, pull their games offline."""
import json
import tempfile
import unittest
from pathlib import Path

from rlreplay.crawl import (Crawler, build_filters, crawl_players, metadata_ids, metadata_line, player_appearances,
                            select_players)
from tests.test_rlreplay_crawl import FakeClock, FakeSite, detail


def _write_meta(path: Path, replays: list[tuple[str, list[tuple[str, int]]]]):
    """replays: [(replay_id, [(player_numeric_id, tier), ...])] -> metadata.json in the crawler's shape."""
    lines = []
    for rid, players in replays:
        d = detail(rid)
        blue = [{"name": f"p{pid}", "id": {"platform": "steam", "id": pid}, "rank": {"tier": tier, "division": 1}}
                for pid, tier in players[:2]]
        orange = [{"name": f"p{pid}", "id": {"platform": "steam", "id": pid}, "rank": {"tier": tier, "division": 1}}
                  for pid, tier in players[2:]]
        d["blue"]["players"], d["orange"]["players"] = blue, orange
        lines.append(metadata_line(d))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class PlayerStageTests(unittest.TestCase):
    def test_appearances_and_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / "band" / "metadata.json"
            # player 1 appears 3 times in band; player 2 twice but tagged Champion (out of band);
            # player 3 once; player 4 three times and already has enough games.
            _write_meta(meta, [
                ("r1", [("1", 14), ("2", 17), ("3", 14), ("4", 14)]),
                ("r2", [("1", 14), ("2", 17), ("5", 14), ("4", 14)]),
                ("r3", [("1", 14), ("6", 14), ("7", 14), ("4", 14)]),
            ])
            seen = player_appearances([meta], min_points=52, max_points=63)
        self.assertEqual(seen["steam:1"]["games"], 3)
        self.assertEqual(seen["steam:1"]["in_band"], 3)
        self.assertEqual(seen["steam:2"]["in_band"], 0)
        chosen = select_players(seen, n=10, per_player=3, min_games=2)
        self.assertEqual(chosen, [])  # players 1 and 4 already have 3 games; 2 is out of band; others seen once
        chosen = select_players(seen, n=10, per_player=5, min_games=2)
        self.assertEqual(sorted(chosen), [("steam:1", 2), ("steam:4", 2)])
        chosen = select_players(seen, n=1, per_player=5, min_games=2)
        self.assertEqual(len(chosen), 1)

    def test_crawl_players_skips_known_replays_and_caps_per_player(self):
        with tempfile.TemporaryDirectory() as tmp:
            band = Path(tmp) / "band"
            _write_meta(band / "metadata.json", [("known1", [("1", 14), ("2", 14), ("3", 14), ("4", 14)])])
            known = metadata_ids(band / "metadata.json")
            self.assertEqual(known, {"known1"})
            site = FakeSite({None: ["known1", "a", "b", "c", "d"]})
            clock = FakeClock()
            logs = []
            out = Path(tmp) / "players"
            totals = crawl_players(out, [("steam:1", 2), ("steam:9", 1)], build_filters(), known_ids=known,
                                   transport=site, key="test-key-value", sleep=clock.sleep, clock=clock.now, log=logs.append)
        self.assertEqual(totals["players"], 2)
        # first player: known1 skipped, a and b saved (cap 2); second player: c saved (a, b now known too)
        self.assertEqual(totals["saved"], 3)
        self.assertEqual(sum(u.endswith("/file") for u in site.calls), 3)
        self.assertNotIn("known1", {u.split("/")[-2] for u in site.calls if u.endswith("/file")})
        list_calls = [u for u in site.calls if "/api/replays?" in u]
        self.assertTrue(all("player-id=steam" in u for u in list_calls))
        self.assertTrue(any("steam%3A1" in u or "steam:1" in u for u in list_calls))

    def test_known_ids_count_as_done_in_crawler(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = FakeSite({None: ["x", "y"]})
            clock = FakeClock()
            result = Crawler(Path(tmp) / "o", build_filters(), transport=site, key="test-key-value", sleep=clock.sleep,
                             clock=clock.now, log=lambda m: None, known_ids={"x"}).run()
        self.assertEqual(result["stats"]["saved"], 1)
        self.assertEqual(result["stats"]["skipped_done"], 1)


if __name__ == "__main__":
    unittest.main()
