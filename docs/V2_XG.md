# v2: expected goals from replay touch events

This is the first event-level model built on the replay pipeline described in
[V2_METHODOLOGY.md](V2_METHODOLOGY.md). Where the game-level experiment in
[V2_RESULTS.md](V2_RESULTS.md) asked "do stats predict the next win", this asks a question
the replay stream can actually answer densely: **how likely was this touch to become a goal?**

## Data

Ranked doubles, the same 2,989 GC+ replays (27 focal players and everyone they played
with or against, 2024-07 to 2026-04). Every ball touch during active play is one row:

- **A touch** is a ball-velocity change above 500 uu/s with a car within 350 uu of the ball;
  that car is the toucher. (The same rule validated at 0.93 to 0.98 correlation against
  ballchasing's movement and positioning stats in the frame-feature work.)
- **A shot** is a touch after which the ball's ballistic path, with gravity and at most one
  ground bounce, crosses the opponent goal plane inside the posts and under the bar within
  3 s. Walls and spin are ignored. Per player-game this correlates 0.78 with ballchasing's
  shot count and flags 89% of scoring touches; ballchasing's definition is not public, and
  it counts slightly more shots than this rule does.
- **A goal** is attributed exactly as the replay header credits it: the scoring touch is the
  credited scorer's last touch before the goal. Touches by the conceding team between that
  touch and the goal are labelled `failed_save`. In a 60-replay check, 98% of header goals
  had a scorer touch within 10 s; the remainder are own goals or header quirks and carry no
  scoring touch.

All geometry is in the touching team's attacking frame (opponent goal at y = +5120), so one
model serves both teams.

## Features

State before the touch (both models): ball position and incoming velocity; distance and
angle width to the opponent goal; distance to own goal; toucher height, speed and boost;
teammate count, nearest-teammate distance, teammates ahead of the ball, deepest teammate's
distance to own goal; opponent count, nearest opponent to the ball, nearest opponent to
their goal, opponents between ball and goal, opponents behind the ball, nearest opponent's
boost; clock, time since kickoff, score differential, team size.

Touch result (touch xG only): ball velocity after the touch, its component toward goal and
angle off goal, where and when the projected path crosses the goal plane, the shot flag.

## Models

- **touch xG**: P(this touch is the scoring touch). LightGBM, 63 leaves, 200 minimum rows
  per leaf, learning rate 0.05, early-stopped on the validation window. Summed per player it
  is that player's xG; evaluated on all touches and on shots only.
- **threat**: P(the touching team scores within 10 s) from the pre-touch state alone. It
  values the situation a player was in, independent of what they did with it.
- **threat against**: P(the opposing team scores within 10 s) from the same state; held-out
  AUC 0.66, calibrated. Used only to form the net value for value added.

Split by replay date, never by player or at random: fit on games before 2025-04-01, early
stop on 2025-04-01 to 2025-07-01, score on games from 2025-07-01. Reported metrics are log
loss and Brier against the training base rate, AUC, and a decile calibration table.

## Outputs

`reports/v2-xg/`: fitted boosters, `touches_scored.parquet` (every touch with both
predictions and an `out_of_time` flag for the held-out period), `player_game_xg.parquet`
(touches, shots, goals, xG, goals above xG per player-game) and `player_xg.parquet`.

## Results

613,236 touches from 2,989 replays (about 205 per match). Base rates: 9.1% of touches are
shots, 2.8% are scoring touches, 12.4% are followed by a goal for the touching team within
10 s. Split: 376,761 train / 80,116 validation / 156,359 test touches. Full tables:
`reports/v2-xg/results.md`.

### Checks against ballchasing (per player-game, 11,938 rows)

| quantity | ours | ballchasing | correlation | exact |
| --- | --- | --- | --- | --- |
| goals | 1.45 | 1.47 | 0.993 | 98% |
| shots | 4.69 | 4.43 | 0.775 | 28% |

Goals are attributed essentially as ballchasing attributes them. Shots are a heuristic that
agrees in volume and ranks players similarly but disagrees on individual touches.

### Model quality (held-out games from 2025-07-01)

| model | rows | log loss | base-rate log loss | AUC | Brier |
| --- | --- | --- | --- | --- | --- |
| touch xG, all touches | 156,359 | 0.045 | 0.127 | 0.980 | 0.0125 |
| touch xG, shots only | 14,537 | 0.355 | — | 0.880 | 0.112 |
| threat (pre-touch state) | 156,359 | 0.329 | 0.372 | 0.713 | 0.095 |

Calibration is tight for both: in every test decile the mean prediction is within 0.005 of
the observed rate (touch xG top decile 0.265 predicted vs 0.265 observed; threat top decile
0.398 vs 0.393). Per player-game, summed xG correlates 0.82 with actual goals.

Touch xG is driven by the ball's path after the touch (shot flag, projected crossing point
and time) and then by geometry: distance to the ball from the goal line, angle width, and
whether an opponent is near their goal. Threat, which cannot see the touch result, is
driven by angle width, opponents behind the ball, nearest opponent to the ball, distance to
goal, and the toucher's boost and speed.

