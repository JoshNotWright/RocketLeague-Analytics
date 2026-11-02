#!/usr/bin/env bash
# Build the model tables from the archive on the lab (lab/build.py) in the lab's venv. Launched
# by the UI for a "build" job, or by hand:  scripts/lab_build.sh --tables frame,touches [--force]
# The venv is created by lab_bootstrap.sh (python3 -m venv .venv + requirements-v2.txt).
# shellcheck source=crawler_lib.sh
. "$(dirname "$0")/crawler_lib.sh"
require LAB_ROOT LAB_REPO
PY="$LAB_REPO/.venv/bin/python"
[[ -x "$PY" ]] || die "no venv at $LAB_REPO/.venv; re-run scripts/lab_bootstrap.sh"
cd "$LAB_REPO"
exec "$PY" -m lab.build --root "$LAB_ROOT" "$@"
