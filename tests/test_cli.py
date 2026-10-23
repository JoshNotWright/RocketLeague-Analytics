import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rlanalytics.cli import main, write_json
from rlanalytics.demo import synthetic_matches, REPLACEMENT_IDS


class CliTests(unittest.TestCase):
    def call(self, *args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(list(args))

    def test_missing_input_exits_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / "missing.jsonl")
            self.assertEqual(self.call("report", "--input", missing,
                                       "--output", str(Path(directory) / "out.json")), 2)

    def test_incomplete_collection_returns_failure_status(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            write_json(config, {})
            with patch("rlanalytics.collect.collect", return_value={"status": "incomplete"}):
                self.assertEqual(self.call("collect", "--config", str(config),
                                          "--output", directory), 2)

    def test_nonfinite_output_cannot_replace_previous_result(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "out.json"
            write_json(path, {"preserved": True})
            with self.assertRaises(ValueError):
                write_json(path, {"bad": float("nan")})
            self.assertEqual(json.loads(path.read_text()), {"preserved": True})

    def test_demo_is_reproducible_and_complete(self):
        self.assertEqual(synthetic_matches(20, seed=4), synthetic_matches(20, seed=4))
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.call("demo", "--output", directory), 0)
            root = Path(directory)
            for filename in ("report.json", "reference.json", "ratings.json",
                             "model.json", "evaluation.json", "impact.json", "DEMO.json"):
                self.assertTrue((root / filename).is_file(), filename)
                json.loads((root / filename).read_text(encoding="utf-8"))
            self.assertTrue(json.loads((root / "DEMO.json").read_text())["synthetic"])
            self.assertEqual(self.call("report", "--input", str(root / "matches.jsonl"),
                "--output", str(root / "new-report.json"), "--csv-dir", str(root / "csv")), 0)
            self.assertTrue((root / "csv" / "players.csv").is_file())
            self.assertTrue((root / "csv" / "teams.csv").is_file())

    def test_explicit_rating_and_evaluation_workflow(self):
        from rlanalytics.data import write_matches
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = synthetic_matches(30)
            write_matches(root / "matches.jsonl", dataset)
            source = str(root / "matches.jsonl")
            reference = str(root / "reference.json")
            self.assertEqual(self.call("reference", "--input", source, "--output", reference,
                                      "--version", "test-v1", "--playlist", "ranked-doubles"), 0)
            self.assertEqual(self.call("rate", "--input", source, "--reference", reference,
                                      "--output", str(root / "ratings.json"),
                                      "--bootstrap-samples", "20"), 0)
            pool = [item for player_id in REPLACEMENT_IDS for item in ("--replacement-id", player_id)]
            self.assertEqual(self.call("evaluate", "--input", source, "--output",
                                      str(root / "evaluation.json"), *pool), 0)


if __name__ == "__main__":
    unittest.main()
