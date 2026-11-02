Touches: train 819782 / valid 89723 / test 1668084 (fit < 2025-04-01, early-stop to 2025-07-01, test after).

## touch_xg (target `scored`, 40 features, 843 rounds)

| model | log loss | AUC | Brier |
|---|---|---|---|
| constant (base rate 0.0325) | 0.1429 | 0.5000 | 0.0313 |
| LightGBM | 0.0508 | 0.9762 | 0.0139 |
| LightGBM, shots only (n=132324) | 0.4053 | 0.8737 | 0.1305 |

Calibration (test deciles, mean predicted vs actual):

| decile | n | predicted | actual |
|---|---|---|---|
| 0 | 166809 | 0.0001 | 0.0003 |
| 1 | 166809 | 0.0002 | 0.0004 |
| 2 | 166809 | 0.0003 | 0.0006 |
| 3 | 166809 | 0.0004 | 0.0007 |
| 4 | 166808 | 0.0007 | 0.0010 |
| 5 | 166808 | 0.0010 | 0.0015 |
| 6 | 166808 | 0.0016 | 0.0023 |
| 7 | 166808 | 0.0028 | 0.0037 |
| 8 | 166808 | 0.0070 | 0.0086 |
| 9 | 166808 | 0.3129 | 0.3047 |

Top features by gain: shot (0.35), proj_x (0.09), ball_y (0.08), goal_angle_width (0.06), proj_t (0.05), proj_z (0.04), post_angle_off_goal (0.04), opp_min_dist_goal (0.03), post_vy (0.02), post_speed (0.02)

## threat (target `team_goal_within_10s`, 30 features, 379 rounds)

| model | log loss | AUC | Brier |
|---|---|---|---|
| constant (base rate 0.1256) | 0.3710 | 0.5000 | 0.1071 |
| LightGBM | 0.3251 | 0.7202 | 0.0934 |

Calibration (test deciles, mean predicted vs actual):

| decile | n | predicted | actual |
|---|---|---|---|
| 0 | 166809 | 0.0365 | 0.0366 |
| 1 | 166809 | 0.0547 | 0.0537 |
| 2 | 166809 | 0.0658 | 0.0631 |
| 3 | 166809 | 0.0760 | 0.0728 |
| 4 | 166808 | 0.0867 | 0.0823 |
| 5 | 166808 | 0.0993 | 0.0939 |
| 6 | 166808 | 0.1158 | 0.1093 |
| 7 | 166808 | 0.1397 | 0.1338 |
| 8 | 166808 | 0.1840 | 0.1773 |
| 9 | 166808 | 0.4035 | 0.3974 |

Top features by gain: goal_angle_width (0.22), dist_goal (0.12), opps_behind_ball (0.10), opp_min_dist_ball (0.07), opp_min_dist_goal (0.05), me_boost (0.03), me_speed (0.03), dist_own_goal (0.03), ball_y (0.03), pre_vy (0.03)

## threat_against (target `opp_goal_within_10s`, 30 features, 302 rounds)

| model | log loss | AUC | Brier |
|---|---|---|---|
| constant (base rate 0.0720) | 0.2557 | 0.5000 | 0.0658 |
| LightGBM | 0.2401 | 0.6617 | 0.0626 |

Calibration (test deciles, mean predicted vs actual):

| decile | n | predicted | actual |
|---|---|---|---|
| 0 | 166809 | 0.0244 | 0.0243 |
| 1 | 166809 | 0.0378 | 0.0381 |
| 2 | 166809 | 0.0448 | 0.0454 |
| 3 | 166809 | 0.0512 | 0.0508 |
| 4 | 166808 | 0.0577 | 0.0567 |
| 5 | 166808 | 0.0649 | 0.0639 |
| 6 | 166808 | 0.0732 | 0.0707 |
| 7 | 166808 | 0.0836 | 0.0809 |
| 8 | 166808 | 0.1001 | 0.0954 |
| 9 | 166808 | 0.1818 | 0.1817 |

Top features by gain: dist_own_goal (0.17), opp_min_dist_ball (0.07), ball_y (0.05), me_dist_own_goal (0.05), me_boost (0.04), dist_goal (0.04), mate_min_dist_own_goal (0.04), seconds_remaining (0.04), opp_nearest_boost (0.04), mate_min_dist (0.04)

## Per player-game checks

47571 player-games joined to ballchasing. corr(xG, goals) = 0.804; corr(our goals, ballchasing goals) = 0.987; corr(our shots, ballchasing shots) = 0.786.
Means per player-game: shots 3.69 (ballchasing 3.77), goals 1.45 (ballchasing 1.48), xG 1.46.

## Is any of it skill?

194 players with 30+ games. Variance of z = (goals - xG) / sqrt(sum p(1-p)) is 1.09 (1.00 means pure binomial noise); 4% of players have |z| > 2 (5% expected by chance).

Split-half reliability (odd vs even games, per player):

| statistic | players | correlation |
|---|---|---|
| goals_above_xg_per_game | 205 | 0.060 |
| xg_per_game | 205 | 0.665 |
| xg_per_shot | 198 | 0.740 |
| mean_threat | 205 | 0.520 |
| shots_per_game | 205 | 0.796 |

## Players with 30+ games, by goals above xG

| player | games | shots | goals | xG | goals - xG | z | xG/shot |
|---|---|---|---|---|---|---|---|
| PastorKurt | 2898 | 8618 | 4037 | 3987.3 | +49.7 | +1.29 | 0.463 |
| PastorCamerius | 2636 | 8576 | 4247 | 4200.5 | +46.5 | +1.17 | 0.490 |
| Mat. | 149 | 739 | 235 | 218.8 | +16.2 | +1.63 | 0.296 |
| k s t n | 40 | 91 | 52 | 38.2 | +13.8 | +3.30 | 0.420 |
| tawk | 71 | 362 | 128 | 115.1 | +12.9 | +1.87 | 0.318 |
| CptKrunchie | 40 | 125 | 66 | 53.2 | +12.8 | +2.67 | 0.426 |
| ZoronoaRoro10 | 30 | 130 | 74 | 61.3 | +12.7 | +2.75 | 0.472 |
| TempoH | 163 | 991 | 284 | 271.7 | +12.3 | +1.10 | 0.274 |
| Kehvn | 164 | 664 | 202 | 189.7 | +12.3 | +1.34 | 0.286 |
| Fairy Peak! | 61 | 285 | 95 | 83.0 | +12.0 | +1.99 | 0.291 |
| ... | | | | | | | |
| Lj | 71 | 393 | 132 | 144.4 | -12.4 | -1.62 | 0.367 |
| Sixela2048 | 40 | 109 | 38 | 50.4 | -12.4 | -2.68 | 0.463 |
| swazz | 144 | 649 | 190 | 203.4 | -13.4 | -1.47 | 0.313 |
| Bucky Hustle | 38 | 149 | 57 | 71.3 | -14.3 | -2.73 | 0.479 |
| Arsenal | 92 | 473 | 125 | 139.6 | -14.6 | -1.90 | 0.295 |