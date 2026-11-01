# v2: replay parsing and the frame-feature experiment

This document describes the `rlreplay` package (branch `v2-replay-features`): where the
data comes from, what is computed from replays, how the models are evaluated, and what the
results do and do not mean. Numbers are in [V2_RESULTS.md](V2_RESULTS.md).

## Question

Do frame-level features parsed from replays predict a player's outcomes better than the
summary statistics ballchasing already publishes, once rank and recent history are known?

## Data

**Source.** Rolv-Arild's "High-Level Rocket League Replay Dataset" on Kaggle: 140,328 GC+
matches with raw `.replay` files and a line-delimited `metadata.json` per mode. Each
metadata line is the full ballchasing replay record (per-player core, boost, movement,
positioning and demo stats, plus each player's rank at the time). Files are fetched one at a
time through Kaggle's public per-file endpoint; the full 160 GB archive is never downloaded.

**Scope.** Ranked doubles only (`playlist_id == "ranked-doubles"`; 97% of the 2v2 folder).
3v3 in this dataset is almost entirely private scrims and 1v1 mixes ranked and private play.

**Selection bias (keep in every write-up).** Uploads to ballchasing are self-selected, and
this dataset is further curated toward the highest-scoring players. Ranks are compressed
at the top: roughly 60% of player-games are Supersonic Legend and 30% carry no rank at all.
Nothing here says anything about Diamond or Champion ladders.

## Units and keys

One row per **player-game**. Key: `(replay_id, player_id)` with
`player_id = "<platform>:<id>"` as ballchasing names platforms (`steam`, `epic`, `ps4`,
`xbox`). Frame rows carry the same key, built from the replay's `UniqueId` attribute.

## Frame features (v1, fixed before the parse run)

Extracted by `rlreplay/frames.py` from rrrocket's network stream, accumulated only while
the game state is `Active` (no countdowns, no goal replays). All are per player-game:

| Feature | Definition |
| --- | --- |
| `avg_speed`, `supersonic_frac` | mean car speed; share of time at 2200 uu/s or more |
| `ground_frac` | share of time with car z at or below 50 uu |
| `avg_boost`, `low_boost_frac`, `zero_boost_frac` | mean boost; share of time below 20; share at 0 |
| `behind_ball_frac` | share of time between the ball and own goal (y axis, team-signed) |
| `avg_dist_ball`, `avg_dist_mate` | mean distance to ball; to nearest teammate |
| `last_man_frac`, `last_man_low_boost_frac` | share of time as the team's deepest car; same with boost below 20 |
| `commit_dist_mate` | mean distance to nearest teammate at the moment of this player's touches |
| `touch_rate` | ball touches per active minute. A touch is a ball velocity change above 500 uu/s with this car the nearest within 350 uu |
| `challenge_rate`, `challenge_win_rate` | touches with an opponent within 800 uu of the ball; share after which the ball moves toward the opponent half one second later |
| `kickoffs`, `kickoff_time_to_ball`, `kickoff_win_rate` | first touches of a round; seconds from `Active` to that touch; share where the ball heads to the opponent half one second later |
| `demos_inflicted`, `demos_taken` | from the demolish attribute (attacker and victim cars) |
| `big_pads`, `small_pads` | inferred from boost jumps (the pickup attribute no longer names the collecting car); a jump to 100 from below 88 is a big pad |

The touch, challenge and kickoff definitions are heuristics chosen in advance and not
tuned to the outcome. They were sanity-checked on real replays (kickoff count equals round
count; pad counts match ballchasing's order of magnitude; speeds within physical limits).

## Modelling table

For every player-game at time *t*, features use only that player's games strictly before
*t*:

- **rank**: own rank points (4 divisions per tier, SSL above GC3 div 4), team and opponent
  mean rank, their difference.
- **history**: games seen so far, days since previous game, running win rate, rolling
  10-game mean of win and goal differential.
- **summary**: rolling 10-game means of every ballchasing stat column (79 columns).
- **frame**: rolling 10-game means of the frame features above.

Targets:

- `won`: did this player's team win this game. Primary.
- `rank_change`: rank points at the player's first game 30 or more days later minus now.
  Secondary; in this population it is zero 85% of the time.

The dataset's own per-match `score` is computed from the whole collection and is never
used as a feature.

## Evaluation

Strict time split by `played_at`: fit before `train_end`, early-stop on
`[train_end, valid_end)`, score once on `>= valid_end`. Random splits across a player's games
are never used. All feature sets are fitted on identical rows (players with at least 5
prior games and known own and opponent rank). Models are LightGBM with fixed, untuned
hyperparameters and a fixed seed; the comparison is between feature sets, not models.

Two experiments:

1. **All games.** Rank, history and summary sets on every ranked-doubles player-game. This
   measures how much summary stats add over rank and history with ample data.
2. **Frame subset.** Rows whose previous 10 games include at least 5 with frame features,
   which restricts to the focal players whose replays were downloaded. All five sets are
   compared on these rows, so the frame set's lift is read against the summary set on the
   same rows.

Metrics: log loss (primary), AUC, Brier, accuracy for `won`; MAE, RMSE, R² for `rank_change`.
A constant-prior model is always reported.

## Reproducing

```powershell
.\.venv\Scripts\python -m rlreplay fetch-meta --mode 2v2 --out data/meta
.\.venv\Scripts\python -m rlreplay meta-table --meta data/meta/2v2.metadata.json --out data/features/player_game_2v2.parquet
.\.venv\Scripts\python -m rlreplay select --table data/features/player_game_2v2.parquet --n-players 120 --max-replays 2500 --out data/subset_2v2.json
.\.venv\Scripts\python -m rlreplay download --subset data/subset_2v2.json --mode 2v2 --raw data/raw/2v2
.\.venv\Scripts\python -m rlreplay extract --raw data/raw/2v2 --out data/features/frame_2v2.parquet
.\.venv\Scripts\python -m rlreplay experiment --table data/features/player_game_2v2.parquet --frames data/features/frame_2v2.parquet --out reports/v2 --train-end 2025-01-01 --valid-end 2025-07-01
```

`rrrocket` (nickbabcock, v0.11.8) must be in `tools/` or on `PATH`. No ballchasing key is
needed for any of this.

## Crawling lower ranks

The Kaggle dataset stops at Grand Champion. To extend the pipeline downwards,
`rlreplay crawl` pulls replays straight from ballchasing.com at a chosen rank band and writes
the same layout the loader already reads (`<out>/<name>/<id>.replay` plus a line-delimited
`metadata.json` whose lines `rlreplay.meta.player_rows` consumes unchanged; `score` and
`gameplay_duration` are `null` because they are dataset-level fields):

```powershell
$env:BALLCHASING_API_KEY = "<your key from https://ballchasing.com/upload>"
.\.venv\Scripts\python -m rlreplay crawl --out data/crawl --name 2v2_diamond_champion `
    --playlist ranked-doubles --min-rank diamond-1 --max-rank champion-3 `
    --after 2025-06-01T00:00:00Z --tier free --max-replays 5000
.\.venv\Scripts\python -m rlreplay meta-table --meta data/crawl/2v2_diamond_champion/metadata.json --out data/features/player_game_2v2_dc.parquet
.\.venv\Scripts\python -m rlreplay extract    --raw data/crawl/2v2_diamond_champion --out data/features/frame_2v2_dc.parquet
```

The crawl is resumable: `crawl_state.json` in the output folder keeps the page cursor, the
ids already saved and the newest replay date, so a restart (after Ctrl-C, a crash or a
`--max-replays` stop) continues where it left off and never re-downloads a file that is
already on disk. Once a listing is exhausted, the next run only asks for replays newer
than the last one seen. Replays whose ballchasing status is `pending` or `failed` are
recorded as deferred and retried on a later run.

Quota caveats (from https://ballchasing.com/doc/api, enforced client-side at 90% of the
published numbers, with `--tier` selecting the row that matches the key's Patreon tier):

| Tier (`--tier`) | list replays | get replay | download file |
|---|---|---|---|
| free (default) | 2/s, 500/h | 2/s, 1000/h | 1/s, **200/h** |
| gold | 2/s, 1000/h | 2/s, 2000/h | 2/s, 400/h |
| diamond | 4/s, 2000/h | 4/s, 5000/h | 2/s, 1000/h |
| champion | 8/s | 8/s | 2/s, 2000/h |
| gc | 16/s | 16/s | 2/s |

The file download is the binding constraint: a free key yields about 180 replays per hour
(roughly 4000 per day), so a 5000-replay crawl takes more than a day of wall-clock time and
is meant to be left running. On a paid key the hourly caps go away and the crawl is bound
by network latency instead, because each replay costs two sequential requests; `--workers 4`
fetches several replays of a page at once behind the same pacers, so the published
per-second limits still hold and one slow download no longer stalls the rest (about 100
replays a minute at best on a GC key). A 429 from the server pauses every endpoint together
(honouring `Retry-After` when sent, otherwise doubling from 10 s up to an hour). The key is
read only from `BALLCHASING_API_KEY`, is never written to disk or printed, and a missing key
exits with status 2 before any request is made. Lower-rank ballchasing uploads are a
self-selected sample (players who choose to upload), not a random draw from the rank.

## Decode once, build many (performance)

`python -m rlreplay decode --raw data/raw/2v2 --out data/decoded/2v2` runs rrrocket and the
stream walker once per replay and writes compact arrays (`<id>.npz`, about 1 MB): per frame
the time, active flag, round start, clock, ball position/velocity, and for every player slot
position, velocity and boost, plus the player table, header goals and demolitions. Downstream
products can then be computed with numpy over arrays. The decision dataset uses this path
(`decisions-build --decoded data/decoded/2v2`): 0.16 s per replay against 4 to 6 s for the
stream-walking builder, with a parity test (`tests/test_rlreplay_decode.py`) and a check on
real replays showing identical labels and float32-level agreement on every feature. Changing a
label horizon is now a one-minute rebuild instead of a 25-minute one. The `extract` and
`touches` commands also write one parquet per replay from the workers instead of shipping row
lists back to the parent, and rrrocket output is parsed with orjson when available.

### Rank bands and the band crawl

`scripts/crawl_bands.ps1` crawls Diamond, Champion and GC ranked doubles in monthly windows
(default 200 replays per band per month from 2025-07 for 16 months), so the band corpus is
spread across the whole date range of the time split rather than piled after the test cutoff.
Each band lands in `data/raw/2v2_<band>/` with its own metadata and state, and the script is
resumable. The per-band pipeline is the usual `meta-table`, `extract`, `touches`; `xg` and
`build-reference` accept several `--touches` / `--table` / `--frames` files (repeat the flag
or comma-separate), so one fit covers every band, with each match's mean rank as a feature
(`rlreplay/ranks.py`, see V2_XG.md). `build-reference --band diamond --table ...` keeps only
games whose mean rank falls in that band; `score --reference rlreplay/reference` then compares
each player with the band they actually played in.

### Player cards

`python -m rlreplay card --report reports/<scored folder> --player <name>` renders a percentile
card from a `score` report: `card_<player>.svg` (standalone image, light and dark via
prefers-color-scheme) and `card_<player>.html` (the same SVG with hover detail per bar). Bars are
percentiles against the reference population the player was scored against, shown in the good
direction for lower-is-better metrics; bar opacity follows the metric's split-half reliability,
which `build-reference` now stores per metric, so finishing luck is visibly quieter than movement.
A trend strip shows xG, value added and goals above xG by year. Code: `rlreplay/card.py`.

To pull one player's own uploads instead of a rank band (for scoring your own games), filter
by player id and clear the rank bounds (`--min-rank=` rather than `--min-rank ""`, because
PowerShell drops empty quoted arguments), for example:

```powershell
$env:BALLCHASING_API_KEY = "<your key>"
python -m rlreplay crawl --out data/raw --name pastorkurt --player-id epic:3a1659f2b97140a88b7607f35c4f60e1 --min-rank= --max-rank= --playlist ranked-doubles --tier gc --workers 4
python -m rlreplay score --raw data/raw/pastorkurt --out reports/pastorkurt --focus PastorKurt
```
