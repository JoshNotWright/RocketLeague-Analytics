Decision policy (5 actions, 54 state features, decision points only). Rows: 4038190 available, 2997869 sampled, train 1846887 / valid 385419 / test 765563; 1513 rounds.

Held-out log loss 0.9183 vs prior 1.5421; accuracy 0.617 vs majority 0.246.

| action | share | recall | mean p when true |
|---|---|---|---|
| challenge | 0.244 | 0.687 | 0.545 |
| big_pad | 0.071 | 0.223 | 0.249 |
| retreat | 0.229 | 0.760 | 0.596 |
| advance | 0.208 | 0.625 | 0.524 |
| hold | 0.249 | 0.523 | 0.425 |

Top features: me_y (0.13), ball_vy (0.06), me_boost (0.06), closing_speed (0.06), me_dist_ball (0.04), me_ahead_of_ball (0.03), me_closer_than_mate (0.03), opp1_dist_ball (0.03), time_since_kickoff (0.03), me_speed (0.03)

Split-half reliability of per-player decision statistics (held-out games, 10+ games):

| statistic | players | correlation |
|---|---|---|
| decision_loglik | 76 | 0.076 |
| challenge_rate | 76 | 0.412 |
| challenge_minus_policy | 76 | 0.296 |

Players by decision log-likelihood under the GC+ policy (held-out games):

| player | games | mean loglik | challenge rate | policy challenge rate |
|---|---|---|---|---|
| jch | 16 | -0.8470 | 0.214 | 0.243 |
| Taroco | 13 | -0.8504 | 0.204 | 0.236 |
| dead | 12 | -0.8767 | 0.211 | 0.223 |
| yANXNZ^^ | 28 | -0.8794 | 0.264 | 0.259 |
| GarrettG | 13 | -0.8829 | 0.233 | 0.238 |
| relatingwave | 14 | -0.8835 | 0.252 | 0.242 |
| Arsenal | 25 | -0.9586 | 0.251 | 0.246 |
| aris | 32 | -0.9601 | 0.240 | 0.236 |
| Firstkiller | 11 | -0.9714 | 0.278 | 0.273 |
| Wahvey | 17 | -0.9857 | 0.267 | 0.245 |