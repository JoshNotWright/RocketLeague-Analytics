Decision policy (5 actions, 52 state features). Rows: 15293132 available, 2997825 sampled, train 1846609 / valid 388627 / test 762589; 1696 rounds.

Held-out log loss 0.4443 vs prior 1.4069; accuracy 0.808 vs majority 0.439.

| action | share | recall | mean p when true |
|---|---|---|---|
| challenge | 0.137 | 0.722 | 0.635 |
| big_pad | 0.047 | 0.579 | 0.495 |
| retreat | 0.182 | 0.833 | 0.766 |
| advance | 0.196 | 0.811 | 0.742 |
| hold | 0.438 | 0.847 | 0.779 |

Top features: me_vy (0.28), me_dist_ball (0.08), me_y (0.07), me_speed (0.05), ball_vy (0.05), me_vz (0.04), me_x (0.03), me_boost (0.03), me_z (0.03), me_vx (0.02)

Split-half reliability of per-player decision statistics (held-out games, 10+ games):

| statistic | players | correlation |
|---|---|---|
| decision_loglik | 76 | 0.523 |
| challenge_rate | 76 | 0.442 |
| challenge_minus_policy | 76 | 0.249 |

Players by decision log-likelihood under the GC+ policy (held-out games):

| player | games | mean loglik | challenge rate | policy challenge rate |
|---|---|---|---|---|
| Bosco | 20 | -0.4042 | 0.120 | 0.121 |
| Porsas52 | 11 | -0.4062 | 0.119 | 0.125 |
| Taroco | 13 | -0.4069 | 0.131 | 0.135 |
| Rxii | 10 | -0.4109 | 0.139 | 0.127 |
| dead | 12 | -0.4130 | 0.120 | 0.121 |
| syracks | 19 | -0.4136 | 0.122 | 0.122 |
| tawk | 15 | -0.4751 | 0.142 | 0.137 |
| M7md | 11 | -0.4796 | 0.147 | 0.144 |
| Arsenal | 25 | -0.4844 | 0.144 | 0.145 |
| Wahvey | 17 | -0.4988 | 0.153 | 0.146 |