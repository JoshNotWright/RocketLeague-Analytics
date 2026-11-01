# Rocket League Analytics

A reproducible pipeline for collecting Rocket League replay data, reporting observed
player/roster statistics, and experimenting with lineup-based win predictions.

The previous scripts are retired entry points. Start with the package CLI below.
The original review, including good ideas, bugs, and false assumptions, is in
[docs/REVIEW.md](docs/REVIEW.md). Model definitions and limitations are in
[docs/METHODOLOGY.md](docs/METHODOLOGY.md).
See [docs/VALIDATION.md](docs/VALIDATION.md) for the checks performed and their limits.

**v2 (replay parsing):** the `rlreplay` package parses raw `.replay` files from the
public GC+ replay dataset, computes per-player-game frame features, and tests whether they
predict outcomes better than ballchasing's summary stats under a strict time split. See
[docs/V2_METHODOLOGY.md](docs/V2_METHODOLOGY.md) for the design and
[docs/V2_RESULTS.md](docs/V2_RESULTS.md) for the numbers, and
[docs/V2_XG.md](docs/V2_XG.md) for the touch-level expected-goals, threat, value-added and
save models built on the same replays, [docs/V2_DECISIONS.md](docs/V2_DECISIONS.md) for the
frame-level decision policy, and [docs/GPU.md](docs/GPU.md) for training on the AMD GPU. To
score a folder of your own replays against the GC+ reference:
`python -m rlreplay score --raw <folder> --out reports/me`. Crawling lower ranks from
ballchasing (`python -m rlreplay crawl`) needs `BALLCHASING_API_KEY` in the environment and
is described at the end of the methodology doc. Everything else needs no ballchasing key:
replays come one file at a time from Kaggle's public per-file endpoint, and parsing uses
the `rrrocket` binary (nickbabcock) placed in `tools/`. Its extra dependencies are listed
in `requirements-v2.txt`. `data/` is gitignored and may be a junction to a larger drive
(`mklink /J data D:\RocketLeague-Analytics\data`); replays run to about 1 GB per thousand.

Security note: a ballchasing API token was committed in the 2023 version of this repository
and is still present in the published Git history. It must be revoked and reissued through
ballchasing; nothing in this repository reads it.

## Install and verify

