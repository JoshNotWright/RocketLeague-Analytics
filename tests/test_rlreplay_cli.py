import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import polars as pl
except ImportError:  # pragma: no cover
    pl = None

if pl is not None:
    from rlreplay.cli import main

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)


def table(counts: dict) -> "pl.DataFrame":
    rows = []
    for player, n in counts.items():
        for i in range(n):
            rows.append({"replay_id": f"{player}-{i}", "player_id": player, "playlist": "ranked-doubles",
                         "played_at": T0 + timedelta(hours=i)})
    return pl.DataFrame(rows)


@unittest.skipIf(pl is None, "polars not installed")
class SelectTests(unittest.TestCase):
    def test_select_caps_heavy_uploaders_and_respects_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory) / "t.parquet"
            table({"whale": 500, "a": 120, "b": 110, "c": 100, "sparse": 10}).write_parquet(src)
            out = Path(directory) / "subset.json"
            main(["select", "--table", str(src), "--out", str(out), "--n-players", "10",
                  "--max-replays", "250", "--min-games", "50", "--max-games", "200"])
            payload = json.loads(out.read_text())
            # whale exceeds max-games; a+b fit (230); c would exceed 250; sparse is below min-games
            self.assertEqual(payload["focal_players"], ["a", "b"])
            self.assertEqual(len(payload["replay_ids"]), 230)
            self.assertEqual(payload["games_per_focal_player"], {"a": 120, "b": 110})


if __name__ == "__main__":
    unittest.main()
