# v2 results: do frame features beat summary stats?

Method, feature definitions and caveats are in [V2_METHODOLOGY.md](V2_METHODOLOGY.md).
Everything below is ranked doubles from the GC+ dataset, evaluated with a strict time split:
fit on games before 2025-01-01, early-stop on 2025-01-01 to 2025-07-01, score once on games
from 2025-07-01 onward. Raw numbers: `reports/v2-all-games/results.json` and
`reports/v2-frames/results.json`.

## Experiment A: all games, summary stats vs rank and history

198,632 player-games, 49,658 matches. Rows with at least 5 prior games and known own and
opponent rank. Target: did this player's team win this game.

Rows: 62,245 train / 33,979 validation / 21,595 test (test period 2025-07-01 to 2026-04-27).

| feature set | log loss | AUC | Brier | accuracy |
| --- | --- | --- | --- | --- |
| constant prior | 0.6931 | 0.500 | 0.2500 | 0.504 |
| rank | 0.6926 | 0.504 | 0.2497 | 0.505 |
| rank + history | 0.6891 | 0.557 | 0.2479 | 0.540 |
| rank + history + summary (86 features) | 0.6883 | 0.558 | 0.2476 | 0.540 |

Top gain shares: rank set is dominated by `rank_diff` (0.49); the history set by
`prev_win_rate` (0.38), `n_prev_games` (0.22), `days_since_prev` (0.17); in the summary set
no single stat column exceeds 0.03 (`r10_core_shots`, `r10_movement_percent_high_air`,
`r10_boost_percent_boost_75_100` lead).

What it says:

- **Rank alone carries no signal here.** Log loss equals the constant model. That is the
  population, not the method: roughly 60% of player-games are Supersonic Legend and most of
  the rest are GC3, so team rank differences are tiny and noisy.
- **Recent history is the real baseline.** Running win rate, games seen so far and days since
  the previous game move AUC to about 0.56. Part of this is a dataset artefact: the
  collection prioritises high-scoring players, so a player with many games in it is a
  player the curator's score liked.
- **Summary stats add almost nothing on top of history.** Eighty rolling ballchasing
  columns improve log loss by about 0.001 and AUC by less than 0.001. Per-game stats at this
  level mostly re-describe the win rate they came with.

Secondary target, rank change over 30 days (zero 85% of the time):

| feature set | MAE | RMSE | R² |
| --- | --- | --- | --- |
| constant (train mean) | 0.209 | 0.561 | -0.014 |
| rank | 0.254 | 0.455 | 0.333 |
| rank + history | 0.212 | 0.451 | 0.345 |
| rank + history + summary | 0.211 | 0.453 | 0.338 |

Rank points themselves explain the variance that is explainable (mean reversion toward the
SSL ceiling); history and stats add nothing meaningful. This target is not useful in a
GC+-only sample and would need Diamond/Champion data.

## Experiment B: frame features on the focal-player subset

2,989 replays (3.2 GB) covering every ranked-doubles game since 2024-07-01 of 27 focal
players who each had 99 to 130 games in that window. Frame features were extracted for all
11,960 player-games in those replays (0 parse failures, 250 s on 12 cores). Rows used for
modelling: player-games whose previous 10 games include at least 5 with frame features,
which in practice means the focal players' own games.

Rows: 1,160 train / 964 validation / 736 test (test period 2025-07-01 to 2026-03-01).

| feature set | log loss | AUC | Brier | accuracy |
| --- | --- | --- | --- | --- |
| constant prior | 0.6952 | 0.500 | 0.2510 | 0.450 |
| rank | 0.6952 | 0.500 | 0.2510 | 0.450 |
| rank + history | 0.6914 | 0.547 | 0.2491 | 0.549 |
| rank + history + summary | 0.6946 | 0.516 | 0.2507 | 0.469 |
| rank + history + frame (31 features) | 0.6944 | 0.512 | 0.2506 | 0.514 |
| rank + history + summary + frame | 0.6936 | 0.530 | 0.2502 | 0.512 |

