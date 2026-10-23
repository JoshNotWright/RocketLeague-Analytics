# Definitions and limitations

## Identity and ingestion

A match is keyed by the provider's `match_guid` when present. The labeled fallback
`rocket_league_id` cannot guarantee linking different recording perspectives. Multiple replay
uploads can refer to one match. Agreeing normalized copies merge replay IDs;
conflicting copies are rejected rather than selected silently. Player identity
is `platform:id`. Names and team display names are descriptive attributes.

The normalizer validates playlist roster size, distinct participants, team
membership, timestamps, nonnegative finite statistics, scores, and exposure.
Known tied/incomplete results are rejected. Provider fields do not reliably
identify every forfeit or disconnection; a structurally complete record is not
proof of a normally completed game.

Exposure consistently uses replay elapsed seconds: `duration` for a match and
`end_time - start_time` for a player. This includes kickoff countdowns; it is not
active game-clock time. Rounded provider durations allow a one-second endpoint
tolerance. Rates normalize exposure but countdown frequency can still affect them.
A provider timestamp explicitly marked as lacking a known timezone requires an
explicit correction through metadata; the raw response is retained.

Optional statistics are null when absent. Canonical records retain a schema
version. Group IDs only become series labels through an explicit mapping;
a tournament group is not automatically assumed to be one series.

## Aggregation

For each observed count metric:

```text
per-match = sum of observed values / number of matches with that metric
per-five-minutes = 300 * sum of values with valid matched exposure
                       / sum of that exposure
```

The implementation uses `300 * rate_total / exposure_seconds`.
Missing exposure excludes the value from the rate numerator and denominator,
but the observed total remains available. Coverage counters accompany both.

Shooting accuracy is `100 * sum(credited goals) / sum(shots)` over rows where
both values are observed. Zero total shots yields null. A team score can include
an own goal, so credited shooting goals and the actual match score are separate
concepts. Boost is an exposure-weighted mean, with coverage.

Team observations are one row per roster per match. Additive team metrics require
all roster members' values; incomplete totals are not treated as full-team totals.
Team exposure is counted once per game. Opponent shots come from the actual
opposing roster, not repeated per-player opponent totals.

## Descriptive index

The reference is fit on an explicit cohort, partitioned by playlist, and saved
with a version, team size, metric weights, means, standard deviations, and shrinkage
settings. Do not refit it on each newly rated batch.

For a metric with sufficient observation, the player's rate is shrunk toward the
reference mean according to observed exposure and the configured prior exposure.
The standardized feature is its difference from the reference mean divided by
the reference scale. A constant reference feature has zero contribution.
The saved weights define a conventional composite; they are not estimated
causal effects or a conversion to wins.

All nonzero-weight components must be observed for ratings; missing weighted
counts produce an explicit error. Reports still accept null statistics and show
coverage. Unweighted components are not required.

The rating output contains its components, exposure, coverage, low-sample
information, and bootstrap intervals. Resampling uncertainty describes variability
under the sampling assumptions; it does not account for upload bias or all model
misspecification. Read the output's interval method and warning fields.

A suitable reference is specific to playlist and the intended population.
Rank/season comparability is a collection/design responsibility; rank is not
automatically adjusted within the canonical model.

## Experimental lineup model

For a match with known pregame lineups:

```text
P(blue win) = logistic(intercept + sum(blue player effects)
                               - sum(orange player effects))
```

A ridge penalty stabilizes the coefficients. Only player membership enters the
design; postgame box scores are never prediction features. The fitted effects
summarize associations in the training schedule.

The replacement effect is the mean coefficient of the user-specified replacement
players. For each scheduled match containing the focal player, compute the
player-side win probability with the observed lineup and with that player's
effect replaced by the replacement effect. Sum the differences:

```text
conditional expected wins above replacement =
    sum(P(player side wins | actual lineup)
        - P(player side wins | replacement effect substituted))
```

This keeps opponents and teammates fixed and makes the output's units clear.
It does not prove that a real replacement player would behave as an averaged
coefficient, fit the same role, or induce no teammate strategy changes.

The replacement pool must be defined before scoring and drawn from training.
Unknown players, limited connectedness, teammates who never separate, and low
sample sizes limit interpretation. Regularization and approximate coefficient
uncertainty cannot solve these identification problems.

## Evaluation

Sort matches by their actual UTC start time. Hold out later observations and
purge the entire series when it straddles the cutoff. No series is split across
training and the holdout. Fit coefficients
and the replacement effect on training only. Report held-out log loss, Brier score,
accuracy, and calibration summaries alongside 0.5 and training-outcome-rate
baselines.

A favorable score on one split is not final validation. For a real study, examine
multiple predeclared time windows, larger future cohorts, calibration uncertainty,
series clustering, rank and patch shifts, and sensitivity to replacement-pool
choice. Keep the final test window untouched while choosing regularization and
other settings.

The demo uses synthetic games and does not answer any of those empirical
questions.
