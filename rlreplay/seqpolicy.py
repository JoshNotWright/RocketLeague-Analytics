"""Sequence policy: predict the next action from a window of past frames, on the GPU.

Runs under the ROCm PyTorch environment (``.venv-torch``; see docs/GPU.md), which does not
have LightGBM, so this module imports only numpy, polars and torch.

    .venv-torch\\Scripts\\python -m rlreplay.seqpolicy --frames data/frames/decisions_2v2_h25 \
        --table data/features/player_game_2v2.parquet --out reports/v2-seqpolicy \
        --train-end 2025-04-01 --valid-end 2025-07-01 --replays 1200

Design: for every (replay, player) the 5 Hz rows form a sequence; the model sees the last
``window`` steps (default 25 = 5 s) of the full 2v2 state and predicts the action label at
the final step. Loss and metrics are computed on decision points only, exactly like the
tabular policy with ``--decision-points-only``, so the two are comparable on the same rows.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import polars as pl

from .decisions import ACTIONS, STATE_FEATURES

RUNTIME_FEATURES = [f for f in STATE_FEATURES if f not in ("time_to_ball",)]  # time_to_ball has nulls; closing_speed covers it


def _utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def load_sequences(frames_dir: Path, table: pl.DataFrame, *, replays: int, seed: int):
    """Return (X [steps, F] float32, meta DataFrame with one row per step, sequence boundaries)."""
    files = sorted(Path(frames_dir).glob("*.parquet"))
    rng = np.random.default_rng(seed)
    if replays and replays < len(files):
        files = list(rng.choice(np.array(files, dtype=object), size=replays, replace=False))
    dates = table.select("replay_id", "played_at").unique(subset=["replay_id"])
    df = (pl.concat([pl.read_parquet(f) for f in files], how="vertical_relaxed")
            .join(dates, on="replay_id", how="inner")
            .sort("replay_id", "player_id", "time"))
    feats = [f for f in RUNTIME_FEATURES if f in df.columns]
    # Null and NaN are different things in polars: boosts and time-since-kickoff can be NaN.
    X = df.select([pl.col(f).cast(pl.Float32).fill_nan(0.0).fill_null(0.0) for f in feats]).to_numpy().astype(np.float32)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    meta = df.select("replay_id", "player_id", "player_name", "time", "played_at", "action_id", "decision_point")
    # sequence id per (replay, player) and position within the sequence
    key = (pl.col("replay_id") + "|" + pl.col("player_id"))
    seq = df.select(key.alias("k")).with_columns(pl.col("k").rle_id().alias("seq")).get_column("seq").to_numpy()
    pos = np.zeros(len(seq), dtype=np.int64)
    starts = np.r_[0, np.flatnonzero(np.diff(seq)) + 1]
    for s, e in zip(starts, np.r_[starts[1:], len(seq)]):
        pos[s:e] = np.arange(e - s)
    return X, meta, feats, seq, pos


def build_model(n_features: int, hidden: int = 128, n_classes: int = len(ACTIONS)):
    import torch
    from torch import nn

    class Policy(nn.Module):
        def __init__(self):
            super().__init__()
            self.inp = nn.Sequential(nn.Linear(n_features, hidden), nn.GELU())
            self.conv = nn.Sequential(nn.Conv1d(hidden, hidden, 5, padding=2), nn.GELU(),
                                      nn.Conv1d(hidden, hidden, 5, padding=2), nn.GELU())
            self.gru = nn.GRU(hidden, hidden, batch_first=True)
            self.head = nn.Sequential(nn.LayerNorm(hidden), nn.Linear(hidden, n_classes))

        def forward(self, x):            # x: [B, W, F]
            h = self.inp(x)
            h = h + self.conv(h.transpose(1, 2)).transpose(1, 2)
            out, _ = self.gru(h)
            return self.head(out[:, -1])

    return Policy()


def run(args):
    import torch
    from torch import nn

    device = "cuda" if torch.cuda.is_available() else "cpu"
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    table = pl.read_parquet(args.table)
    t0 = time.time()
    X, meta, feats, seq, pos = load_sequences(Path(args.frames), table, replays=args.replays, seed=args.seed)
    print(f"loaded {len(X)} steps, {len(feats)} features, {int(seq.max()) + 1} sequences in {time.time() - t0:.0f}s", flush=True)
    a, b = _utc(args.train_end), _utc(args.valid_end)
    played = meta["played_at"].to_numpy()
    split = np.where(played < np.datetime64(a.replace(tzinfo=None)), 0, np.where(played < np.datetime64(b.replace(tzinfo=None)), 1, 2))
    y = meta["action_id"].to_numpy().astype(np.int64)
    dp = meta["decision_point"].to_numpy()
    W = args.window
    # targets: decision points with at least W-1 past steps in the same sequence
    valid_target = dp & (pos >= W - 1)
    idx = {s: np.flatnonzero(valid_target & (split == s)) for s in (0, 1, 2)}
    print({k: int(len(v)) for k, v in idx.items()}, flush=True)
    mean, std = X[split == 0].mean(0), X[split == 0].std(0) + 1e-6
    Xn = torch.from_numpy(np.clip((X - mean) / std, -10.0, 10.0).astype(np.float32))
    if not np.isfinite(Xn.numpy()).all():
        raise SystemExit("non-finite features after standardisation")
    Xg = Xn.to(device=device, dtype=torch.float16 if device == "cuda" else torch.float32)
    yg = torch.from_numpy(y).to(device)
    offsets = torch.arange(-(W - 1), 1, device=device)

    def batch(ids):
        ids = torch.as_tensor(ids, device=device)
        gather = (ids[:, None] + offsets[None, :])           # [B, W]
        return Xg[gather].float(), yg[ids]

    model = build_model(len(feats), hidden=args.hidden).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    loss_fn = nn.CrossEntropyLoss()
    prior = np.bincount(y[idx[0]], minlength=len(ACTIONS)) / len(idx[0])

    def evaluate(ids):
        model.eval()
        total, correct, n = 0.0, 0, 0
        probs = []
        with torch.no_grad(), torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=device == "cuda"):
            for s in range(0, len(ids), args.batch * 4):
                xb, yb = batch(ids[s:s + args.batch * 4])
                logits = model(xb).float()
                total += loss_fn(logits, yb).item() * len(yb)
                correct += (logits.argmax(1) == yb).sum().item()
                n += len(yb)
                probs.append(torch.softmax(logits, 1).cpu().numpy())
        model.train()
        return total / n, correct / n, np.concatenate(probs) if probs else None

    best, best_state, bad = math.inf, None, 0
    steps_per_epoch = max(1, len(idx[0]) // args.batch)
    rng = np.random.default_rng(args.seed)
    history = []
    for epoch in range(args.epochs):
        perm = rng.permutation(idx[0])
        t1 = time.time()
        for s in range(steps_per_epoch):
            xb, yb = batch(perm[s * args.batch:(s + 1) * args.batch])
            with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=device == "cuda"):
                loss = loss_fn(model(xb).float(), yb)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
        vl, va, _ = evaluate(idx[1])
        history.append({"epoch": epoch, "valid_logloss": vl, "valid_acc": va, "seconds": time.time() - t1})
        print(f"epoch {epoch}: valid log loss {vl:.4f} acc {va:.3f} ({time.time() - t1:.0f}s)", flush=True)
        if not math.isfinite(vl):
            raise SystemExit("validation loss is not finite; check features / learning rate")
        if vl < best - 1e-4:
            best, bad = vl, 0
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
            if bad >= args.patience:
                break
    model.load_state_dict(best_state)
    tl, ta, probs = evaluate(idx[2])
    yt = y[idx[2]]
    prior_ll = float(-np.mean(np.log(np.clip(prior[yt], 1e-9, 1))))
    per_class = {name: {"share": float((yt == k).mean()), "recall": float((probs[yt == k].argmax(1) == k).mean()) if (yt == k).any() else None}
                 for k, name in enumerate(ACTIONS)}
    # per-player decision log-likelihood on test games
    ll = np.log(np.clip(probs[np.arange(len(yt)), yt], 1e-9, 1))
    test_meta = meta[idx[2].tolist()].with_columns(pl.Series("loglik", ll))
    per_game = test_meta.group_by("replay_id", "player_id", "player_name", "played_at").agg(pl.len().alias("frames"), pl.col("loglik").mean().alias("decision_loglik"))
    per_game.write_parquet(out / "player_game_decisions.parquet")
    torch.save({"state_dict": best_state, "features": feats, "mean": mean, "std": std, "window": W, "hidden": args.hidden}, out / "seqpolicy.pt")
    report = {"device": device, "gpu": torch.cuda.get_device_name(0) if device == "cuda" else None,
              "replays": args.replays, "window": W, "hidden": args.hidden, "features": len(feats),
              "rows": {k: int(len(v)) for k, v in idx.items()}, "epochs_run": len(history), "history": history,
              "test_logloss": tl, "test_accuracy": ta, "prior_logloss": prior_ll, "prior_accuracy": float(prior.max()),
              "per_class": per_class}
    (out / "results.json").write_text(json.dumps(report, indent=1, default=str))
    lines = [f"Sequence policy on {report['gpu'] or 'CPU'}: window {W} steps (5 Hz), hidden {args.hidden}, {len(feats)} features, "
             f"{args.replays} replays; decision points train/valid/test {report['rows'][0]}/{report['rows'][1]}/{report['rows'][2]}.",
             f"Test log loss {tl:.4f} vs prior {prior_ll:.4f}; accuracy {ta:.3f} vs majority {prior.max():.3f}; {len(history)} epochs.",
             "", "| action | share | recall |", "|---|---|---|"]
    lines += [f"| {k} | {v['share']:.3f} | {v['recall']:.3f} |" for k, v in per_class.items()]
    (out / "results.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--frames", required=True); p.add_argument("--table", required=True); p.add_argument("--out", required=True)
    p.add_argument("--train-end", required=True); p.add_argument("--valid-end", required=True)
    p.add_argument("--replays", type=int, default=1200); p.add_argument("--window", type=int, default=25)
    p.add_argument("--hidden", type=int, default=128); p.add_argument("--batch", type=int, default=2048)
    p.add_argument("--lr", type=float, default=1e-3); p.add_argument("--epochs", type=int, default=30)
    p.add_argument("--patience", type=int, default=4); p.add_argument("--seed", type=int, default=7)
    run(p.parse_args(argv))


if __name__ == "__main__":
    main()
