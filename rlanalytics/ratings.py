"""Frozen-reference descriptive ratings, not causal impact or wins."""
from __future__ import annotations
from collections import defaultdict
import hashlib
import json
import math
from typing import Iterable, Mapping
import numpy as np

REFERENCE_SCHEMA_VERSION = 1
DEFAULT_WEIGHTS = {"goals": 1.0, "assists": 0.75, "shots": 0.10, "saves": 0.50, "demos": 0.20}
METRICS = tuple(DEFAULT_WEIGHTS)
LIMITATIONS = [
    "Descriptive weighted activity index; not wins or causal player value.",
    "Weights are editorial choices, not fitted or validated effects on winning.",
    "No adjustment for rank, teammates, opponents, roles or game state.",
    "Bootstrap intervals are conditional on the frozen reference and sampled matches.",
    "Matches may be dependent within a series; match bootstrap may understate uncertainty.",
]

def _unique_matches(matches: Iterable[Mapping]) -> list[dict]:
    result, fingerprints = {}, {}
    for match in matches:
        if match.get("schema_version") != 1:
            raise ValueError("Expected canonical match schema_version=1")
        match_id = match.get("match_id")
        if not isinstance(match_id, str) or not match_id.strip():
            raise ValueError("Each match needs a nonempty match_id")
        canonical = {k: v for k, v in match.items() if k != "replay_ids"}
        fingerprint = json.dumps(canonical, sort_keys=True, allow_nan=False)
        if match_id in result:
            if fingerprints[match_id] != fingerprint:
                raise ValueError("Conflicting records for match_id " + match_id)
            continue
        result[match_id], fingerprints[match_id] = dict(match), fingerprint
    return [result[k] for k in sorted(result)]

def _cohort(matches, playlist=None):
    if playlist is not None:
        matches = [m for m in matches if m.get("playlist") == playlist]
    if not matches:
        raise ValueError("No matches in the requested reference cohort")
    playlists = {m.get("playlist") for m in matches}
    if len(playlists) != 1 or not all(isinstance(x, str) and x for x in playlists):
        raise ValueError("Use a separate reference for each playlist")
    return matches, next(iter(playlists))

def _cohort_team_size(matches, playlist):
    known = {"ranked-duels": 1, "ranked-doubles": 2, "ranked-standard": 3, "ranked-solo-standard": 3}
    sizes = {m.get("expected_players_per_team", known.get(playlist)) for m in matches}
    if len(sizes) != 1:
        raise ValueError("Use a separate reference for each team size")
    size = next(iter(sizes))
    if type(size) is not int or size < 1:
        raise ValueError("Reference cohort requires explicit expected_players_per_team")
    if playlist in known and size != known[playlist]:
        raise ValueError("Reference team size contradicts playlist")
    for match in matches:
        for color in ("blue", "orange"):
            if sum(p.get("team") == color for p in match.get("players", [])) != size:
                raise ValueError("Reference requires complete rosters of a consistent team size")
    return size

