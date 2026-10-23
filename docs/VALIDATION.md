# Validation record

Verified locally on 2026-10-23 using Windows, Python 3.12.14, and NumPy 2.3.5.

## Automated checks

`python -B -m unittest discover -s tests -v`: **79 tests passed**.

The suite covers stable IDs, provider field mapping, nullable statistics, malformed
and incomplete records, elapsed exposure, timezone corrections, named CSV import,
atomic writes, duplicate upload merging/conflict quarantine, preserved query
filters, direct/child group pagination, sampling, retries, safe pagination targets,
incomplete-run status, exact player identity, observed rosters, pooled shooting,
weighted boost, correct team denominators, private team-size separation,
constant reference features, exposure shrinkage, reproducible bootstrap intervals,
replacement definitions, shared parameter uncertainty, roster confounding,
pregame-only predictions, temporal/series separation, and import-safe legacy scripts.

The end-to-end CLI test generates 100 synthetic matches, trains on 80, evaluates
20 later matches, and writes reports, CSV exports, a frozen reference, descriptive
ratings, a fitted model, and a conditional win-impact estimate.

Python source parsing and package metadata parsing passed. A current-tree
credential-literal check found no matching embedded API tokens. The original
credential remains in Git history; no rotation or history rewrite was performed.

## Limits

The tests use synthetic data and mocked HTTP transports. No live API collection
or real-cohort model validation was performed. These checks demonstrate software
behavior, not predictive accuracy, causal validity, or a defensible empirical
replacement level. Provider upload bias and optional missingness remain explicit
limitations.

The fitted model uses dense matrices and a full coefficient covariance matrix.
It is intended for bounded cohorts with useful lineup variation; very large
matchmaking populations need a sparse/scalable estimation design before fitting.

An offline wheel build also passed using pip wheel --no-index --no-deps --no-build-isolation.
