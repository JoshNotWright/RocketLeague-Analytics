"""Deterministic synthetic fixtures, explicitly unsuitable as empirical evidence."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import random

PLAYER_IDS = tuple(f"steam:{76560000000000001 + index}" for index in range(8))
REPLACEMENT_IDS = PLAYER_IDS[-2:]


def synthetic_matches(count=100, *, seed=7):
    """Generate varied, complete doubles lineups with known synthetic strengths."""
    if not isinstance(count, int) or count < 20:
        raise ValueError("The synthetic cohort needs at least 20 matches.")
    rng = random.Random(seed)
    names = ("Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel")
    strengths = (0.75, 0.5, 0.25, 0.1, -0.1, -0.25, -0.5, -0.75)
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    matches = []
    for index in range(count):
        roster = rng.sample(range(len(PLAYER_IDS)), 4)
        contrast = sum(strengths[i] for i in roster[:2]) - sum(strengths[i] for i in roster[2:])
        blue_wins = rng.random() < 1 / (1 + math.exp(-contrast))
        scores = (3, 1) if blue_wins else (1, 3)
        duration = 360.0 if index % 7 == 0 else 300.0
        team_shots = (rng.randint(6, 12), rng.randint(6, 12))
        team_demos = (rng.randint(0, 6), rng.randint(0, 6))
        players = []
        for team_index, team in enumerate(("blue", "orange")):
            first_goals = rng.randint(0, scores[team_index])
            first_shots = rng.randint(first_goals, team_shots[team_index] - (scores[team_index] - first_goals))
            goals = (first_goals, scores[team_index] - first_goals)
            shots = (first_shots, team_shots[team_index] - first_shots)
            save_total = rng.randint(0, team_shots[1-team_index] - scores[1-team_index])
            first_saves = rng.randint(0, save_total)
            saves = (first_saves, save_total - first_saves)
            first_demos = rng.randint(0, team_demos[team_index])
            demos = (first_demos, team_demos[team_index] - first_demos)
            first_taken = rng.randint(0, team_demos[1-team_index])
            taken = (first_taken, team_demos[1-team_index] - first_taken)
            for slot in range(2):
                player = roster[2 * team_index + slot]
                players.append({
                    "player_id": PLAYER_IDS[player], "name": names[player],
                    "team": team, "team_name": f"Synthetic {team}",
                    "duration_seconds": duration,
                    "goals": goals[slot], "shots": shots[slot],
                    "assists": rng.randint(0, goals[1-slot]),
                    "saves": saves[slot],
                    "shots_against": team_shots[1-team_index],
                    "goals_against": scores[1-team_index],
                    "demos": demos[slot], "demos_taken": taken[slot],
                    "boost_mean": float(rng.randint(30, 70)),
                })
        matches.append({
            "schema_version": 1,
            "match_id": f"synthetic-match-{index:04d}",
            "replay_ids": [f"synthetic-replay-{index:04d}"],
            "series_id": f"synthetic-series-{index // 2:04d}",
            "playlist": "ranked-doubles",
            "played_at": (start + timedelta(minutes=10 * index)).isoformat(),
            "duration_seconds": duration,
            "blue_score": scores[0], "orange_score": scores[1],
            "players": players,
        })
    return matches


def run_demo(output, *, seed=7):
    from .cli import _report_value, write_json
    from .data import write_matches
    from .impact import evaluate_chronologically, fit_model, expected_wins_above_replacement
    from .ratings import build_reference, rate_players

    directory = Path(output)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Demo output must be a new or empty directory.")
    directory.mkdir(parents=True, exist_ok=True)
    matches = synthetic_matches(seed=seed)
    # Reference/training data precede target/evaluation matches.
    training, future = matches[:80], matches[80:]
    write_matches(directory / "matches.jsonl", matches)
    write_matches(directory / "training.jsonl", training)
    write_matches(directory / "future.jsonl", future)
    reference = build_reference(training, version="synthetic-demo-v1", playlist="ranked-doubles")
    model = fit_model(training, replacement_player_ids=REPLACEMENT_IDS)
    outputs = {
        "report.json": _report_value(matches),
        "reference.json": reference,
        "ratings.json": rate_players(future, reference, bootstrap_samples=100, seed=seed),
        "model.json": model,
        "evaluation.json": evaluate_chronologically(
            matches, replacement_player_ids=REPLACEMENT_IDS, train_fraction=0.8),
        "impact.json": expected_wins_above_replacement(model, future, PLAYER_IDS[0]),
    }
    for filename, value in outputs.items():
        write_json(directory / filename, value)
    summary = {
        "synthetic": True, "seed": seed, "match_count": len(matches),
        "training_matches": len(training), "future_matches": len(future),
        "replacement_player_ids": list(REPLACEMENT_IDS),
        "warning": "Software demonstration only. These results do not measure real-world accuracy or player skill.",
    }
    write_json(directory / "DEMO.json", summary)
    return summary
