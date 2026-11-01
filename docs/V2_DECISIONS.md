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

## Second pass: 2.5 s horizon, decision points only

The momentum problem above was addressed in two ways, both now cheap thanks to the decoded
replay cache (`python -m rlreplay decode`, see V2_METHODOLOGY.md): the full 14.7M-row
dataset rebuilds in 23 s.

1. **Horizon 2.5 s** with a 1000 uu displacement threshold for retreat/advance.
2. **Decision points only.** Each row carries the player's closing speed on the ball and a
   `decision_point` flag that is false when the outcome is already decided by current motion
   (time to ball under half the horizon, or already moving along the pitch fast enough to
   cross the threshold). Fits use only decision points: 27% of frames, with a balanced label
   mix (challenge 24%, retreat 23%, advance 21%, hold 25%, big pad 7%).

**Tabular policy** (LightGBM, 3M-row sample of the 4.0M decision points, same date split,
test on 765,563 rows, 1,513 rounds):

| | log loss | accuracy |
| --- | --- | --- |
| class prior | 1.542 | 0.246 |
| decision-point policy | 0.918 | 0.617 |

Per-class recall: retreat 0.76, challenge 0.69, advance 0.63, hold 0.52, big pad 0.22. The
top features are now position and context rather than own velocity: own y (0.13), ball
velocity along the pitch, own boost, closing speed, distance to ball, whether the player is
ahead of the ball and closer than their teammate, nearest opponent's distance to the ball,
time since kickoff. That is what a decision model should look at.

**The reliability result reverses.** On the 1 s continuation task, per-player decision
log-likelihood had split-half reliability 0.52. On decision points it is **0.08**. The earlier
"predictability is a stable trait" was a momentum artefact: players differ consistently in
how they move, not in how predictable their choices are once motion is controlled for. Two
things that do persist:

| per-player statistic (held-out games, 76 players) | split-half reliability |
| --- | --- |
| challenge rate at decision points | 0.41 |
| challenge rate minus the policy's expected rate | 0.30 |
| decision log-likelihood | 0.08 |

**Crossed with value added** (`reports/v2-decisions-h25/cross_value.json`, 3,064 held-out
player-games, 75 players): decision log-likelihood is unrelated to value added (player-level
r = −0.05) and to xG (−0.14); challenging more than the policy expects correlates weakly and
positively with value added (+0.16 at player level, +0.08 per game). So at GC+ the signal is
not "plays like the population" but "challenges more often than the population would in the
same spots", and even that is a modest, noisy effect. Any stronger claim needs more than 76
players.

**Sequence model on the GPU** (`rlreplay/seqpolicy.py`, run in `.venv-torch` on the Radeon AI
PRO R9700). Input: the last 25 steps (5 s) of the full 2v2 state; model: linear embedding,
two temporal convolutions, a GRU, and a linear head (hidden 128); bf16 autocast, AdamW,
early stopping on the validation window. 1,200 replays, 985k / 200k / 402k decision-point
windows for train / validation / test; 15 s per epoch, best at epoch 3 of 7.

| | log loss | accuracy |
| --- | --- | --- |
| class prior | 1.540 | 0.247 |
| sequence policy | 0.975 | 0.596 |

Per-class recall: retreat 0.75, challenge 0.66, advance 0.56, hold 0.54, big pad 0.19.
The decision-point task is genuinely harder than the 1 s continuation task (majority class
25% rather than 44%), and the model still removes about 37% of the prior's log loss. It
overfits quickly (validation loss rises from epoch 4), so regularisation and more replays
are the obvious next levers; the GPU makes both cheap.

## Reproducing

```powershell
.\.venv\Scripts\python -m rlreplay decisions-build --raw data/raw/2v2 --out data/frames/decisions_2v2
.\.venv\Scripts\python -m rlreplay decisions-fit --frames data/frames/decisions_2v2 --table data/features/player_game_2v2.parquet --out reports/v2-decisions --train-end 2025-04-01 --valid-end 2025-07-01 --sample-rows 3000000
```

The stream-walking build takes about 25 minutes on 12 cores; the fit about 13 minutes on CPU.
The cached path replaces the first command:

```powershell
.\.venv\Scripts\python -m rlreplay decode --raw data/raw/2v2 --out data/decoded/2v2
.\.venv\Scripts\python -m rlreplay decisions-build --decoded data/decoded/2v2 --out data/frames/decisions_2v2_h25 --horizon 2.5
.\.venv\Scripts\python -m rlreplay decisions-fit --frames data/frames/decisions_2v2_h25 --table data/features/player_game_2v2.parquet --out reports/v2-decisions-h25 --train-end 2025-04-01 --valid-end 2025-07-01 --decision-points-only
.\.venv-torch\Scripts\python -m rlreplay.seqpolicy --frames data/frames/decisions_2v2_h25 --table data/features/player_game_2v2.parquet --out reports/v2-seqpolicy --train-end 2025-04-01 --valid-end 2025-07-01 --replays 1200
```