Sensitivity check with a later split (fit before 2025-05-01, early-stop to 2025-08-01, test
after; 1,850 / 369 / 641 rows; `reports/v2-frames-alt-split/`):

| feature set | log loss | AUC |
| --- | --- | --- |
| constant prior | 0.6948 | 0.500 |
| rank + history | 0.6927 | 0.524 |
| rank + history + summary | 0.6942 | 0.534 |
| rank + history + frame | 0.6946 | 0.485 |
| rank + history + summary + frame | 0.6947 | 0.499 |

What it says:

- **No detectable lift from frame features.** Adding the 22 rolling frame features to
  rank and history makes log loss worse by 0.003, the same as adding the summary stats.
  Every wider set early-stops after 5 to 34 rounds: with about a thousand training rows the
  model cannot separate signal from noise in 30 to 100 extra columns, and the best it can do
  is approximately ignore them.
- **This is an underpowered test, not a strong null.** With 736 test rows the standard error
  of an AUC near 0.5 is about 0.02, so differences under roughly 0.04 AUC are not
  distinguishable. Experiment A, with 20 times the data, puts the summary-stat lift at under
  0.001 AUC; a frame-feature lift of that size would need a similar number of frame-covered
  rows to measure, which is 30 to 50 thousand more replays.
- **Which frame features the model reached for**, for what it is worth at this sample size:
  rolling small-pad pickups, touch rate, kickoff time to ball and distance to nearest
  teammate carried the most gain in the frame-only set. Challenge and kickoff win rates did
  not rank.
- **The "similar players" step was not run.** The plan made k-NN conditional on a real lift;
  there is none to build on.

### Bottom line

At the top of the ladder, a player's recent results predict their next result modestly
(AUC about 0.55), and neither ballchasing's summary stats nor these frame features add
measurable information on top of that. The pipeline is sound and validated, so the honest
next experiment is a different population (Diamond/Champion, where rank still varies) and
more frame-covered games per player, not more features.

## Validation of the frame extractor

Before any modelling, the extractor was checked against ballchasing's own per-player stats
for all 11,956 player-games in the downloaded replays (`python -m rlreplay validate`, output
in `reports/v2-frames/extractor_validation.md`). Pearson correlation between the frame-derived
value and ballchasing's value for the same player-game:

| frame feature | ballchasing stat | corr |
| --- | --- | --- |
| avg_speed | movement.avg_speed | 0.94 |
| supersonic_frac | movement.percent_supersonic_speed | 0.98 |
| ground_frac | movement.percent_ground | 0.96 |
| avg_boost | boost.avg_amount | 0.82 |
| zero_boost_frac | boost.percent_zero_boost | 0.96 |
| behind_ball_frac | positioning.percent_behind_ball | 0.97 |
| avg_dist_ball | positioning.avg_distance_to_ball | 0.94 |
| avg_dist_mate | positioning.avg_distance_to_mates | 0.97 |
| big_pads / small_pads | boost.count_collected_big / small | 0.92 / 0.99 |
| demos_inflicted / demos_taken | demo.inflicted / taken | 0.98 / 0.99 (97 to 98% exact) |
| active_time | gameplay_duration | 0.995 |

Means agree within a few percent; remaining differences come from definitions (ballchasing's
ground threshold, boost sampling, and a 13 s gap in active time from how goal replays are
clipped). Replay attribute names changed twice inside this dataset's date range (boost became
a struct in mid-2024, demolitions gained an extended form in December 2024); the extractor
handles both, and this check is what caught the second. Touch, challenge and kickoff features have no ballchasing counterpart; they were
sanity-checked (kickoffs per match equals rounds, two players' touches sum to plausible
totals) but not independently validated.

## Caveats

- GC+ only, self-selected uploads, curated toward the strongest players. None of this
  transfers to "will this Diamond rank up".
- The focal-player subset is 27 players; frame-set conclusions rest on their games.
- LightGBM hyperparameters are fixed and untuned, identical across feature sets, so the
  comparison is between information content, not model capacity.
- Rank at game time comes from ballchasing and is missing for about 30% of player-games;
  those rows are excluded from all models.