def _finite_number(value, field, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(field + " must be a finite number; missing data is not zero")
    value = float(value)
    if not math.isfinite(value) or value < 0 or (positive and value <= 0):
        raise ValueError(field + " has an invalid value")
    return value

def _observations(matches, metrics=METRICS):
    observations = defaultdict(list)
    for match in matches:
        players = match.get("players")
        if not isinstance(players, list) or not players:
            raise ValueError("Each match needs players")
        seen = set()
        match_duration = _finite_number(match.get("duration_seconds"), "duration_seconds", positive=True)
        for player in players:
            pid = player.get("player_id")
            if not isinstance(pid, str) or ":" not in pid or pid in seen:
                raise ValueError("Players need unique platform:id identifiers within a match")
            seen.add(pid)
            duration = _finite_number(player.get("duration_seconds"), "player duration_seconds", positive=True)
            if duration > match_duration + 1:
                raise ValueError("Player exposure cannot exceed match duration (apart from provider rounding)")
            counts = {}
            for metric in metrics:
                count = _finite_number(player.get(metric), metric)
                if not count.is_integer():
                    raise ValueError(metric + " must be a nonnegative integer count")
                counts[metric] = count
            observations[pid].append({"match_id": match["match_id"], "name": player.get("name", pid),
                                      "duration_seconds": duration, "counts": counts})
    return dict(observations)

def _totals(rows, metrics=METRICS):
    exposure = sum(r["duration_seconds"] for r in rows)
    counts = {metric: sum(r["counts"][metric] for r in rows) for metric in metrics}
    return exposure, counts

def build_reference(matches, *, version, playlist=None, weights=None, prior_seconds=1500.0):
    """Fit once. Means/scales equally weight aggregated player rates.

    Prior exposure is a configurable heuristic, not fitted reliability.
    """
    if not isinstance(version, str) or not version.strip():
        raise ValueError("Supply a nonempty reference version")
    prior_seconds = _finite_number(prior_seconds, "prior_seconds")
    weights = dict(DEFAULT_WEIGHTS if weights is None else weights)
    if not weights or set(weights) - set(METRICS):
        raise ValueError("Weights must use supported event metrics: " + ", ".join(METRICS))
    for key, value in weights.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("Weights must be finite numbers")
        weights[key] = float(value)
    weights = {k: v for k, v in weights.items() if v != 0}
    if not weights:
        raise ValueError("At least one weight must be nonzero")
    matches, playlist = _cohort(_unique_matches(matches), playlist)
    team_size = _cohort_team_size(matches, playlist)
    observations = _observations(matches, weights)
    if len(observations) < 2:
        raise ValueError("A reference needs at least two distinct players")
    rates = []
    for rows in observations.values():
        seconds, counts = _totals(rows, weights)
        rates.append({metric: 300.0 * counts[metric] / seconds for metric in weights})
    parameters = {}
    for metric in weights:
        values = np.array([r[metric] for r in rates], dtype=float)
        std = float(np.std(values))
        parameters[metric] = {"mean": float(np.mean(values)), "scale": std if std > 1e-12 else 1.0,
                              "constant_reference": std <= 1e-12}
    match_ids = sorted(m["match_id"] for m in matches)
    dates = sorted(m["played_at"] for m in matches if isinstance(m.get("played_at"), str))
    return {"schema_version": 1, "kind": "frozen_descriptive_activity_reference", "version": version.strip(),
            "cohort": {"playlist": playlist, "team_size": team_size, "rank_adjustment": "none"}, "rate_seconds": 300.0,
            "weights": weights, "metrics": parameters, "prior_seconds": prior_seconds,
            "score_definition": "weighted mean of standardized, exposure-shrunk event rates; constant-reference components contribute zero",
            "missing_data_policy": "reject missing weighted event counts; never impute zero",
            "reference_players": len(observations), "reference_matches": len(matches),
            "match_ids_sha256": hashlib.sha256(json.dumps(match_ids).encode("utf-8")).hexdigest(),
            "played_at_min": dates[0] if dates else None, "played_at_max": dates[-1] if dates else None,
            "limitations": list(LIMITATIONS)}

def _validate_reference(reference):
    if not isinstance(reference, Mapping) or reference.get("schema_version") != 1 or reference.get("kind") != "frozen_descriptive_activity_reference":
        raise ValueError("Unsupported rating reference schema")
    if not reference.get("version") or not reference.get("cohort", {}).get("playlist"):
        raise ValueError("Reference needs a version and playlist")
    if reference.get("rate_seconds") != 300.0:
        raise ValueError("Reference rate_seconds must be 300")
    _finite_number(reference.get("prior_seconds"), "prior_seconds")
    weights = reference.get("weights", {})
    if not weights or set(weights) - set(METRICS):
        raise ValueError("Invalid reference weights")
    magnitude = 0.0
    for metric, weight in weights.items():
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight):
            raise ValueError("Invalid reference weight")
        magnitude += abs(weight)
        parameters = reference.get("metrics", {}).get(metric, {})
        _finite_number(parameters.get("mean"), metric + " reference mean")
        _finite_number(parameters.get("scale"), metric + " reference scale", positive=True)
        if type(parameters.get("constant_reference")) is not bool:
            raise ValueError("Reference constant_reference must be a boolean")
    if magnitude == 0:
        raise ValueError("Reference weights cannot all be zero")

