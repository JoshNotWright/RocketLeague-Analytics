"""Explicit, import-safe command-line entry points."""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import sys
import tempfile

from . import __version__


def read_json(path):
    with open(path, encoding="utf-8-sig") as stream:
        return json.load(stream)


def write_json(path, value):
    """Serialize fully before atomically replacing the requested output."""
    serialized = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n", dir=target.parent,
        prefix=target.name + ".", suffix=".tmp", delete=False
    ) as stream:
        pending = Path(stream.name)
        stream.write(serialized)
    try:
        os.replace(pending, target)
    finally:
        pending.unlink(missing_ok=True)


def _write_csv(path, rows):
    rows = list(rows)
    columns = sorted({key for row in rows for key in row})
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _report_value(matches):
    from .stats import aggregate_players, aggregate_teams
    return {
        "schema_version": 1,
        "match_count": len(matches),
        "players": aggregate_players(matches),
        "teams": aggregate_teams(matches),
        "methodology": {
            "identity": "Full platform player IDs; teams are observed match rosters.",
            "rates": "Observed metric totals divided by matched exposure, per 300 seconds.",
            "missing": "Missing values are excluded and coverage is reported.",
            "shooting": "Pooled credited goals / jointly observed shots.",
        },
    }


def _collect(args):
    from .collect import collect
    manifest = collect(read_json(args.config), args.output)
    # Do not print raw API payloads or authentication headers.
    print(f"Collection status: {manifest.get('status', 'unknown')}. Manifest: {Path(args.output) / 'manifest.json'}")
    return 0 if manifest.get("status") == "complete" else 2


def _import_json(args):
    from .data import from_ballchasing, write_matches
    metadata = read_json(args.metadata) if args.metadata else None
    matches = [
        from_ballchasing(read_json(path), metadata=metadata,
                        expected_players_per_team=args.players_per_team)
        for path in args.input
    ]
    write_matches(args.output, matches)
    print(f"Wrote normalized matches: {args.output}")
    return 0


def _import_csv(args):
    from .data import import_csv, write_matches
    match = import_csv(args.input, metadata=read_json(args.metadata),
                       column_map=read_json(args.columns) if args.columns else None)
    write_matches(args.output, [match])
    print(f"Wrote normalized match: {args.output}")
    return 0


def _report(args):
    from .data import load_matches
    from .stats import flatten_rows
    report = _report_value(load_matches(args.input))
    write_json(args.output, report)
    if args.csv_dir:
        _write_csv(Path(args.csv_dir) / "players.csv", flatten_rows(report["players"]))
        _write_csv(Path(args.csv_dir) / "teams.csv", flatten_rows(report["teams"]))
    print(f"Wrote report: {args.output}")
    return 0


def _reference(args):
    from .data import load_matches
    from .ratings import build_reference
    value = build_reference(
        load_matches(args.input), version=args.version, playlist=args.playlist,
        weights=read_json(args.weights) if args.weights else None,
        prior_seconds=args.prior_seconds,
    )
    write_json(args.output, value)
    print(f"Wrote frozen reference: {args.output}")
    return 0


def _rate(args):
    from .data import load_matches
    from .ratings import rate_players
    value = rate_players(load_matches(args.input), read_json(args.reference),
                         bootstrap_samples=args.bootstrap_samples, seed=args.seed)
    write_json(args.output, value)
    print(f"Wrote descriptive ratings: {args.output}")
    return 0


def _fit(args):
    from .data import load_matches
    from .impact import fit_model
    model = fit_model(load_matches(args.input),
                      replacement_player_ids=args.replacement_id,
                      regularization=args.regularization)
    write_json(args.output, model)
    print(f"Wrote experimental lineup model: {args.output}")
    return 0


def _evaluate(args):
    from .data import load_matches
    from .impact import evaluate_chronologically
    value = evaluate_chronologically(
        load_matches(args.input), replacement_player_ids=args.replacement_id,
        train_fraction=args.train_fraction, regularization=args.regularization,
        group_ids=read_json(args.series_map) if args.series_map else None,
    )
    write_json(args.output, value)
    print(f"Wrote chronological holdout evaluation: {args.output}")
    return 0


