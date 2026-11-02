Decision policy (5 actions, 56 state features, decision points only). Rows: 20734404 available, 3979863 sampled, train 1240617 / valid 114815 / test 2624431; 1146 rounds.

Held-out log loss 0.9410 vs prior 1.5409; accuracy 0.606 vs majority 0.275.

| action | share | recall | mean p when true |
|---|---|---|---|
| challenge | 0.214 | 0.649 | 0.504 |
| big_pad | 0.075 | 0.225 | 0.244 |
| retreat | 0.218 | 0.748 | 0.589 |
| advance | 0.208 | 0.622 | 0.505 |
| hold | 0.285 | 0.554 | 0.451 |

Top features: me_y (0.14), ball_vy (0.07), me_boost (0.05), me_dist_ball (0.05), closing_speed (0.05), me_ahead_of_ball (0.04), me_speed (0.03), time_to_ball (0.03), me_vy (0.02), opp1_dist_ball (0.02)

Split-half reliability of per-player decision statistics (held-out games, 10+ games):

| statistic | players | correlation |
|---|---|---|
| decision_loglik | 292 | 0.287 |
| challenge_rate | 292 | 0.645 |
| challenge_minus_policy | 292 | 0.459 |
| gc_gap | 292 | 0.670 |
| retreat_where_gc_challenges | 292 | 0.255 |
| challenge_where_gc_would_not | 292 | 0.514 |

Counterfactual: the same held-out states scored with both rank inputs set to 80 rank points (GC).

| player rank bin | rows | challenge rate | GC policy challenge rate | GC gap (nats) |
|---|---|---|---|---|
| 12-23 | 55 | 0.091 | 0.145 | 0.0921 |
| 24-35 | 62 | 0.113 | 0.167 | 0.0995 |
| 36-47 | 493 | 0.160 | 0.220 | 0.0348 |
| 48-59 | 391862 | 0.201 | 0.222 | 0.0141 |
| 60-71 | 592428 | 0.210 | 0.227 | 0.0092 |
| 72-83 | 394806 | 0.226 | 0.230 | 0.0005 |
| 84-95 | 217179 | 0.240 | 0.233 | 0.0066 |

Actual action -> action the GC-conditioned policy rates most likely (share of held-out rows):

| actual \ GC policy | challenge | big_pad | retreat | advance | hold |
|---|---|---|---|---|---|
| challenge | 0.145 | 0.002 | 0.020 | 0.016 | 0.031 |
| big_pad | 0.006 | 0.015 | 0.025 | 0.018 | 0.011 |
| retreat | 0.018 | 0.005 | 0.166 | 0.006 | 0.024 |
| advance | 0.029 | 0.004 | 0.006 | 0.129 | 0.040 |
| hold | 0.045 | 0.006 | 0.038 | 0.049 | 0.147 |

Players by decision log-likelihood under the policy at their own rank (held-out games):

| player | games | mean loglik | challenge rate | policy challenge rate |
|---|---|---|---|---|
| Catman (chat off) | 10 | -0.8154 | 0.235 | 0.232 |
| yeet indeed | 11 | -0.8283 | 0.183 | 0.183 |
| BuddyHardmouth | 10 | -0.8285 | 0.183 | 0.189 |
| GarrettG | 13 | -0.8347 | 0.224 | 0.240 |
| Taroco | 13 | -0.8385 | 0.200 | 0.242 |
| negate | 11 | -0.8392 | 0.198 | 0.215 |
| 2lii | 10 | -1.0229 | 0.284 | 0.232 |
| NotMisterGrimmm | 11 | -1.0283 | 0.229 | 0.244 |
| volterohh | 12 | -1.0406 | 0.223 | 0.202 |
| UwUGamerBoi | 15 | -1.0469 | 0.234 | 0.231 |