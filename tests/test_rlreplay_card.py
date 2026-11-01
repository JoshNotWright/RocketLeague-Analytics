"""Player card rendering from a score report: percentiles in the good direction, reliability fading."""
import json
import tempfile
import unittest
from pathlib import Path

try:
    import polars as pl
except ImportError:  # pragma: no cover
    pl = None

if pl is not None:
    from rlreplay import card


def _report_dir(tmp):
    metrics = {"xg": {"raw": 1.3, "shrunk": 1.29, "ref_percentile": 40.0, "ref_median": 1.4},
               "goals_above_xg": {"raw": 0.5, "shrunk": 0.02, "ref_percentile": 99.0, "ref_median": 0.0},
               "kickoff_time_to_ball": {"raw": 2.2, "shrunk": 2.2, "ref_percentile": 95.0, "ref_median": 2.0},
               "big_pads": {"raw": 14.0, "shrunk": 14.0, "ref_percentile": 10.0, "ref_median": 19.0}}
    report = {"replays": 3, "failures": {}, "players": [
        {"player_id": "epic:1", "name": "Alice", "games": 3, "win_rate": 0.67, "median_game_rank": 57.0,
         "band": "diamond", "reference": "testref", "reference_band": "diamond", "metrics": metrics}]}
    (Path(tmp) / "report.json").write_text(json.dumps(report), encoding="utf-8")
    pl.DataFrame({"player_id": ["epic:1"] * 3, "date": ["2024-01-01", "2024-02-01", "2025-01-01"],
                  "xg": [1.0, 1.5, 1.4], "value_added": [0.1, 0.2, 0.3], "goals_above_xg": [0.0, 0.5, 1.0]}).write_parquet(Path(tmp) / "player_game.parquet")
    ref_dir = Path(tmp) / "ref"
    ref_dir.mkdir()
    (ref_dir / "testref.json").write_text(json.dumps({"band": "diamond", "players": 40, "min_games": 30, "metrics": {
        "xg": {"higher_is_better": True, "reliability": 0.6},
        "goals_above_xg": {"higher_is_better": True, "reliability": 0.05},
        "kickoff_time_to_ball": {"higher_is_better": False, "reliability": 0.4},
        "big_pads": {"higher_is_better": None, "reliability": 0.8}}}), encoding="utf-8")
    return Path(tmp), ref_dir


@unittest.skipIf(pl is None, "polars not installed")
class CardTests(unittest.TestCase):
    def test_rank_name(self):
        self.assertEqual(card.rank_name(52), "Diamond I div 1")
        self.assertEqual(card.rank_name(59.8), "Diamond II div 4")
        self.assertEqual(card.rank_name(76), "Grand Champion I div 1")
        self.assertEqual(card.rank_name(88), "Supersonic Legend")
        self.assertEqual(card.rank_name(None), "rank unknown")

    def test_direction_and_color(self):
        self.assertEqual(card.directional_percentile({"ref_percentile": 95.0}, False), 5.0)
        self.assertEqual(card.directional_percentile({"ref_percentile": 95.0}, True), 95.0)
        self.assertEqual(card.directional_percentile({"ref_percentile": 10.0}, None), 10.0)
        self.assertEqual(card.bar_color(95.0, True), "var(--good-2)")
        self.assertEqual(card.bar_color(5.0, True), "var(--poor-2)")
        self.assertEqual(card.bar_color(10.0, None), "var(--neutral-bar)")

    def test_render_writes_svg_and_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_dir, ref_dir = _report_dir(tmp)
            result = card.write_card(report_dir, "alice", report_dir, reference_dir=ref_dir)
            svg = Path(result["svg"]).read_text(encoding="utf-8")
            html = Path(result["html"]).read_text(encoding="utf-8")
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("Diamond II div 2", svg)                       # 57 points
        self.assertIn('data-metric="kickoff_time_to_ball" data-label="Kickoff time to ball" data-raw="2.20s"', svg)
        self.assertIn('data-pct="5"', svg)                           # lower is better: 95 -> 5
        self.assertIn('opacity="0.34"', svg)                         # reliability 0.05 -> 0.3 + 0.045, rounded
        self.assertIn(">noise<", svg)                                # goals above xG flagged
        self.assertIn(">style<", svg)                                # big pads has no good direction
        self.assertIn("BY YEAR", svg)
        self.assertIn("<title>Alice player card</title>", html)
        self.assertIn(svg, html)


if __name__ == "__main__":
    unittest.main()
