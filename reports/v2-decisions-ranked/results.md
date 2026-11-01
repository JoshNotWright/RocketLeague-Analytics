Decision policy (5 actions, 56 state features, decision points only). Rows: 8261970 available, 3999065 sampled, train 3130995 / valid 290024 / test 578046; 2000 rounds.

Held-out log loss 0.9189 vs prior 1.5450; accuracy 0.617 vs majority 0.275.

| action | share | recall | mean p when true |
|---|---|---|---|
| challenge | 0.236 | 0.681 | 0.537 |
| big_pad | 0.072 | 0.230 | 0.252 |
| retreat | 0.228 | 0.762 | 0.594 |
| advance | 0.210 | 0.626 | 0.518 |
| hold | 0.255 | 0.529 | 0.431 |

Top features: me_y (0.15), ball_vy (0.07), me_boost (0.05), closing_speed (0.05), me_dist_ball (0.05), me_ahead_of_ball (0.04), me_speed (0.03), time_to_ball (0.03), time_since_kickoff (0.02), me_vy (0.02)

Split-half reliability of per-player decision statistics (held-out games, 10+ games):

| statistic | players | correlation |
|---|---|---|
| decision_loglik | 78 | 0.158 |
| challenge_rate | 78 | 0.454 |
| challenge_minus_policy | 78 | 0.333 |
| gc_gap | 78 | 0.504 |
| retreat_where_gc_challenges | 78 | 0.030 |
| challenge_where_gc_would_not | 78 | 0.352 |

Counterfactual: the same held-out states scored with both rank inputs set to 80 rank points (GC).

| player rank bin | rows | challenge rate | GC policy challenge rate | GC gap (nats) |
|---|---|---|---|---|
| 12-23 | 120 | 0.050 | 0.132 | 0.1198 |
| 36-47 | 1252 | 0.160 | 0.208 | 0.0076 |
| 48-59 | 34715 | 0.198 | 0.215 | 0.0228 |
| 60-71 | 1562 | 0.182 | 0.205 | 0.0086 |
| 72-83 | 350 | 0.271 | 0.241 | 0.0035 |
| 84-95 | 341834 | 0.242 | 0.230 | 0.0117 |

Actual action -> action the GC-conditioned policy rates most likely (share of held-out rows):

| actual \ GC policy | challenge | big_pad | retreat | advance | hold |
|---|---|---|---|---|---|
| challenge | 0.156 | 0.002 | 0.024 | 0.020 | 0.034 |
| big_pad | 0.005 | 0.015 | 0.025 | 0.016 | 0.010 |
| retreat | 0.019 | 0.005 | 0.174 | 0.006 | 0.025 |
| advance | 0.025 | 0.005 | 0.006 | 0.132 | 0.040 |
| hold | 0.039 | 0.005 | 0.036 | 0.041 | 0.134 |

Players by decision log-likelihood under the policy at their own rank (held-out games):

| player | games | mean loglik | challenge rate | policy challenge rate |
|---|---|---|---|---|
| jch | 16 | -0.8431 | 0.214 | 0.244 |
| Taroco | 13 | -0.8440 | 0.204 | 0.240 |
| yANXNZ^^ | 28 | -0.8784 | 0.266 | 0.260 |
| A7mD | 41 | -0.8800 | 0.282 | 0.265 |
| Chronic | 11 | -0.8804 | 0.227 | 0.227 |
| mimillo | 27 | -0.8815 | 0.226 | 0.234 |
| aris | 32 | -0.9717 | 0.238 | 0.238 |
| Arsenal | 25 | -0.9790 | 0.255 | 0.248 |
| Firstkiller | 11 | -0.9876 | 0.275 | 0.268 |
| Wahvey | 17 | -0.9939 | 0.272 | 0.246 |