def _score(counts, seconds, reference):
    prior, shrunk, terms = reference["prior_seconds"], {}, []
    for metric, weight in reference["weights"].items():
        parameters = reference["metrics"][metric]
        shrunk[metric] = (300.0 * counts[metric] + prior * parameters["mean"]) / (seconds + prior)
        standardized = 0.0 if parameters["constant_reference"] else (shrunk[metric] - parameters["mean"]) / parameters["scale"]
        terms.append(weight * standardized)
    return float(sum(terms) / sum(abs(w) for w in reference["weights"].values())), shrunk

def rate_players(matches, reference, *, bootstrap_samples=400, seed=0):
    """Score unchanged reference; reject missing weighted components explicitly."""
    _validate_reference(reference)
    if isinstance(bootstrap_samples, bool) or not isinstance(bootstrap_samples, int) or bootstrap_samples < 0:
        raise ValueError("bootstrap_samples must be a nonnegative integer")
    matches = _unique_matches(matches)
    if not matches:
        return []
    matches, playlist = _cohort(matches)
    if playlist != reference["cohort"]["playlist"]:
        raise ValueError("Scoring playlist differs from frozen reference playlist")
    if _cohort_team_size(matches, playlist) != reference["cohort"].get("team_size"):
        raise ValueError("Scoring team size differs from frozen reference")
    metrics = tuple(reference["weights"])
    observations, result = _observations(matches, metrics), []
    for pid in sorted(observations):
        player_seed = int.from_bytes(hashlib.sha256((str(seed) + ":" + pid).encode()).digest()[:8], "big")
        rng = np.random.default_rng(player_seed)
        rows = observations[pid]
        seconds, counts = _totals(rows, metrics)
        raw_rates = {metric: 300.0 * counts[metric] / seconds for metric in metrics}
        score, shrunk = _score(counts, seconds, reference)
        interval, flags = None, []
        if any(reference["metrics"][m]["constant_reference"] for m in metrics):
            flags.append("constant_reference_components_contribute_zero")
        if len(rows) < 5:
            flags.append("insufficient_matches_for_bootstrap_interval")
        elif bootstrap_samples < 100:
            flags.append("bootstrap_disabled_or_too_few_resamples")
        else:
            samples = []
            for _ in range(bootstrap_samples):
                selection = rng.integers(0, len(rows), size=len(rows))
                sample_seconds, sample_counts = _totals([rows[int(i)] for i in selection], metrics)
                samples.append(_score(sample_counts, sample_seconds, reference)[0])
            lower, upper = np.quantile(samples, [0.025, 0.975])
            interval = {"lower": float(lower), "upper": float(upper), "level": 0.95,
                        "method": "match_percentile_bootstrap_conditional_on_reference", "resamples": bootstrap_samples}
            if float(lower) == float(upper):
                flags.append("no_observed_between_match_variation_interval_does_not_establish_certainty")
        result.append({"player_id": pid, "names": sorted({str(r["name"]) for r in rows}), "playlist": playlist,
                       "matches": len(rows), "duration_seconds": seconds,
                       "counts": {k: int(v) for k, v in counts.items()}, "rates_per_5_minutes": raw_rates,
                       "shrunk_rates_per_5_minutes": shrunk,
                       "shrinkage_data_weight": seconds / (seconds + reference["prior_seconds"]),
                       "descriptive_index": score, "reference_version": reference["version"], "interval": interval,
                       "component_coverage": {metric: 1.0 for metric in metrics},
                       "flags": flags, "limitations": list(LIMITATIONS)})
    return sorted(result, key=lambda row: (-row["descriptive_index"], row["player_id"]))
