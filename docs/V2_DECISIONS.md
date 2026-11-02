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

## Third pass: one policy across ranks, and the GC counterfactual

`decisions-fit` now joins each row's own rank points (`me_rank`) and the match's mean rank
(`game_rank`, from the player-game table) as model inputs, so one policy spans the ladder,
and scores every held-out decision twice: at the player's own rank and with both rank inputs
set to 80 (GC2), the same state as if a GC player were in it. Per player-game that gives the
**GC gap**, the extra surprise (nats) of the player's actual choices under the GC-conditioned
policy, and two disagreement rates: retreating where the GC policy rates challenge most
likely, and challenging where it would not. First fit on Kaggle GC+ plus PastorKurt's 2,898
Diamond-level games (`reports/v2-decisions-ranked`, 8.3M decision points available, 4.0M
sampled, same date split, test on 578k rows):

| | log loss | accuracy |
| --- | --- | --- |
| class prior | 1.545 | 0.275 |
| rank-conditioned decision-point policy | 0.919 | 0.617 |

Same accuracy as the GC+-only fit; the rank inputs do not change what drives the model
(own y, ball velocity, boost, closing speed), they let it shift the mix by rank. By rank bin
on held-out rows, the GC-conditioned policy would challenge more often than Diamond players
do in the same states (0.215 against an actual 0.198 at rank points 48 to 59), and less
often than GC players do (0.230 against 0.242); the GC gap at Diamond is 0.023 nats against
0.012 for GC players scored as GC.

| per-player statistic (held-out games, 78 players) | split-half reliability |
| --- | --- |
| GC gap | 0.50 |
| challenge where the GC policy would not | 0.35 |
| challenge rate minus own-rank policy | 0.33 |
| decision log-likelihood (own rank) | 0.16 |
| retreat where the GC policy challenges | 0.03 |

Read the GC-gap reliability with care: between players it is partly rank itself, since a
Diamond player has a larger gap by construction, so the within-band version is what matters
and needs the band corpus. "Retreat where GC challenges" does not persist at all (0.03) and
happens on under 2% of decision points at every rank, so the headline that lower-rank
players are too passive is not supported here; what persists is challenging where the GC
policy would not (0.35), which at Diamond is 6% of decision points against 8% for GC
players themselves. The Diamond side of this fit is 233 held-out player-games, 109 players,
dominated by PastorKurt and PastorCamerius (121 and 120 games); their GC gaps are 0.042 and
0.027 nats with challenge rates 0.181 and 0.201 against an own-rank policy expectation of
0.187 and 0.189. The band crawl (`scripts/crawl_bands.ps1`, `crawl_band_players.ps1`) is what
turns this into a statement about Diamond players in general rather than two of them.

## Fourth pass: the band corpus as the Diamond population

Same model, same split, with the 8,642 band-crawl games added (`reports/v2-decisions-bands`:
20.7M decision points available, 4.0M sampled). Every band game is played after 2025-07, so
the training rows are unchanged and the band corpus is pure held-out test (2.6M rows, 292
players with 10+ held-out games against 78 before). Log loss 0.941 against the prior's 1.541,
accuracy 0.606; the test mix is now mostly Diamond to GC rather than GC+, so the numbers are
not comparable to the third pass and the point of this fit is what follows.

| player rank bin | held-out rows | challenge rate | GC policy would | GC gap (nats) |
| --- | --- | --- | --- | --- |
| 48-59 (Diamond) | 391,862 | 0.201 | 0.222 | 0.014 |
| 60-71 (Champion) | 592,428 | 0.210 | 0.227 | 0.009 |
| 72-83 (GC) | 394,806 | 0.226 | 0.230 | 0.001 |
| 84-95 (SSL) | 217,179 | 0.240 | 0.233 | 0.007 |

Monotone: in the same states, the GC-conditioned policy challenges more often than Diamond and
Champion players do, matches GC, and challenges less than SSL. The third pass's reading that
lower ranks are not too passive was made on two Diamond accounts; with 115 Diamond players it
does not hold. Split-half reliability of the per-player statistics, overall and **within
band** (players whose median own rank falls in the band, 10+ held-out games), which answers
the rank-confound caveat from the third pass:

| statistic | all (264) | Diamond (110) | Champion (40) | GC (114) |
| --- | --- | --- | --- | --- |
| GC gap | 0.62 | 0.63 | 0.63 | 0.46 |
| challenge where the GC policy would not | 0.49 | 0.46 | 0.48 | 0.31 |
| retreat where the GC policy challenges | 0.24 | 0.35 | 0.14 | 0.16 |
| challenge rate minus own-rank policy | 0.46 (292) | | | |

The GC gap is a persistent trait of a Diamond player within Diamond (0.63), not a restatement
of rank. Over-challenging persists at every band; under-challenging persists at Diamond (0.35)
and is noise at GC, which is what you would expect if passivity is a lower-rank habit.

PastorKurt among the 115 Diamond players: GC gap 0.044 nats, 95th percentile (median 0.009,
interquartile 0.002 to 0.022); challenge rate 0.177 against an own-rank policy expectation of
0.187, 17th percentile; retreats where the GC policy challenges 2.3% of decision points, 94th
percentile; challenges where it would not 5.8%, 35th percentile. So his decisions sit further
from GC behaviour than almost any Diamond player in the corpus, and the direction is passive:
he challenges less than Diamond peers and far less than the GC policy. PastorCamerius: gap
0.026 (83rd), challenge rate 0.204 (53rd), under-challenge 43rd, over-challenge 59th.

What this is not: a value statement. The policy says what GC players do, not that doing it
wins; the win-probability counterfactual in the handoff notes is the next model.

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

Third pass (rank-conditioned, needs the per-source decision datasets and tables):

```powershell
.\.venv\Scripts\python -m rlreplay decode --raw data/raw/pastorkurt --out data/decoded/pastorkurt
.\.venv\Scripts\python -m rlreplay decisions-build --decoded data/decoded/pastorkurt --out data/frames/decisions_pastorkurt_h25 --horizon 2.5
.\.venv\Scripts\python -m rlreplay decisions-fit --frames data/frames/decisions_2v2_h25 --frames data/frames/decisions_pastorkurt_h25 --table data/features/player_game_2v2.parquet --table data/features/player_game_pastorkurt.parquet --out reports/v2-decisions-ranked --train-end 2025-04-01 --valid-end 2025-07-01 --decision-points-only --sample-rows 4000000
```

Fourth pass (band corpus; stage one is `meta-table`, `decode`, `extract`, `touches`, `decisions-build`
per band folder):

```powershell
.\.venv\Scripts\python -m rlreplay decisions-fit --frames data/frames/decisions_2v2_h25,data/frames/decisions_pastorkurt_h25,data/frames/decisions_2v2_diamond_h25,data/frames/decisions_2v2_champion_h25,data/frames/decisions_2v2_gc_h25,data/frames/decisions_2v2_diamond_players_h25 --table data/features/player_game_2v2.parquet,data/features/player_game_pastorkurt.parquet,data/features/player_game_2v2_diamond.parquet,data/features/player_game_2v2_champion.parquet,data/features/player_game_2v2_gc.parquet,data/features/player_game_2v2_diamond_players.parquet --out reports/v2-decisions-bands --train-end 2025-04-01 --valid-end 2025-07-01 --decision-points-only --sample-rows 4000000
```