def _impact(args):
    from .data import load_matches
    from .impact import expected_wins_above_replacement
    value = expected_wins_above_replacement(
        read_json(args.model), load_matches(args.input), args.player_id)
    write_json(args.output, value)
    print(f"Wrote conditional win-impact estimate: {args.output}")
    return 0


def _demo(args):
    from .demo import run_demo
    result = run_demo(args.output, seed=args.seed)
    print(f"Synthetic demo written to {args.output}: {result['match_count']} matches.")
    print("Synthetic validation checks software behavior; it does not establish real-world model accuracy.")
    return 0


def parser():
    root = argparse.ArgumentParser(
        prog="rlanalytics", description="Reproducible match analytics and experimental lineup models."
    )
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)

    command = commands.add_parser("collect", help="Collect configured Ballchasing replays with a manifest.")
    command.add_argument("--config", required=True, help="JSON collection configuration; token is read from BALLCHASING_TOKEN.")
    command.add_argument("--output", required=True)
    command.set_defaults(func=_collect)

    command = commands.add_parser("import-json", help="Normalize downloaded Ballchasing replay detail JSON.")
    command.add_argument("--input", required=True, action="append", help="Repeat for each replay JSON.")
    command.add_argument("--metadata", help="Optional explicit metadata JSON.")
    command.add_argument("--players-per-team", type=int)
    command.add_argument("--output", required=True)
    command.set_defaults(func=_import_json)

    command = commands.add_parser("import-csv", help="Import one match using named columns and explicit metadata.")
    command.add_argument("--input", required=True)
    command.add_argument("--metadata", required=True)
    command.add_argument("--columns", help="Optional canonical-field to CSV-column mapping JSON.")
    command.add_argument("--output", required=True)
    command.set_defaults(func=_import_csv)

    command = commands.add_parser("report", help="Aggregate players and observed rosters with coverage.")
    command.add_argument("--input", required=True)
    command.add_argument("--output", required=True)
    command.add_argument("--csv-dir")
    command.set_defaults(func=_report)

    command = commands.add_parser("reference", help="Fit and save a versioned descriptive reference cohort.")
    command.add_argument("--input", required=True)
    command.add_argument("--output", required=True)
    command.add_argument("--version", required=True)
    command.add_argument("--playlist")
    command.add_argument("--weights", help="JSON object of metric weights.")
    command.add_argument("--prior-seconds", type=float, default=1500.0)
    command.set_defaults(func=_reference)

    command = commands.add_parser("rate", help="Score against a saved descriptive reference.")
    command.add_argument("--input", required=True)
    command.add_argument("--reference", required=True)
    command.add_argument("--output", required=True)
    command.add_argument("--bootstrap-samples", type=int, default=400)
    command.add_argument("--seed", type=int, default=0)
    command.set_defaults(func=_rate)

    command = commands.add_parser("fit-impact", help="Fit the experimental pregame lineup model.")
    command.add_argument("--input", required=True)
    command.add_argument("--output", required=True)
    command.add_argument("--replacement-id", action="append", required=True)
    command.add_argument("--regularization", type=float, default=1.0)
    command.set_defaults(func=_fit)

    command = commands.add_parser("evaluate", help="Evaluate a future holdout, keeping series separate.")
    command.add_argument("--input", required=True)
    command.add_argument("--output", required=True)
    command.add_argument("--replacement-id", action="append", required=True)
    command.add_argument("--regularization", type=float, default=1.0)
    command.add_argument("--train-fraction", type=float, default=0.8)
    command.add_argument("--series-map", help="Optional JSON mapping match IDs to series IDs.")
    command.set_defaults(func=_evaluate)

    command = commands.add_parser("impact", help="Estimate win differences for an explicit match schedule.")
    command.add_argument("--input", required=True)
    command.add_argument("--model", required=True)
    command.add_argument("--player-id", required=True)
    command.add_argument("--output", required=True)
    command.set_defaults(func=_impact)

    command = commands.add_parser("demo", help="Run the complete pipeline offline on synthetic data.")
    command.add_argument("--output", required=True)
    command.add_argument("--seed", type=int, default=7)
    command.set_defaults(func=_demo)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
