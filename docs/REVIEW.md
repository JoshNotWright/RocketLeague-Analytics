# Review of the original project

Reviewed baseline: commit `6f0c8538bbe7681e3267b6f3b7200a1f054b8803`.
This review concerns that revision; the reliability branch replaces its behavior.

## What was good

- The project pursued player-level and team-level analysis, rather than only
  leaderboard totals.
- It recognized that offensive and defensive contributions differ and that
  context matters.
- Ballchasing replay data offered a better foundation than manual score entry.
- Separate downloader, aggregation, scoring, and presentation scripts expressed
  useful conceptual boundaries.
- The ambition to compare against replacement level was a worthwhile research
  question, even though the implemented score had not established that meaning.

These are strengths of direction and decomposition, not proof that the original
outputs were correct.

## What was bad, and how it changes

| Original issue | Consequence | Replacement |
| --- | --- | --- |
| Match keys built from shortened player IDs and total score | Distinct games collide and valid data can be deleted | Provider match GUID plus full player IDs; conflicting copies rejected |
| Name/prefix identity lookup | Different players can be merged, renamed players split | Full platform ID with names as attributes |
| First data row skipped in leaderboard logic | The leading input player can disappear | Validated records, no implicit header skipping |
| A stale game-count scalar applied across players | Per-game rates use another player's denominator | Per-entity observation counts and exposure |
| Appended team snapshots and first-row selection | Old aggregates can be reported as current | Fresh deterministic aggregation |
| Blank/missing metrics coerced to zero | Unobserved data becomes apparent poor performance | Nulls and per-metric coverage |
| Average boost accumulated as a sum | More games appear to mean more average boost | Duration-weighted mean |
| Mean of per-game shooting percentages | A one-shot game counts like a high-volume game | Pooled jointly observed goals and shots |
| Opponent totals repeated across roster members | Team defense can be multiplied by roster size | Aggregate from actual opposing roster once |
| Current Liquipedia team assignment | Historical games attributed to the wrong team | Observed roster identity from each match |
| Hardcoded season/mode and recent uploads labeled random | Sample differs from the intended cohort | Explicit filters, complete enumeration, seeded sampling |
| Group child/direct paths and pagination not all handled | Games silently omitted | Traverse both direct and child paths and pagination |
| Download cleanup deletes raw inputs | Results cannot be reconstructed or failures investigated | Preserved raw responses and checksums |
| Partial downloads reported as successful | Missingness hidden downstream | Incomplete manifest and nonzero CLI status |
| Import-time networking, mutation, pickle cache, embedded token | Merely importing a helper is unsafe and hard to test | Explicit CLI operations, no executable cache, environment token |
| No dependency specification or regression tests | Reproducing outputs and detecting regressions is difficult | Package metadata, pinned validation environment, offline tests |

## False assumptions and algorithm critique

### A weighted box-score sum is not wins above replacement

The original score combined hand-selected offensive and defensive terms and
normalized them. That does not establish a conversion to wins, a replacement
population, or a counterfactual. A standardized score can be a descriptive index,
but calling it WAR overstates what it measures.

The new design separates two products: a transparent descriptive index and an
experimental lineup model whose counterfactual output is explicitly in expected
wins. The latter still requires external validation and causal caution.

### Min-max normalization does not make incomparable terms meaningful

Fitting min/max on each rated sample makes every player's scale depend on the
other players included. An outlier changes effective weights, and a constant
column creates a zero denominator. A player can change score without changing
performance.

A fixed, versioned reference cohort keeps the scale stable. Constant reference
features receive zero standardized contribution instead of dividing by zero.
The choice of reference remains a substantive assumption.

### Territory and distance are not opportunity corrections by themselves

Dividing offense by time in the offensive zone creates a small-denominator
reward and can make zero-zone observations discontinuous. Defensive distance
and last-defender position cannot establish individual responsibility for
conceded goals: role, teammate errors, opponent quality, and game state influence
both positioning and outcomes.

The new descriptive score does not use those divisions. Causal claims would need
richer possession/opportunity data and a defensible design, not another arbitrary
coefficient.

### Shooting accuracy is not independent of scoring volume or shot selection

The old accuracy term could reward one goal from one shot more than a larger
scoring contribution with more attempts. Goals, shots, and shooting percentage
also partly reuse the same information. Counting them as independent evidence
double-counts contributions.

Reports retain pooled accuracy for interpretation. The default descriptive
weights are explicit conventions, not learned value per shot. Estimating shot
quality would require additional event/context features and validation.

### Raw totals and per-game values hide exposure differences

A longer overtime gives more time to accumulate stats. Mixed totals, averages,
and percentages do not become commensurate just because they share a formula.
Rates now use observed replay seconds, with coverage carried into outputs.
Even time normalization cannot remove score effects, role effects, or selection
bias from short/disconnected appearances.

### Uploaded games are not the player population

A random draw from uploads remains biased toward uploaders and available games.
Filters and seeds make that process auditable; they do not make it representative.
Missing pages, rejected data, private matches, and partial series can further
change the cohort.

### Team strength cannot always be split into player strength

Players who always play together provide little or no evidence about their
separate effects. Regularization can produce unique numbers without identifying
unique real-world contributions. The experimental model reports design-rank and
related warnings; the uncertainty is conditional on its assumptions and does
not remove confounding.

### A current name or organization is not historical identity

Display names can change and organizations can acquire different rosters.
Platform IDs and replay rosters are the appropriate primary identities.
Cross-platform account linking is deliberately not inferred.

## What remains a research problem

A useful real-world impact metric needs a declared target population, transparent
replacement selection, adequate lineup variation, stable data collection, temporal
and series-separated evaluation, probability calibration checks, and comparison
to simple baselines. It should also examine rank, patch, season, and skill drift.

No real-cohort accuracy claim is made by this branch. The implementation supplies
an auditable experiment and regression coverage; it does not manufacture evidence
that the experiment succeeds.

## Concrete baseline examples

The isolated checks against the original revision are preserved in
[baseline-regressions.json](baseline-regressions.json). They are historical
observations from synthetic counterexamples, not current model results.

- Two distinct input games produced the same shortened match key.
- Constant-range min-max scaling raised `ZeroDivisionError`.
- Averaging game shooting percentages yielded 50%, while pooled goals/shots was 10%.
- At identical other inputs, the offensive-zone denominator produced scores of
  0.835 at 30%, 8.35 at 3%, 2505 at 0.01%, and then 0 at exactly 0%.
- Adding a defensive outlier reversed A/B ordering without changing A or B.
- A one-goal/one-shot example scored 0.835 while a two-goal/four-shot example
  scored about 0.673 under the original offensive formula.
