"""Time-split evaluation: does a feature set predict a player's next result better than rank alone?

Every model is fitted on games before ``train_end``, early-stopped on games in
[train_end, valid_end), and scored once on games at or after ``valid_end``.
Random splits across a player's games are never used.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import polars as pl

from .features import FRAME_COLUMNS
from .meta import SUMMARY_COLUMNS


def feature_sets(window: int = 10) -> dict[str, list[str]]:
    p = f"r{window}_"
    rank = ["rank_points", "team_rank_mean", "opp_rank_mean", "rank_diff"]
    history = rank + ["n_prev_games", "days_since_prev", "prev_win_rate", p + "won", p + "goal_diff"]
    summary = history + [p + c for c in SUMMARY_COLUMNS]
    frame = summary + [p + c for c in FRAME_COLUMNS]
    frame_only = history + [p + c for c in FRAME_COLUMNS]
    return {"rank": rank, "rank+history": history, "rank+history+summary": summary,
            "rank+history+frame": frame_only, "rank+history+summary+frame": frame}


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def time_split(df: pl.DataFrame, train_end: str, valid_end: str):
    a, b = _utc(train_end), _utc(valid_end)
    return (df.filter(pl.col("played_at") < a),
            df.filter((pl.col("played_at") >= a) & (pl.col("played_at") < b)),
            df.filter(pl.col("played_at") >= b))


def _matrix(df: pl.DataFrame, features: list[str]) -> np.ndarray:
    return df.select([pl.col(f).cast(pl.Float64) for f in features]).to_numpy()


def binary_metrics(y: np.ndarray, p: np.ndarray) -> dict:
    from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return {"n": int(len(y)), "log_loss": float(log_loss(y, p)), "auc": float(roc_auc_score(y, p)),
            "brier": float(brier_score_loss(y, p)), "accuracy": float(np.mean((p >= 0.5) == (y == 1)))}


def regression_metrics(y: np.ndarray, pred: np.ndarray) -> dict:
    err = pred - y
    ss_res = float(np.sum(err ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2)) if len(y) > 1 else float("nan")
    return {"n": int(len(y)), "mae": float(np.mean(np.abs(err))), "rmse": float(np.sqrt(np.mean(err ** 2))),
            "r2": float(1 - ss_res / ss_tot) if ss_tot else float("nan")}


def fit_lightgbm(train, valid, features, target, *, objective, seed=7, params=None):
    import lightgbm as lgb

    base = {"objective": objective, "learning_rate": 0.03, "num_leaves": 31, "min_data_in_leaf": 100,
            "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0,
            "verbose": -1, "seed": seed, "num_threads": 0}
    base.update(params or {})
    dtrain = lgb.Dataset(_matrix(train, features), label=train[target].cast(pl.Float64).to_numpy(),
                         feature_name=features, free_raw_data=False)
    dvalid = lgb.Dataset(_matrix(valid, features), label=valid[target].cast(pl.Float64).to_numpy(),
                         reference=dtrain)
    booster = lgb.train(base, dtrain, num_boost_round=3000, valid_sets=[dvalid],
                        callbacks=[lgb.early_stopping(100, verbose=False)])
    return booster


def importances(booster, features, top=15):
    gain = booster.feature_importance(importance_type="gain")
    total = gain.sum() or 1.0
    order = np.argsort(gain)[::-1][:top]
    return [{"feature": features[i], "gain_share": float(gain[i] / total)} for i in order]


def evaluate_sets(df: pl.DataFrame, sets: dict[str, list[str]], *, target="won", train_end, valid_end,
                  min_prev_games: int = 5, seed: int = 7) -> dict:
    """Fit each feature set on identical rows and score on the held-out time window."""
    needed = sorted({f for fs in sets.values() for f in fs})
    rows = df.filter(pl.col("n_prev_games") >= min_prev_games).filter(pl.col(target).is_not_null())
    # Identical rows for every set: require the context columns, tolerate nulls in rolling features
    # (LightGBM handles missing values natively) but drop rows with no rank information at all.
    rows = rows.filter(pl.col("rank_points").is_not_null() & pl.col("opp_rank_mean").is_not_null())
    train, valid, test = time_split(rows, train_end, valid_end)
    is_binary = rows[target].dtype == pl.Boolean
    results = {"target": target, "train_end": train_end, "valid_end": valid_end,
               "min_prev_games": min_prev_games, "rows": {"train": len(train), "valid": len(valid), "test": len(test)},
               "test_period": [str(test["played_at"].min()), str(test["played_at"].max())], "models": {}}
    if len(train) < 500 or len(valid) < 100 or len(test) < 100:
        results["error"] = "not enough rows for a time split"
        return results
    y_test = test[target].cast(pl.Float64).to_numpy()
    if is_binary:
        prior = float(train[target].cast(pl.Float64).mean())
        results["models"]["constant"] = {"metrics": binary_metrics(y_test, np.full(len(y_test), prior))}
    else:
        mean = float(train[target].cast(pl.Float64).mean())
        results["models"]["constant"] = {"metrics": regression_metrics(y_test, np.full(len(y_test), mean))}
    for name, features in sets.items():
        features = [f for f in features if f in rows.columns]
        booster = fit_lightgbm(train, valid, features, target,
                               objective="binary" if is_binary else "regression", seed=seed)
        pred = booster.predict(_matrix(test, features), num_iteration=booster.best_iteration)
        metrics = binary_metrics(y_test, pred) if is_binary else regression_metrics(y_test, pred)
        results["models"][name] = {"metrics": metrics, "best_iteration": int(booster.best_iteration),
                                   "n_features": len(features), "importances": importances(booster, features)}
    return results


def summarize(results: dict) -> str:
    lines = [f"target={results['target']} rows train/valid/test = "
             f"{results['rows']['train']}/{results['rows']['valid']}/{results['rows']['test']} "
             f"test period {results['test_period'][0]} .. {results['test_period'][1]}"]
    if "error" in results:
        return "\n".join(lines + [results["error"]])
    first = next(iter(results["models"].values()))["metrics"]
    keys = [k for k in first if k != "n"]
    lines.append("| model | " + " | ".join(keys) + " |")
    lines.append("|---|" + "---|" * len(keys))
    for name, entry in results["models"].items():
        lines.append(f"| {name} | " + " | ".join(f"{entry['metrics'][k]:.4f}" for k in keys) + " |")
    return "\n".join(lines)
