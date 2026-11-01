Touches: train 819782 / valid 89723 / test 174992 (fit < 2025-04-01, early-stop to 2025-07-01, test after).

## touch_xg (target `scored`, 40 features, 843 rounds)

| model | log loss | AUC | Brier |
|---|---|---|---|
| constant (base rate 0.0325) | 0.1304 | 0.5000 | 0.0279 |
| LightGBM | 0.0451 | 0.9804 | 0.0126 |
| LightGBM, shots only (n=15933) | 0.3564 | 0.8833 | 0.1122 |

Calibration (test deciles, mean predicted vs actual):

| decile | n | predicted | actual |
|---|---|---|---|
| 0 | 17500 | 0.0001 | 0.0001 |
| 1 | 17500 | 0.0001 | 0.0002 |
| 2 | 17499 | 0.0002 | 0.0005 |
| 3 | 17499 | 0.0003 | 0.0004 |
| 4 | 17499 | 0.0005 | 0.0003 |
| 5 | 17499 | 0.0008 | 0.0009 |
| 6 | 17499 | 0.0013 | 0.0014 |
| 7 | 17499 | 0.0023 | 0.0027 |
| 8 | 17499 | 0.0063 | 0.0069 |
| 9 | 17499 | 0.2755 | 0.2734 |

Top features by gain: shot (0.35), proj_x (0.09), ball_y (0.08), goal_angle_width (0.06), proj_t (0.05), proj_z (0.04), post_angle_off_goal (0.04), opp_min_dist_goal (0.03), post_vy (0.02), post_speed (0.02)

## threat (target `team_goal_within_10s`, 30 features, 379 rounds)

| model | log loss | AUC | Brier |
|---|---|---|---|
| constant (base rate 0.1256) | 0.3736 | 0.5000 | 0.1081 |
| LightGBM | 0.3289 | 0.7166 | 0.0947 |

Calibration (test deciles, mean predicted vs actual):

| decile | n | predicted | actual |
|---|---|---|---|
| 0 | 17500 | 0.0364 | 0.0383 |
| 1 | 17500 | 0.0550 | 0.0563 |
| 2 | 17499 | 0.0664 | 0.0621 |
| 3 | 17499 | 0.0767 | 0.0760 |
| 4 | 17499 | 0.0871 | 0.0858 |
| 5 | 17499 | 0.0992 | 0.0945 |
| 6 | 17499 | 0.1155 | 0.1110 |
| 7 | 17499 | 0.1392 | 0.1341 |
| 8 | 17499 | 0.1828 | 0.1804 |
| 9 | 17499 | 0.3954 | 0.3950 |

Top features by gain: goal_angle_width (0.22), dist_goal (0.12), opps_behind_ball (0.10), opp_min_dist_ball (0.07), opp_min_dist_goal (0.05), me_boost (0.03), me_speed (0.03), dist_own_goal (0.03), ball_y (0.03), pre_vy (0.03)

## threat_against (target `opp_goal_within_10s`, 30 features, 302 rounds)

| model | log loss | AUC | Brier |
|---|---|---|---|
| constant (base rate 0.0720) | 0.2489 | 0.5000 | 0.0635 |
| LightGBM | 0.2334 | 0.6631 | 0.0603 |

Calibration (test deciles, mean predicted vs actual):

| decile | n | predicted | actual |
|---|---|---|---|
| 0 | 17500 | 0.0254 | 0.0253 |
| 1 | 17500 | 0.0378 | 0.0366 |
| 2 | 17499 | 0.0444 | 0.0407 |
| 3 | 17499 | 0.0503 | 0.0476 |
| 4 | 17499 | 0.0564 | 0.0553 |
| 5 | 17499 | 0.0634 | 0.0604 |
| 6 | 17499 | 0.0719 | 0.0697 |
| 7 | 17499 | 0.0825 | 0.0810 |
| 8 | 17499 | 0.0984 | 0.0846 |
| 9 | 17499 | 0.1892 | 0.1801 |

Top features by gain: dist_own_goal (0.17), opp_min_dist_ball (0.07), ball_y (0.05), me_dist_own_goal (0.05), me_boost (0.04), dist_goal (0.04), mate_min_dist_own_goal (0.04), seconds_remaining (0.04), opp_nearest_boost (0.04), mate_min_dist (0.04)

## Per player-game checks

20725 player-games joined to ballchasing. corr(xG, goals) = 0.845; corr(our goals, ballchasing goals) = 0.989; corr(our shots, ballchasing shots) = 0.785.
Means per player-game: shots 4.00 (ballchasing 3.97), goals 1.46 (ballchasing 1.50), xG 1.46.

## Is any of it skill?

98 players with 30+ games. Variance of z = (goals - xG) / sqrt(sum p(1-p)) is 0.88 (1.00 means pure binomial noise); 0% of players have |z| > 2 (5% expected by chance).

Split-half reliability (odd vs even games, per player):

| statistic | players | correlation |
|---|---|---|
| goals_above_xg_per_game | 105 | 0.073 |
| xg_per_game | 105 | 0.638 |
| xg_per_shot | 103 | 0.314 |
| mean_threat | 105 | 0.545 |
| shots_per_game | 105 | 0.634 |

## Players with 30+ games, by goals above xG

| player | games | shots | goals | xG | goals - xG | z | xG/shot |
|---|---|---|---|---|---|---|---|
| PastorKurt | 2898 | 8618 | 4037 | 3987.3 | +49.7 | +1.29 | 0.463 |
| PastorCamerius | 2636 | 8576 | 4247 | 4200.5 | +46.5 | +1.17 | 0.490 |
| Mat. | 149 | 739 | 235 | 218.8 | +16.2 | +1.63 | 0.296 |
| tawk | 71 | 362 | 128 | 115.1 | +12.9 | +1.87 | 0.318 |
| TempoH | 163 | 991 | 284 | 271.7 | +12.3 | +1.10 | 0.274 |
| Kehvn | 164 | 664 | 202 | 189.7 | +12.3 | +1.34 | 0.286 |
| Fairy Peak! | 61 | 285 | 95 | 83.0 | +12.0 | +1.99 | 0.291 |
| v.ops1 | 130 | 666 | 231 | 219.9 | +11.1 | +1.19 | 0.330 |
| delta | 74 | 347 | 133 | 123.2 | +9.8 | +1.38 | 0.355 |
| Abdullah | 76 | 433 | 151 | 141.2 | +9.8 | +1.28 | 0.326 |
| ... | | | | | | | |
| alemz | 87 | 417 | 116 | 125.9 | -9.9 | -1.33 | 0.302 |
| yANXNZ^^ | 101 | 611 | 181 | 193.3 | -12.3 | -1.37 | 0.316 |
| Lj | 71 | 393 | 132 | 144.4 | -12.4 | -1.62 | 0.367 |
| swazz | 144 | 649 | 190 | 203.4 | -13.4 | -1.47 | 0.313 |
| Arsenal | 92 | 473 | 125 | 139.6 | -14.6 | -1.90 | 0.295 |