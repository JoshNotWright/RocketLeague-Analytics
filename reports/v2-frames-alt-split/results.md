## all_games_won

target=won rows train/valid/test = 87349/13167/17303 test period 2025-08-01 00:04:25+00:00 .. 2026-04-27 23:13:06+00:00
| model | log_loss | auc | brier | accuracy |
|---|---|---|---|---|
| constant | 0.6931 | 0.5000 | 0.2500 | 0.5033 |
| rank | 0.6925 | 0.5032 | 0.2497 | 0.5038 |
| rank+history | 0.6883 | 0.5569 | 0.2476 | 0.5397 |
| rank+history+summary | 0.6884 | 0.5565 | 0.2476 | 0.5400 |

- rank: 4 features, 54 rounds; top gain: rank_diff (0.56), team_rank_mean (0.16), opp_rank_mean (0.16), rank_points (0.12)
- rank+history: 9 features, 116 rounds; top gain: prev_win_rate (0.41), n_prev_games (0.20), days_since_prev (0.17), r10_goal_diff (0.11), rank_diff (0.04), r10_won (0.03)
- rank+history+summary: 86 features, 153 rounds; top gain: prev_win_rate (0.14), n_prev_games (0.04), r10_core_shots (0.03), days_since_prev (0.02), r10_boost_amount_overfill (0.02), r10_core_score (0.02)

## all_games_rank_change

target=rank_change rows train/valid/test = 85412/12601/14503 test period 2025-08-01 00:04:25+00:00 .. 2026-03-01 22:50:23+00:00
| model | mae | rmse | r2 |
|---|---|---|---|
| constant | 0.2200 | 0.6026 | -0.0099 |
| rank | 0.2326 | 0.4659 | 0.3962 |
| rank+history | 0.2025 | 0.4777 | 0.3654 |
| rank+history+summary | 0.2010 | 0.4664 | 0.3950 |

- rank: 4 features, 515 rounds; top gain: rank_points (0.73), team_rank_mean (0.26), opp_rank_mean (0.00), rank_diff (0.00)
- rank+history: 9 features, 71 rounds; top gain: rank_points (0.72), team_rank_mean (0.19), n_prev_games (0.05), prev_win_rate (0.02), days_since_prev (0.01), r10_goal_diff (0.01)
- rank+history+summary: 86 features, 80 rounds; top gain: rank_points (0.60), team_rank_mean (0.13), n_prev_games (0.04), opp_rank_mean (0.04), rank_diff (0.02), prev_win_rate (0.02)

## frame_subset_won

target=won rows train/valid/test = 1850/369/641 test period 2025-08-01 01:47:56+00:00 .. 2026-03-01 02:22:04+00:00
| model | log_loss | auc | brier | accuracy |
|---|---|---|---|---|
| constant | 0.6948 | 0.5000 | 0.2508 | 0.4633 |
| rank | 0.6948 | 0.5000 | 0.2508 | 0.4633 |
| rank+history | 0.6927 | 0.5241 | 0.2498 | 0.5195 |
| rank+history+summary | 0.6942 | 0.5340 | 0.2505 | 0.4633 |
| rank+history+frame | 0.6946 | 0.4853 | 0.2507 | 0.4711 |
| rank+history+summary+frame | 0.6947 | 0.4988 | 0.2508 | 0.4633 |

- rank: 4 features, 1 rounds; top gain: rank_diff (0.00), opp_rank_mean (0.00), team_rank_mean (0.00), rank_points (0.00)
- rank+history: 9 features, 19 rounds; top gain: prev_win_rate (0.33), n_prev_games (0.24), days_since_prev (0.20), r10_goal_diff (0.17), r10_won (0.05), rank_diff (0.00)
- rank+history+summary: 86 features, 1 rounds; top gain: prev_win_rate (0.26), r10_movement_percent_high_air (0.16), r10_core_saves (0.12), r10_positioning_time_defensive_third (0.10), r10_movement_time_powerslide (0.09), r10_positioning_time_infront_ball (0.08)
- rank+history+frame: 31 features, 3 rounds; top gain: r10_small_pads (0.22), r10_kickoffs (0.17), prev_win_rate (0.15), days_since_prev (0.10), r10_last_man_frac (0.05), r10_zero_boost_frac (0.05)
- rank+history+summary+frame: 108 features, 1 rounds; top gain: prev_win_rate (0.25), r10_small_pads (0.17), r10_boost_avg_amount (0.17), r10_boost_time_zero_boost (0.08), r10_boost_amount_used_while_supersonic (0.08), r10_positioning_percent_offensive_half (0.07)
