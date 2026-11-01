"""Command line for the v2 replay pipeline.

    python -m rlreplay fetch-meta  --mode 2v2 --out data/meta
    python -m rlreplay meta-table  --meta data/meta/2v2.metadata.json --out data/features/player_game_2v2.parquet
    python -m rlreplay select      --table data/features/player_game_2v2.parquet --n-players 120 --max-replays 2500 --out data/subset_2v2.json
    python -m rlreplay download    --subset data/subset_2v2.json --mode 2v2 --raw data/raw/2v2
    python -m rlreplay extract     --raw data/raw/2v2 --out data/features/frame_2v2.parquet
    python -m rlreplay validate    --table ... --frames ... --out reports/v2-frames/extractor_validation.md
    python -m rlreplay experiment  --table ... --frames ... --out reports/ --train-end 2025-01-01 --valid-end 2025-07-01
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path


def cmd_fetch_meta(args):
    from .kaggle import download, metadata_name
    out = Path(args.out) / f"{args.mode}.metadata.json"
    download(metadata_name(args.mode), out)
    print(out)


def cmd_meta_table(args):
    from .meta import load_player_games
    table = load_player_games(args.meta)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    table.write_parquet(args.out)
    print(f"{args.out}: {table.height} player-games, {table['replay_id'].n_unique()} matches")


def cmd_select(args):
    """Pick focal players and take *every* game of theirs inside a date window.

    Players are added most-active-first until the replay budget is reached, so each focal
    player's rolling history inside the window is fully covered by frame features.
    """
    from datetime import datetime, timezone
    import polars as pl
    table = pl.read_parquet(args.table)
    if args.playlist:
        table = table.filter(pl.col("playlist") == args.playlist)
    if args.since:
        table = table.filter(pl.col("played_at") >= datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc))
    counts = table.group_by("player_id").len().sort("len", "player_id", descending=[True, False])
    focal, chosen = [], set()
    for player_id, n in counts.iter_rows():
        if len(focal) >= args.n_players or n < args.min_games:
            break
        if n > args.max_games:
            continue  # one extreme uploader would otherwise consume the whole budget
        games = set(table.filter(pl.col("player_id") == player_id)["replay_id"].to_list())
        if len(chosen | games) > args.max_replays:
            continue
        focal.append(player_id)
        chosen |= games
    games = table.filter(pl.col("replay_id").is_in(list(chosen))).select("replay_id", "played_at").unique().sort("played_at")
    payload = {"playlist": args.playlist, "since": args.since, "n_players": len(focal), "focal_players": focal,
               "games_per_focal_player": {p: int(table.filter(pl.col("player_id") == p).height) for p in focal},
               "replay_ids": games["replay_id"].to_list(),
               "span": [str(games["played_at"].min()), str(games["played_at"].max())]}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, indent=1))
    print(f"{len(payload['replay_ids'])} replays for {len(focal)} players, {payload['span'][0]} .. {payload['span'][1]}")


def cmd_download(args):
    from .kaggle import download_replays
    subset = json.loads(Path(args.subset).read_text())
    ids = subset["replay_ids"]
    if args.limit:
        ids = ids[:args.limit]
    started = time.time()

    def progress(done, total, failed):
        print(f"{done}/{total} downloaded, {failed} failed, {time.time() - started:.0f}s", flush=True)

    result = download_replays(args.mode, ids, args.raw, workers=args.workers, progress=progress)
    report = Path(args.raw) / "download_report.json"
    report.write_text(json.dumps({"ok": len(result["ok"]), "failed": result["failed"]}, indent=1))
    print(f"ok={len(result['ok'])} failed={len(result['failed'])} -> {report}")


def _extract_one(path: str):
    from .frames import extract
    from .header import header_summary, parse
    replay_id = Path(path).stem
    try:
        parsed = parse(path, network=True)
        rows = extract(parsed, replay_id=replay_id)
        head = header_summary(parsed)
        for row in rows:
            row["header_team0_score"] = head["team0_score"]
            row["header_team1_score"] = head["team1_score"]
            row["header_num_frames"] = head["num_frames"]
        return replay_id, rows, None
    except Exception as exc:  # noqa: BLE001 - keep going, record the failure
        return replay_id, [], f"{type(exc).__name__}: {str(exc)[:200]}"


def cmd_extract(args):
    import polars as pl
    paths = sorted(str(p) for p in Path(args.raw).glob("*.replay"))
    if args.limit:
        paths = paths[:args.limit]
    rows, failures = [], {}
    started = time.time()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(_extract_one, p) for p in paths]
        for n, future in enumerate(as_completed(futures), 1):
            replay_id, got, error = future.result()
            if error:
                failures[replay_id] = error
            rows.extend(got)
            if n % 100 == 0:
                print(f"{n}/{len(paths)} parsed, {len(failures)} failed, {time.time() - started:.0f}s", flush=True)
    frame = pl.DataFrame(rows, infer_schema_length=None)
    # Join key used by the metadata table: "<platform>:<id>" with ballchasing's platform names.
    platform_map = {"Steam": "steam", "Epic": "epic", "PlayStation": "ps4", "PsyNet": "psynet",
                    "Xbox": "xbox", "Switch": "switch"}
    frame = frame.with_columns(
        (pl.col("frame_platform").replace_strict(platform_map, default=pl.col("frame_platform").str.to_lowercase())
         + ":" + pl.col("frame_uid")).alias("player_id"))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(args.out)
    Path(args.out).with_suffix(".failures.json").write_text(json.dumps(failures, indent=1))
    print(f"{args.out}: {frame.height} player-games from {len(paths) - len(failures)} replays, "
          f"{len(failures)} failures, {time.time() - started:.0f}s")


def cmd_validate(args):
    import polars as pl
    from .validate import compare, to_markdown
    report, coverage = compare(pl.read_parquet(args.table), pl.read_parquet(args.frames))
    text = to_markdown(report, coverage)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        report.write_csv(Path(args.out).with_suffix(".csv"))
    print(text)


def cmd_experiment(args):
    import polars as pl
    from . import features, model
    table = pl.read_parquet(args.table)
    frames = pl.read_parquet(args.frames) if args.frames else None
    built = features.build_table(table, frames, window=args.window)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    built.write_parquet(out / "model_table.parquet")
    sets = model.feature_sets(args.window)
    report = {"window": args.window, "experiments": {}}
    # A. Summary-stat lift over rank, on every player-game (no frame features needed).
    summary_sets = {k: v for k, v in sets.items() if "frame" not in k}
    report["experiments"]["all_games_won"] = model.evaluate_sets(
        built, summary_sets, target="won", train_end=args.train_end, valid_end=args.valid_end,
        min_prev_games=args.min_prev)
    report["experiments"]["all_games_rank_change"] = model.evaluate_sets(
        built, summary_sets, target="rank_change", train_end=args.train_end, valid_end=args.valid_end,
        min_prev_games=args.min_prev)
    # B. Frame-feature lift, restricted to rows whose recent history has frame coverage.
    if frames is not None:
        covered = features.rolling_frame_coverage(built, window=args.window)
        covered = covered.filter(pl.col("n_prev_frame_games") >= args.min_frame_prev)
        report["frame_rows"] = covered.height
        report["experiments"]["frame_subset_won"] = model.evaluate_sets(
            covered, sets, target="won", train_end=args.train_end, valid_end=args.valid_end,
            min_prev_games=args.min_prev)
    (out / "results.json").write_text(json.dumps(report, indent=1, default=str))
    lines = []
    for name, res in report["experiments"].items():
        lines.append(f"## {name}\n\n{model.summarize(res)}\n")
        for mname, entry in res.get("models", {}).items():
            if "importances" in entry:
                top = ", ".join(f"{i['feature']} ({i['gain_share']:.2f})" for i in entry["importances"][:6])
                lines.append(f"- {mname}: {entry['n_features']} features, {entry['best_iteration']} rounds; top gain: {top}")
        lines.append("")
    (out / "results.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="rlreplay")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("fetch-meta"); p.add_argument("--mode", default="2v2"); p.add_argument("--out", default="data/meta")
    p.set_defaults(func=cmd_fetch_meta)
    p = sub.add_parser("meta-table"); p.add_argument("--meta", required=True); p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_meta_table)
    p = sub.add_parser("select"); p.add_argument("--table", required=True); p.add_argument("--out", required=True)
    p.add_argument("--playlist", default="ranked-doubles"); p.add_argument("--n-players", type=int, default=120)
    p.add_argument("--max-replays", type=int, default=2500); p.add_argument("--since", default=None)
    p.add_argument("--min-games", type=int, default=30); p.add_argument("--max-games", type=int, default=400)
    p.set_defaults(func=cmd_select)
    p = sub.add_parser("download"); p.add_argument("--subset", required=True); p.add_argument("--mode", default="2v2")
    p.add_argument("--raw", required=True); p.add_argument("--workers", type=int, default=8)
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=cmd_download)
    p = sub.add_parser("extract"); p.add_argument("--raw", required=True); p.add_argument("--out", required=True)
    p.add_argument("--workers", type=int, default=None); p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=cmd_extract)
    p = sub.add_parser("validate"); p.add_argument("--table", required=True); p.add_argument("--frames", required=True)
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_validate)
    p = sub.add_parser("experiment"); p.add_argument("--table", required=True); p.add_argument("--frames", default=None)
    p.add_argument("--out", required=True); p.add_argument("--window", type=int, default=10)
    p.add_argument("--train-end", required=True); p.add_argument("--valid-end", required=True)
    p.add_argument("--min-prev", type=int, default=5); p.add_argument("--min-frame-prev", type=int, default=5)
    p.set_defaults(func=cmd_experiment)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
