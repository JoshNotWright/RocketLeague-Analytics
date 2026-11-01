## all_games_won

target=won rows train/valid/test = 62245/33979/21595 test period 2025-07-01 00:00:53+00:00 .. 2026-04-27 23:13:06+00:00
| model | log_loss | auc | brier | accuracy |
|---|---|---|---|---|
| constant | 0.6931 | 0.5000 | 0.2500 | 0.5042 |
| rank | 0.6926 | 0.5035 | 0.2497 | 0.5052 |
| rank+history | 0.6891 | 0.5571 | 0.2479 | 0.5398 |
| rank+history+summary | 0.6883 | 0.5575 | 0.2476 | 0.5401 |

- rank: 4 features, 74 rounds; top gain: rank_diff (0.49), team_rank_mean (0.20), opp_rank_mean (0.20), rank_points (0.12)
- rank+history: 9 features, 80 rounds; top gain: prev_win_rate (0.38), n_prev_games (0.22), days_since_prev (0.17), r10_goal_diff (0.12), rank_diff (0.04), r10_won (0.04)
- rank+history+summary: 86 features, 106 rounds; top gain: prev_win_rate (0.12), n_prev_games (0.06), r10_core_shots (0.03), r10_movement_percent_high_air (0.02), r10_boost_percent_boost_75_100 (0.02), r10_boost_amount_overfill (0.02)

## all_games_rank_change

target=rank_change rows train/valid/test = 60847/32987/18682 test period 2025-07-01 00:00:53+00:00 .. 2026-03-01 22:50:23+00:00
| model | mae | rmse | r2 |
|---|---|---|---|
| constant | 0.2094 | 0.5607 | -0.0136 |
| rank | 0.2543 | 0.4550 | 0.3327 |
| rank+history | 0.2116 | 0.4507 | 0.3450 |
| rank+history+summary | 0.2106 | 0.4532 | 0.3379 |

- rank: 4 features, 121 rounds; top gain: rank_points (0.73), team_rank_mean (0.27), opp_rank_mean (0.00), rank_diff (0.00)
- rank+history: 9 features, 82 rounds; top gain: rank_points (0.71), team_rank_mean (0.19), n_prev_games (0.04), prev_win_rate (0.03), days_since_prev (0.01), r10_goal_diff (0.01)
- rank+history+summary: 86 features, 77 rounds; top gain: rank_points (0.59), team_rank_mean (0.13), n_prev_games (0.06), opp_rank_mean (0.04), rank_diff (0.02), r10_movement_percent_low_air (0.01)

## frame_subset_won

target=won rows train/valid/test = 1160/964/736 test period 2025-07-01 00:00:53+00:00 .. 2026-03-01 02:22:04+00:00
| model | log_loss | auc | brier | accuracy |
|---|---|---|---|---|
| constant | 0.6952 | 0.5000 | 0.2510 | 0.4497 |
| rank | 0.6952 | 0.5000 | 0.2510 | 0.4497 |
| rank+history | 0.6914 | 0.5471 | 0.2491 | 0.5489 |
| rank+history+summary | 0.6946 | 0.5162 | 0.2507 | 0.4688 |
| rank+history+frame | 0.6944 | 0.5118 | 0.2506 | 0.5136 |
| rank+history+summary+frame | 0.6936 | 0.5297 | 0.2502 | 0.5122 |

- rank: 4 features, 1 rounds; top gain: rank_diff (0.00), opp_rank_mean (0.00), team_rank_mean (0.00), rank_points (0.00)
- rank+history: 9 features, 28 rounds; top gain: prev_win_rate (0.36), days_since_prev (0.25), n_prev_games (0.25), r10_goal_diff (0.07), r10_won (0.06), rank_diff (0.00)
- rank+history+summary: 86 features, 5 rounds; top gain: prev_win_rate (0.12), r10_positioning_avg_distance_to_mates (0.11), days_since_prev (0.09), r10_positioning_percent_offensive_third (0.07), r10_boost_amount_used_while_supersonic (0.06), r10_boost_count_collected_small (0.05)
- rank+history+frame: 31 features, 34 rounds; top gain: r10_small_pads (0.13), prev_win_rate (0.09), days_since_prev (0.08), r10_touch_rate (0.08), r10_kickoff_time_to_ball (0.06), r10_avg_dist_mate (0.06)
- rank+history+summary+frame: 108 features, 12 rounds; top gain: prev_win_rate (0.10), days_since_prev (0.06), r10_touch_rate (0.05), r10_small_pads (0.05), r10_boost_count_collected_small (0.04), r10_avg_dist_mate (0.04)
