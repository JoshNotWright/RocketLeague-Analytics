"""Experimental pregame roster model, not validated causal WAR.

Only IDs, team membership and playlist enter predictors. Final scores are labels.
Persistent rosters cannot separate individual effects; regularization does not
solve that causal identification problem.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime
import math
from typing import Mapping
import numpy as np
from .ratings import _unique_matches

MODEL_SCHEMA_VERSION = 1
LIMITATIONS = [
    "Experimental model, not a validated causal WAR estimate.",
    "Roster associations confound player skill, teammate synergy, opponents and selection.",
    "Persistent rosters and disconnected competition limit individual identification.",
    "No rank adjustment; comparisons are only within the recorded playlist and team size.",
    "Intervals use a local penalized-likelihood approximation, not causal confidence intervals.",
    "Intervals omit future-outcome noise, model misspecification and unobserved player skill.",
    "Counterfactual replacement is an explicit pool-average coefficient, not an observed substitution.",
    "Only explicitly flagged forfeits are excluded; an absent flag does not prove a completed game.",
]
_PLAYLIST_SIZES = {
    "ranked-duels": 1, "ranked-duel": 1, "ranked-solo-duel": 1, "unranked-duels": 1,
    "casual-duel": 1, "duel": 1, "1v1": 1,
    "ranked-doubles": 2, "unranked-doubles": 2, "casual-doubles": 2, "doubles": 2, "2v2": 2,
    "ranked-standard": 3, "ranked-solo-standard": 3, "unranked-standard": 3,
    "casual-standard": 3, "standard": 3, "3v3": 3,
}
_PRIVATE_PLAYLISTS = {"private", "private-match", "tournament"}

def _roster(match, cohort=None):
    playlist = match.get("playlist")
    if playlist not in _PLAYLIST_SIZES and playlist not in _PRIVATE_PLAYLISTS:
        raise ValueError("Unsupported playlist for roster model: " + str(playlist))
    players = match.get("players")
    if not isinstance(players, list) or not players:
        raise ValueError("A complete pregame roster is required")
    blue, orange, seen = [], [], set()
    for player in players:
        pid = player.get("player_id")
        if not isinstance(pid, str) or ":" not in pid or pid in seen:
            raise ValueError("Roster requires unique platform:id player identifiers")
        seen.add(pid)
        if player.get("team") == "blue":
            blue.append(pid)
        elif player.get("team") == "orange":
            orange.append(pid)
        else:
            raise ValueError("Player team must be blue or orange")
    size = _PLAYLIST_SIZES.get(playlist, match.get("expected_players_per_team"))
    if size not in (1, 2, 3) or len(blue) != size or len(orange) != size:
        raise ValueError("Incomplete or unbalanced roster for playlist; private matches need expected_players_per_team")
    result = {"playlist": playlist, "team_size": size, "rank_adjustment": "none"}
    if cohort is not None and (playlist != cohort["playlist"] or size != cohort["team_size"]):
        raise ValueError("Match differs from model playlist/team-size cohort")
    return sorted(blue), sorted(orange), result

def _label(match):
    for field in ("forfeit", "is_forfeit"):
        if field in match and type(match[field]) is not bool:
            raise ValueError("Explicit forfeit flags must be booleans")
    if match.get("forfeit") or match.get("is_forfeit"):
        return None
    scores = []
    for field in ("blue_score", "orange_score"):
        value = match.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("Training/evaluation requires finite final scores")
        if value < 0 or int(value) != value:
            raise ValueError("Final scores must be nonnegative integers")
        scores.append(value)
    return None if scores[0] == scores[1] else float(scores[0] > scores[1])

def _eligible(matches):
    included, cohort = [], None
    excluded = {"ties": 0, "forfeits": 0}
    for match in _unique_matches(matches):
        _, _, current = _roster(match, cohort)
        if cohort is None:
            cohort = current
        label = _label(match)
        if label is None:
            excluded["forfeits" if match.get("forfeit") or match.get("is_forfeit") else "ties"] += 1
        else:
            included.append(match)
    if not included:
        raise ValueError("No eligible completed, untied matches")
    return included, cohort, excluded

def _sigmoid(value):
    return np.exp(-np.logaddexp(0.0, -np.asarray(value, dtype=float)))

def _design(match, player_ids, cohort):
    blue, orange, _ = _roster(match, cohort)
    index = {pid: i + 1 for i, pid in enumerate(player_ids)}
    vector = np.zeros(len(player_ids) + 1)
    vector[0] = 1.0
    unknown = []
    for sign, roster in ((1.0, blue), (-1.0, orange)):
        for pid in roster:
            if pid in index:
                vector[index[pid]] += sign
            else:
                unknown.append(pid)
    return vector, sorted(unknown), blue, orange

def _fit_matrix(x, y, regularization):
    theta = np.zeros(x.shape[1])
    penalty = np.full(x.shape[1], regularization)
    penalty[0] = 1e-6
    def objective(value):
        logits = x @ value
        return float(np.sum(np.logaddexp(0.0, logits) - y * logits)
                     + 0.5 * np.sum(penalty * value * value))
    converged = False
    iteration = 0
    for iteration in range(100):
        probabilities = _sigmoid(x @ theta)
        gradient = x.T @ (probabilities - y) + penalty * theta
        hessian = x.T @ ((probabilities * (1.0 - probabilities))[:, None] * x) + np.diag(penalty)
        if float(np.max(np.abs(gradient))) < 1e-8:
            converged = True
            break
        step = np.linalg.solve(hessian, gradient)
        scale, old = 1.0, objective(theta)
        while scale > 1e-8:
            candidate = theta - scale * step
            if objective(candidate) <= old - 1e-4 * scale * float(gradient @ step):
                theta = candidate
                break
            scale *= 0.5
        else:
            if float(np.max(np.abs(gradient))) < 1e-6:
                converged = True
                break
            raise ValueError("Roster model optimization did not converge")
    if not converged:
        gradient = x.T @ (_sigmoid(x @ theta) - y) + penalty * theta
        converged = float(np.max(np.abs(gradient))) < 1e-6
    if not converged:
        raise ValueError("Roster model optimization did not converge in 100 iterations")
    probabilities = _sigmoid(x @ theta)
    hessian = x.T @ ((probabilities * (1.0 - probabilities))[:, None] * x) + np.diag(penalty)
    return theta, np.linalg.inv(hessian), iteration + 1

def fit_model(matches, *, replacement_player_ids, regularization=1.0):
    """Fit training data only; every explicit replacement ID must appear there."""
    if isinstance(regularization, bool) or not isinstance(regularization, (int, float)):
        raise ValueError("regularization must be a finite positive number")
    if not math.isfinite(regularization) or regularization <= 0:
        raise ValueError("regularization must be a finite positive number")
    if isinstance(replacement_player_ids, (str, bytes)):
        raise ValueError("replacement_player_ids must be a collection of exact IDs")
    pool = list(replacement_player_ids)
    if not pool or not all(isinstance(pid, str) for pid in pool):
        raise ValueError("Supply an explicit nonempty replacement-player pool")
    replacement_ids = sorted(set(pool))
    matches, cohort, excluded = _eligible(matches)
    player_ids = sorted({p["player_id"] for m in matches for p in m["players"]})
    missing = set(replacement_ids) - set(player_ids)
    if missing:
        raise ValueError("Replacement IDs absent from training data: " + ", ".join(sorted(missing)))
    x = np.vstack([_design(m, player_ids, cohort)[0] for m in matches])
    y = np.array([_label(m) for m in matches], dtype=float)
    theta, covariance, iterations = _fit_matrix(x, y, float(regularization))
    signatures = defaultdict(list)
    for i, pid in enumerate(player_ids):
        signatures[tuple(x[:, i + 1].tolist())].append(pid)
    inseparable = [ids for ids in signatures.values() if len(ids) > 1]
    rank = int(np.linalg.matrix_rank(x[:, 1:]))
    augmented_rank = int(np.linalg.matrix_rank(x))
    additional_confounding = augmented_rank < len(player_ids)
    flags = []
    if additional_confounding:
        flags.append("individual_player_effects_not_identified_by_roster_design")
    if inseparable:
        flags.append("inseparable_teammates_receive_regularization_driven_allocations")
    if len(set(y.tolist())) < 2:
        flags.append("only_one_outcome_class_in_training")
    if len(matches) < 30:
        flags.append("small_training_sample")
    return {
        "schema_version": 1, "kind": "experimental_regularized_roster_logistic", "cohort": cohort,
        "player_ids": player_ids, "intercept": float(theta[0]),
        "coefficients": {pid: float(theta[i + 1]) for i, pid in enumerate(player_ids)},
        "covariance": covariance.tolist(), "regularization": float(regularization), "intercept_regularization": 1e-6,
        "replacement": {"definition": "equal_weight_mean_of_explicit_training_player_pool",
                        "player_ids": replacement_ids,
                        "coefficient": float(np.mean([theta[player_ids.index(pid) + 1] for pid in replacement_ids]))},
        "training": {"matches": len(matches), "match_ids": sorted(m["match_id"] for m in matches),
                     "blue_win_rate": float(np.mean(y)), "excluded": excluded,
                     "player_appearances": {pid: int(np.count_nonzero(x[:, i + 1])) for i, pid in enumerate(player_ids)},
                     "player_design_rank": rank, "augmented_design_rank": augmented_rank,
                     "maximum_identifiable_rank": max(0, len(player_ids) - 1),
                     "additional_roster_confounding": additional_confounding,
                     "inseparable_teammate_groups": inseparable, "optimizer_iterations": iterations},
        "flags": flags, "limitations": list(LIMITATIONS),
    }

def _model_arrays(model):
    if not isinstance(model, Mapping) or model.get("schema_version") != 1 or model.get("kind") != "experimental_regularized_roster_logistic":
        raise ValueError("Unsupported roster model schema")
    try:
        player_ids = model["player_ids"]
        if not isinstance(player_ids, list) or not player_ids or len(set(player_ids)) != len(player_ids):
            raise ValueError("Invalid model player IDs")
        theta = np.array([model["intercept"]] + [model["coefficients"][pid] for pid in player_ids], dtype=float)
        covariance = np.array(model["covariance"], dtype=float)
        replacement = model["replacement"]["player_ids"]
        if not replacement or set(replacement) - set(player_ids):
            raise ValueError("Invalid model replacement pool")
    except (KeyError, TypeError) as exc:
        raise ValueError("Invalid model parameters") from exc
    if covariance.shape != (len(theta), len(theta)) or not np.all(np.isfinite(theta)) or not np.all(np.isfinite(covariance)):
        raise ValueError("Invalid model parameters")
    if not np.allclose(covariance, covariance.T, rtol=1e-8, atol=1e-10) or np.any(np.diag(covariance) < 0):
        raise ValueError("Invalid model covariance")
    return player_ids, theta, covariance

def predict_match(model, match):
    """Only pregame roster and playlist are read; scores and stats are ignored."""
    player_ids, theta, covariance = _model_arrays(model)
    vector, unknown, _, _ = _design(match, player_ids, model["cohort"])
    logit = float(vector @ theta)
    probability, interval = float(_sigmoid(logit)), None
    if not unknown:
        sigma = math.sqrt(max(0.0, float(vector @ covariance @ vector)))
        interval = {"lower": float(_sigmoid(logit - 1.96 * sigma)), "upper": float(_sigmoid(logit + 1.96 * sigma)),
                    "level": 0.95, "method": "approximate_penalized_likelihood"}
    return {"blue_win_probability": probability, "orange_win_probability": 1.0 - probability,
            "unknown_players": unknown,
            "unknown_player_policy": "zero_regularized_log_odds_coefficient; uncertainty_unquantified",
            "interval": interval, "experimental": True}

def _impact_components(model, match, player_id):
    player_ids, theta, covariance = _model_arrays(model)
    vector, unknown, blue, orange = _design(match, player_ids, model["cohort"])
    if player_id not in blue and player_id not in orange:
        raise ValueError("Player is not in this match roster")
    if player_id not in player_ids:
        raise ValueError("Cannot estimate replacement impact for an unseen player")
    sign = 1.0 if player_id in blue else -1.0
    replacement = model["replacement"]["player_ids"]
    substitute = vector.copy()
    substitute[player_ids.index(player_id) + 1] -= sign
    for pid in replacement:
        substitute[player_ids.index(pid) + 1] += sign / len(replacement)
    actual_blue, substitute_blue = float(_sigmoid(vector @ theta)), float(_sigmoid(substitute @ theta))
    actual = actual_blue if sign > 0 else 1.0 - actual_blue
    counterfactual = substitute_blue if sign > 0 else 1.0 - substitute_blue
    gradient = sign * (actual_blue * (1.0 - actual_blue) * vector
                       - substitute_blue * (1.0 - substitute_blue) * substitute)
    return actual, counterfactual, gradient, covariance, unknown

def player_impact(model, match, player_id):
    actual, replacement, gradient, covariance, unknown = _impact_components(model, match, player_id)
    difference, interval = actual - replacement, None
    if not unknown:
        half_width = 1.96 * math.sqrt(max(0.0, float(gradient @ covariance @ gradient)))
        interval = {"lower": max(-1.0, difference - half_width), "upper": min(1.0, difference + half_width),
                    "level": 0.95, "method": "approximate_penalized_delta_method"}
    return {"player_id": player_id, "match_id": match.get("match_id"), "team_win_probability": actual,
            "replacement_team_win_probability": replacement, "expected_wins_above_replacement": difference,
            "interval": interval, "unknown_players": unknown,
            "replacement_player_ids": list(model["replacement"]["player_ids"]),
            "roster_contrast_identified_from_design": not model["training"]["additional_roster_confounding"],
            "experimental": True, "limitations": list(LIMITATIONS)}

def expected_wins_above_replacement(model, matches, player_id):
    """Sum schedule counterfactuals, propagating shared parameter uncertainty."""
    matches = _unique_matches(matches)
    actual_sum, replacement_sum = 0.0, 0.0
    _, _, covariance = _model_arrays(model)
    gradient_sum = np.zeros(covariance.shape[0])
    unknown, match_ids = set(), []
    for match in matches:
        _roster(match, model["cohort"])
        if not any(p["player_id"] == player_id for p in match["players"]):
            continue
        actual, replacement, gradient, _, unseen = _impact_components(model, match, player_id)
        actual_sum += actual
        replacement_sum += replacement
        gradient_sum += gradient
        unknown.update(unseen)
        match_ids.append(match["match_id"])
    if not match_ids:
        raise ValueError("Player has no matches in the supplied schedule")
    difference, interval = actual_sum - replacement_sum, None
    if not unknown:
        half_width = 1.96 * math.sqrt(max(0.0, float(gradient_sum @ covariance @ gradient_sum)))
        interval = {"lower": max(-float(len(match_ids)), difference - half_width),
                    "upper": min(float(len(match_ids)), difference + half_width), "level": 0.95,
                    "method": "approximate_penalized_delta_method_shared_parameters"}
    return {"player_id": player_id, "matches": len(match_ids), "match_ids": match_ids,
            "expected_wins": actual_sum, "replacement_expected_wins": replacement_sum,
            "expected_wins_above_replacement": difference, "interval": interval,
            "unknown_players": sorted(unknown), "replacement_player_ids": list(model["replacement"]["player_ids"]),
            "roster_contrast_identified_from_design": not model["training"]["additional_roster_confounding"],
            "experimental": True, "limitations": list(LIMITATIONS)}

def _date(value):
    if not isinstance(value, str):
        raise ValueError("Chronological evaluation requires played_at ISO timestamps")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid played_at timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("played_at must include a UTC offset")
    return parsed

def _metrics(labels, probabilities):
    clipped, bins = np.clip(probabilities, 1e-12, 1.0 - 1e-12), []
    for index in range(10):
        mask = (probabilities >= index / 10.0) & ((probabilities < (index + 1) / 10.0) if index < 9 else (probabilities <= 1.0))
        count = int(np.sum(mask))
        if count:
            bins.append({"lower": index / 10.0, "upper": (index + 1) / 10.0, "matches": count,
                         "mean_predicted": float(np.mean(probabilities[mask])),
                         "observed_blue_win_rate": float(np.mean(labels[mask]))})
    ece = sum(b["matches"] * abs(b["mean_predicted"] - b["observed_blue_win_rate"]) for b in bins) / len(labels)
    return {"matches": len(labels),
            "log_loss": float(-np.mean(labels * np.log(clipped) + (1.0 - labels) * np.log(1.0 - clipped))),
            "brier_score": float(np.mean((probabilities - labels) ** 2)),
            "accuracy": float(np.mean(np.where(probabilities == 0.5, 0.5, (probabilities > 0.5) == labels))),
            "accuracy_tie_policy": "half_credit_at_exactly_0.5",
            "expected_calibration_error": float(ece), "calibration_bins": bins}

def evaluate_chronologically(matches, *, replacement_player_ids, train_fraction=0.8,
                             group_ids=None, regularization=1.0):
    """Untouched future holdout; purge entire series crossing the time boundary."""
    if isinstance(train_fraction, bool) or not isinstance(train_fraction, (int, float)) or not 0 < train_fraction < 1:
        raise ValueError("train_fraction must be strictly between zero and one")
    if group_ids is not None and not isinstance(group_ids, Mapping):
        raise ValueError("group_ids must map match IDs to series IDs")
    matches, _, excluded = _eligible(matches)
    if len(matches) < 2:
        raise ValueError("Chronological evaluation needs at least two eligible matches")
    matches.sort(key=lambda m: (_date(m.get("played_at")), m["match_id"]))
    cutoff_index = max(1, min(len(matches) - 1, int(len(matches) * train_fraction)))
    cutoff = _date(matches[cutoff_index]["played_at"])
    groups, series_covered = defaultdict(list), 0
    for match in matches:
        supplied = (group_ids or {}).get(match["match_id"])
        series = supplied if supplied is not None else match.get("series_id")
        if series is not None:
            if not isinstance(series, str) or not series.strip():
                raise ValueError("Series/group identifiers must be nonempty strings")
            group = ("series", series)
            series_covered += 1
        else:
            group = ("match", match["match_id"])
        groups[group].append(match)
    train, test, purged = [], [], []
    for rows in groups.values():
        dates = [_date(m["played_at"]) for m in rows]
        if max(dates) < cutoff:
            train.extend(rows)
        elif min(dates) >= cutoff:
            test.extend(rows)
        else:
            purged.extend(rows)
    if not train or not test:
        raise ValueError("No nonoverlapping train/test split remains after series-boundary purge")
    model = fit_model(train, replacement_player_ids=replacement_player_ids, regularization=regularization)
    test.sort(key=lambda m: (_date(m["played_at"]), m["match_id"]))
    predictions = [predict_match(model, m) for m in test]
    probabilities = np.array([p["blue_win_probability"] for p in predictions])
    labels = np.array([_label(m) for m in test])
    rate, warnings = model["training"]["blue_win_rate"], list(LIMITATIONS)
    if series_covered != len(matches):
        warnings.append("Some series IDs are missing: independence is enforced only for supplied groups and unique matches.")
    if len(test) < 30:
        warnings.append("Small holdout; metrics and calibration bins are noisy.")
    return {"experimental": True, "model": model,
            "split": {"method": "chronological_group_purge", "cutoff": cutoff.isoformat(),
                      "train_matches": len(train), "test_matches": len(test),
                      "train_match_ids": sorted(m["match_id"] for m in train),
                      "test_match_ids": sorted(m["match_id"] for m in test),
                      "purged_match_ids": sorted(m["match_id"] for m in purged),
                      "series_coverage": series_covered / len(matches),
                      "all_matches_have_series_ids": series_covered == len(matches), "excluded": excluded},
            "metrics": _metrics(labels, probabilities),
            "baselines": {"coin_flip": _metrics(labels, np.full(len(test), 0.5)),
                          "training_blue_win_rate": _metrics(labels, np.full(len(test), rate))},
            "holdout_unknown_player_matches": sum(bool(p["unknown_players"]) for p in predictions),
            "predictions": [{"match_id": m["match_id"], "blue_win_probability": p["blue_win_probability"],
                             "observed_blue_win": int(label), "unknown_players": p["unknown_players"]}
                            for m, p, label in zip(test, predictions, labels)],
            "limitations": warnings}