### Is any of it skill?

This is the part that matters for "advanced analytics", and the answer is sharp.

| per-player statistic | split-half correlation (odd vs even games, 102 players) |
| --- | --- |
| xG per game | 0.62 |
| shots per game | 0.57 |
| mean threat per touch | 0.55 |
| xG per shot | 0.06 |
| goals above xG per game | 0.04 |

Among 95 players with 30 or more games, the variance of z = (goals − xG) / √Σp(1−p) is
0.80 against 1.00 expected from pure binomial noise, and 1% of players have |z| > 2
against 5% expected by chance. **Finishing above expectation does not persist from one
half of a player's games to the other at this level.** The leaderboard of "goals above xG"
(+15 to −14 over 100 to 160 games) is what coin flips look like at these volumes, and it
should not be read as a ranking of finishers.

What does persist is volume and situation: how often a player shoots, how much xG they
generate per game, and how dangerous the positions are where they touch the ball. Those
are stable player traits with reliability comparable to shooting volume in other sports,
and they are the ones worth building on.

### What this gives you that the summary stats did not

- A per-touch probability of scoring, calibrated, that turns "shots" into "shot quality"
  and lets any single shot or save be rated.
- A pre-touch danger value for any game state, usable to credit the touch that moved the
  ball from a 5% situation to a 40% one rather than only the one that scored.
- Evidence that, at GC+, finishing skill is not separable from noise in a season's worth of
  games, so player evaluation should rest on chance creation and positioning, not
  conversion.

## Value added per touch and save quality

Built on the three fitted models (`rlreplay/value.py`, outputs in `reports/v2-value/`).

