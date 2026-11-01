Touches: train 376761 / valid 80116 / test 156359 (fit < 2025-04-01, early-stop to 2025-07-01, test after).

## touch_xg (target `scored`, 39 features, 297 rounds)

| model | log loss | AUC | Brier |
|---|---|---|---|
| constant (base rate 0.0285) | 0.1269 | 0.5000 | 0.0270 |
| LightGBM | 0.0447 | 0.9808 | 0.0126 |
| LightGBM, shots only (n=14537) | 0.3560 | 0.8781 | 0.1121 |

Calibration (test deciles, mean predicted vs actual):

| decile | n | predicted | actual |
|---|---|---|---|
| 0 | 15636 | 0.0001 | 0.0001 |
| 1 | 15636 | 0.0002 | 0.0001 |
| 2 | 15636 | 0.0003 | 0.0003 |
| 3 | 15636 | 0.0004 | 0.0003 |
| 4 | 15636 | 0.0006 | 0.0004 |
| 5 | 15636 | 0.0009 | 0.0010 |
| 6 | 15636 | 0.0013 | 0.0013 |
| 7 | 15636 | 0.0022 | 0.0024 |
| 8 | 15636 | 0.0062 | 0.0066 |
| 9 | 15635 | 0.2666 | 0.2652 |

Top features by gain: shot (0.32), proj_x (0.14), ball_y (0.07), proj_t (0.07), goal_angle_width (0.05), proj_z (0.04), opp_min_dist_goal (0.03), dist_goal (0.03), post_toward_goal (0.03), post_angle_off_goal (0.02)

## threat (target `team_goal_within_10s`, 29 features, 333 rounds)

| model | log loss | AUC | Brier |
|---|---|---|---|
| constant (base rate 0.1249) | 0.3724 | 0.5000 | 0.1077 |
| LightGBM | 0.3293 | 0.7124 | 0.0948 |

Calibration (test deciles, mean predicted vs actual):

| decile | n | predicted | actual |
|---|---|---|---|
| 0 | 15636 | 0.0346 | 0.0404 |
| 1 | 15636 | 0.0531 | 0.0527 |
| 2 | 15636 | 0.0646 | 0.0696 |
| 3 | 15636 | 0.0749 | 0.0763 |
| 4 | 15636 | 0.0855 | 0.0872 |
| 5 | 15636 | 0.0977 | 0.0901 |
| 6 | 15636 | 0.1139 | 0.1105 |
| 7 | 15636 | 0.1382 | 0.1337 |
| 8 | 15636 | 0.1830 | 0.1757 |
| 9 | 15635 | 0.3984 | 0.3912 |

Top features by gain: goal_angle_width (0.23), opps_behind_ball (0.09), opp_min_dist_ball (0.07), dist_goal (0.06), opp_min_dist_goal (0.05), me_boost (0.04), me_speed (0.04), ball_z (0.03), seconds_remaining (0.03), pre_vy (0.03)

## threat_against (target `opp_goal_within_10s`, 29 features, 157 rounds)

| model | log loss | AUC | Brier |
|---|---|---|---|
| constant (base rate 0.0715) | 0.2487 | 0.5000 | 0.0635 |
| LightGBM | 0.2336 | 0.6607 | 0.0603 |

Calibration (test deciles, mean predicted vs actual):

| decile | n | predicted | actual |
|---|---|---|---|
| 0 | 15636 | 0.0267 | 0.0258 |
| 1 | 15636 | 0.0375 | 0.0370 |
| 2 | 15636 | 0.0438 | 0.0407 |
| 3 | 15636 | 0.0495 | 0.0473 |
| 4 | 15636 | 0.0554 | 0.0550 |
| 5 | 15636 | 0.0621 | 0.0629 |
| 6 | 15636 | 0.0705 | 0.0691 |
| 7 | 15636 | 0.0809 | 0.0775 |
| 8 | 15636 | 0.0965 | 0.0888 |
| 9 | 15635 | 0.1889 | 0.1767 |

Top features by gain: dist_own_goal (0.19), opp_min_dist_ball (0.10), ball_y (0.06), me_boost (0.05), seconds_remaining (0.05), mate_min_dist_own_goal (0.04), me_dist_own_goal (0.04), opp_nearest_boost (0.04), mate_min_dist (0.04), ball_z (0.04)

## Per player-game checks

11938 player-games joined to ballchasing. corr(xG, goals) = 0.801; corr(our goals, ballchasing goals) = 0.993; corr(our shots, ballchasing shots) = 0.775.
Means per player-game: shots 4.69 (ballchasing 4.43), goals 1.45 (ballchasing 1.47), xG 1.44.

## Is any of it skill?

95 players with 30+ games. Variance of z = (goals - xG) / sqrt(sum p(1-p)) is 0.82 (1.00 means pure binomial noise); 0% of players have |z| > 2 (5% expected by chance).

Split-half reliability (odd vs even games, per player):

| statistic | players | correlation |
|---|---|---|
| goals_above_xg_per_game | 102 | 0.030 |
| xg_per_game | 102 | 0.632 |
| xg_per_shot | 100 | 0.087 |
| mean_threat | 102 | 0.561 |
| shots_per_game | 102 | 0.568 |

## Players with 30+ games, by goals above xG

| player | games | shots | goals | xG | goals - xG | z | xG/shot |
|---|---|---|---|---|---|---|---|
| Mat. | 149 | 739 | 235 | 219.1 | +15.9 | +1.57 | 0.297 |
| tawk | 71 | 362 | 128 | 115.2 | +12.8 | +1.81 | 0.318 |
| Kehvn | 164 | 664 | 202 | 189.7 | +12.3 | +1.31 | 0.286 |
| Fairy Peak! | 61 | 285 | 95 | 83.4 | +11.6 | +1.89 | 0.293 |
| v.ops1 | 130 | 666 | 231 | 219.5 | +11.5 | +1.20 | 0.330 |
| delta | 74 | 347 | 133 | 122.1 | +10.9 | +1.50 | 0.352 |
| velocity | 111 | 554 | 185 | 174.9 | +10.1 | +1.16 | 0.316 |
| Abdullah | 76 | 433 | 151 | 140.9 | +10.1 | +1.29 | 0.325 |
| aZapatos | 51 | 264 | 78 | 68.5 | +9.5 | +1.60 | 0.260 |
| bora | 76 | 418 | 133 | 124.5 | +8.5 | +1.15 | 0.298 |
| ... | | | | | | | |
| GarrettG | 93 | 467 | 147 | 158.4 | -11.4 | -1.40 | 0.339 |
| Lj | 71 | 393 | 132 | 144.3 | -12.3 | -1.59 | 0.367 |
| Arsenal | 92 | 473 | 125 | 137.5 | -12.5 | -1.60 | 0.291 |
| yANXNZ^^ | 101 | 611 | 181 | 194.0 | -13.0 | -1.41 | 0.317 |
| swazz | 144 | 649 | 190 | 203.9 | -13.9 | -1.50 | 0.314 |