Python 3.10+ is required. The pinned verification environment uses Python 3.12
and NumPy 2.3.5. The collector uses the Python standard library.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install --no-deps -e .
python -B -m unittest discover -s tests -v
python -m rlanalytics demo --output demo-output
```

The demo requires a new or empty output directory and is entirely offline. It creates 100 synthetic doubles matches, a frozen
reference from the first 80, future-match descriptive ratings, a chronological
holdout evaluation, and a conditional replacement estimate. These results check
software behavior; they provide **no evidence of real-world predictive accuracy**.
Generated datasets, credentials, and demo outputs are ignored by Git.

## Collect replays

Set `BALLCHASING_TOKEN` privately in your environment. Do not add it to source,
configuration files, screenshots, or Git. A token previously embedded in the old
scripts has been removed from this version, but remains in Git history and should
be revoked through the provider.

Create a configuration such as `collection.json`:

```json
{
  "filters": {
    "playlist": "ranked-doubles",
    "season": "f10"
  },
  "sample_size": 100,
  "seed": 7,
  "expected_players_per_team": 2,
  "max_retries": 3,
  "timeout": 30
}
```

The historical season above is only an example: choose the intended season
explicitly. Provider filter names and accepted values are documented in the
[Ballchasing API](https://ballchasing.com/doc/api). Filters are sent on enumeration
requests and preserved in the manifest.

```powershell
python -m rlanalytics collect --config collection.json --output data/cohort-001
```

Use a new empty output directory for each run. The collector records raw JSON,
checksums, query settings, selected replay IDs, accepted/rejected counts, and
errors. An incomplete collection exits with status 2. Reports should be interpreted
alongside `manifest.json`, including rejection counts. A seeded sample is taken
**after** all eligible upload IDs have been enumerated. It is a sample of those
uploads, not a random sample of all Rocket League games.

For a known replay list, use `{"replay_ids": ["REPLAY_ID"]}`. For groups, use
`{"group_ids": ["GROUP_ID"]}`; direct games, child groups, and paginated results are
enumerated. Do not combine explicit replay IDs with filters and assume those
filters were verified. Optional `group_series_map` and `replay_series_map` assign
series IDs; only label a group as a series when it really represents one series.

If you already have replay-detail JSON:

```powershell
python -m rlanalytics import-json --input replay.json --output data/matches.jsonl
```

Repeat `--input` for additional files. Supported imports are ranked duels, doubles,
standard, solo standard, and private matches. Private matches require provider
`team_size` or an explicit `--players-per-team`. Validation rejects
ambiguous identity, malformed scores, ties, and incomplete rosters. Optional
statistics remain null when absent.

## Reports and descriptive ratings

```powershell
python -m rlanalytics report --input data/cohort-001/matches.jsonl --output output/report.json --csv-dir output/csv
python -m rlanalytics reference --input data/training.jsonl --output output/reference.json --version doubles-v1 --playlist ranked-doubles
python -m rlanalytics rate --input data/future.jsonl --reference output/reference.json --output output/ratings.json --seed 7
```

Player identity is the full platform ID; names are labels. A team represents the
roster that actually played, independently of current organization affiliation.
A roster change creates a different team entity. Player and team reports are
partitioned by playlist and team size.

Rates use observed replay elapsed seconds, including kickoff countdowns, and report missingness. Shooting percentage pools
credited goals and shots from jointly observed matches; a zero-shot denominator
is undefined. Boost is a duration-weighted mean, not a sum.

The descriptive index uses fixed reference means/scales and explicit weights.
It rejects missing weighted component counts instead of imputing zeros; choose
an explicitly documented subset of metrics with `--weights` when appropriate.
Keep the reference file unchanged for comparisons; change its version when the
cohort or assumptions change. Use a reference cohort with a suitable date range,
playlist, rank, and sampling policy. Rank comparability is not automatically
established by the normalized records. This index is **not WAR** and its weights
are not estimated causal contributions to winning.

## Experimental win impact

```powershell
python -m rlanalytics evaluate --input data/matches.jsonl --replacement-id steam:FULL_ID_1 --replacement-id steam:FULL_ID_2 --output output/evaluation.json
python -m rlanalytics fit-impact --input data/training.jsonl --replacement-id steam:FULL_ID_1 --replacement-id steam:FULL_ID_2 --output output/model.json
python -m rlanalytics impact --input data/future.jsonl --model output/model.json --player-id steam:PLAYER_ID --output output/impact.json
```

Choose replacement IDs from an explicit, defensible **training-only** cohort.
The CLI deliberately does not infer replacement level from the players being
rated. Evaluation fits on earlier matches and evaluates later matches; it purges
entire series that straddle the split. The JSON includes
probability metrics and baseline comparisons. Use `--series-map` with a JSON
match-ID-to-series-ID mapping if series labels are stored separately.

The model uses pregame player lineups to predict the final winner. Postgame shots,
goals, saves, and boost are not predictors. Replacement impact changes the focal
player's coefficient to the mean of the specified replacement pool, with teammates
and opponents fixed. Its units are expected wins on the supplied schedule.
It remains an **experimental conditional association**, with identifiability and
uncertainty warnings, not a proven causal measure.

Do not rank a real population from this model until it has been evaluated on
representative future data. Synthetic tests cannot resolve teammate confounding,
upload bias, skill drift, rank imbalance, or a poor replacement definition.

## CSV migration and old scripts

The old positional CSV format and old function APIs are intentionally unsupported.
Named CSV import uses semicolons by default (the legacy export separator).
Every root script and helper now provides migration guidance when run and does
nothing on import. Old `calculate_war` formulas, Liquipedia identity inference,
destructive cleanup, and automatic spreadsheet writes are retired.

Use `python -m rlanalytics import-csv --input match.csv --metadata match.json
--output data/match.jsonl` for an explicit named-column import. Supply
`--columns columns.json` when headers differ from the canonical field names.
Metadata must describe a single match: full match/replay identity, playlist,
UTC date, duration, and team scores. Each row must identify a full platform player
ID and team. See `rlanalytics/data.py` for the validated schema and importer
contract. Files lacking this information cannot be made reliable by guessing.

JSON and CSV reports replace automatic Google Sheets publication. They can be
reviewed and imported into a spreadsheet explicitly; the package does not contact
a spreadsheet service.

## Data and reproducibility

- `matches.jsonl`: validated schema-versioned matches; duplicate replay copies
  merge only when their normalized match contents agree.
- `raw/*.json`: preserved provider responses.
- `manifest.json`: provenance, sampling configuration, content checksums,
  failures, and completion status.
- `reference.json`: saved descriptive cohort statistics and settings.
- `model.json`: experimental fitted coefficients, replacement pool, diagnostics.
- `evaluation.json`: held-out probability scores and baselines.

No real replay corpus is included. The branch's tests and demo are offline.
Live API behavior, real-cohort calibration, and empirical replacement validity
still require a fresh authorized collection and a documented study.