**Value added.** Each touch gets a net value V = threat − threat-against (the touching team's
10 s scoring chance minus the opponent's). The value after the touch is +1 or −1 if a goal
follows before anyone else touches the ball, 0 if a kickoff intervenes, and otherwise the
next touch's V seen from this team's side. Value added is the difference. It is the
possession-value idea from football and basketball applied to a touch sequence: a player is
credited for moving the game toward the opponent's goal and away from their own, whether or
not they are the one who scores.

- Mean value added per touch is +0.0006, as it should be for a zero-sum quantity.
- Summed per team-game it correlates 0.745 with the team's goal differential, so the chain
  accounting hangs together (a sign check caught an unsigned-integer wraparound in an
  earlier version of this check; the models were never wrong, the test was).

**Save quality.** Every shot is "faced". A shot that does not score and is touched by a
defender within 3 s is a save credited to that defender; a scored shot is charged to the
defender who touched it on the way in, or else to the defender nearest their goal. Goals
prevented above expected (GSAx) is Σ xG faced − goals conceded. 42,020 shots faced, save
rate 0.669, mean xG faced 0.293.

**Does any of it persist?**

| per-player statistic | split-half correlation |
| --- | --- |
| mean V at own touches (situation quality) | 0.58 |
| touches per game | 0.58 |
| shots faced per game | 0.41 |
| value added per game | 0.38 |
| value added per touch | 0.37 |
| GSAx per game | 0.14 |
| GSAx per shot faced | 0.10 |
| xG faced per shot | −0.06 |

Value added is a real but noisy player trait: about 0.38 reliability over roughly 50 games
per half, which is far better than finishing (0.04) and worse than volume measures (0.58).
A season of a player's games gives a usable estimate; a dozen games does not. The leaders
add about +0.5 goal-equivalents per game over an average touch sequence, the trailers about
−0.4; given the reliability those extremes should be shrunk substantially toward zero.

Save quality is mostly noise at this volume. Across 105 defenders with 100 or more shots
faced, the variance of GSAx/√Σp(1−p) is 1.49, so there is more spread than pure chance,
but split-half reliability of 0.10 to 0.14 means a player's GSAx in one half of their games
barely predicts the other half. Shots faced per game (0.41) persists because it reflects
role: who plays deeper. xG faced per shot does not persist at all, which says shot
difficulty is not something the last defender controls in 2v2.

## Shrinkage: turning the tables into estimates

Every per-player number above is a raw mean, and the reliabilities show that for some
statistics a raw mean over 100 games is mostly noise. `rlreplay/shrink.py` applies the
standard empirical-Bayes correction: a player's mean is pulled toward the population mean by
τ² / (τ² + σ²/n), where σ² is the pooled game-to-game variance and τ² the between-player
variance estimated by the method of moments. A statistic with no real between-player spread
gets τ² = 0 and every player lands on the population mean; a stable trait is barely moved.
The priors fitted on the GC+ population are stored in `rlreplay/reference/gcplus_2v2.json`
alongside the percentile tables, so any replay folder can be shrunk toward GC+ rather than
toward itself.

## Scoring your own replays

```powershell
.\.venv\Scripts\python -m rlreplay score --raw C:\path\to\replays --out reports\me --focus "your name"
```

parses every `.replay` in the folder, computes frame features and touch events, applies the
three fitted boosters, derives value added and shots faced, shrinks each per-game metric
toward the GC+ priors given how many games were seen, and writes `report.md` /
`report.json` with raw, shrunk, GC+ median and GC+ percentile per metric. On 17 games of a
2022 player, for example, raw goals above xG of +0.12 per game shrinks to +0.02 (percentile
51), while touches per game, supersonic share and pad pickups keep most of their signal.
That is the intended behaviour: the report refuses to call anyone a finisher on 17 games.

The percentiles compare against GC+ players with 30 or more games, and the models are
calibrated on GC+ play, so at other ranks the absolute xG and threat values are relative
indicators rather than literal probabilities.

## What these numbers can and cannot support

- **xG per shot and goals above xG are descriptive.** A player who beats their xG over 100
  games finished better than the model expected from the situations and ball trajectories
  the model saw. It does not say they will keep doing so; shooting over-performance is
  noisy in every sport where it has been measured.
- **Threat credits situations, not causes.** A high mean threat means a player touched the
  ball in dangerous positions; it does not separate creating those positions from being
  handed them.
- **Population.** GC+ ranked doubles. Shot and save quality at Diamond are different
  distributions; the model should be re-fitted, not reused, there.
- **Definitions are heuristics.** Touch attribution, the shot rule and the 10 s threat
  window were fixed before fitting and checked against ballchasing where a counterpart
  exists, but none is ground truth.

## Reproducing

```powershell
.\.venv\Scripts\python -m rlreplay touches --raw data/raw/2v2 --out data/features/touches_2v2.parquet
.\.venv\Scripts\python -m rlreplay xg --touches data/features/touches_2v2.parquet --table data/features/player_game_2v2.parquet --out reports/v2-xg --train-end 2025-04-01 --valid-end 2025-07-01
.\.venv\Scripts\python -m rlreplay value --touches-scored reports/v2-xg/touches_scored.parquet --out reports/v2-value
```
