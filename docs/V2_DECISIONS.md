# v2: frame-level decision policy (what does a GC+ player do next?)

First frame-level model, built on the same 2,989 ranked-doubles replays as
[V2_XG.md](V2_XG.md). Code: `rlreplay/decisions.py`; outputs: `reports/v2-decisions/`;
frame dataset: `data/frames/decisions_2v2/` (one parquet per replay, 2.2 GB, gitignored).

## Dataset

Active frames subsampled at 5 Hz. One row per (frame, player) in doubles only, with the
full state in that player's attacking frame: ball position and velocity, the player, their
teammate and both opponents (position, velocity, speed, boost, distance to ball, ahead of
ball), clock and time since kickoff. 15.3 million rows; 52 state features.

Label: what the player did over the next 1 s, from their own trajectory only:

| action | rule | share |
| --- | --- | --- |
| challenge | touched the ball within the horizon | 13.7% |
| big_pad | boost rose to full from below 85 without a touch | 4.7% |
| retreat | moved more than 1000 uu toward own goal | 18.2% |
| advance | moved more than 1000 uu toward the opponent goal | 19.6% |
| hold | none of the above (shadowing, waiting, small adjustments) | 43.8% |

## Model and evaluation

LightGBM multiclass (127 leaves, 500 minimum rows per leaf) on a 3M-row hash sample, split by
replay date: fit before 2025-04-01, early stop to 2025-07-01, test on games from 2025-07-01
(762,589 rows). 1,696 rounds.

| | log loss | accuracy |
| --- | --- | --- |
| class prior | 1.407 | 0.439 |
| policy | 0.444 | 0.808 |

Per-class recall: hold 0.85, retreat 0.83, advance 0.81, challenge 0.72, big pad 0.58.

**Read this with the feature importances in hand.** The top feature is the player's own
forward velocity (gain share 0.28), then distance to ball, own y position and speed. A
one-second horizon is short enough that momentum explains a large part of the label: a car
already moving toward the ball will usually touch it. This model is therefore a *short-horizon
behaviour* model, not yet a *decision* model. It is still useful as a baseline and for the
player statistic below, but the next iteration should either lengthen the horizon to 2 to 3 s,
label changes of intent rather than continuations, or condition on a window of past frames
(the sequence-model route, for which the GPU environment in [GPU.md](GPU.md) is now ready).

## Player statistic: decision similarity

For each held-out player-game, the mean log-likelihood of the player's actual actions under
the GC+ policy. Higher means the player did what the population model expected.

| per-player statistic | players (10+ held-out games) | split-half reliability |
| --- | --- | --- |
| decision log-likelihood | 76 | 0.52 |
| challenge rate | 76 | 0.44 |
| challenge rate minus policy's expected rate | 76 | 0.25 |

Predictability under the population policy is a stable player trait (0.52), on a par with
situation quality in the xG work. Whether being *less* predictable is good or bad is not
answered here: the players at the bottom of the table include both strong and weak results,
and the right follow-up is to cross decision similarity with value added (does deviating
from the GC+ norm raise or lower the team's net threat?). That join is straightforward from
`player_game_decisions.parquet` and `player_game_value.parquet` and is left for the next pass.

## Reproducing

```powershell
.\.venv\Scripts\python -m rlreplay decisions-build --raw data/raw/2v2 --out data/frames/decisions_2v2
.\.venv\Scripts\python -m rlreplay decisions-fit --frames data/frames/decisions_2v2 --table data/features/player_game_2v2.parquet --out reports/v2-decisions --train-end 2025-04-01 --valid-end 2025-07-01 --sample-rows 3000000
```

The build takes about 25 minutes on 12 cores; the fit about 13 minutes on CPU.
