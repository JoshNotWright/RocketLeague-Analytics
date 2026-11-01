#!/usr/bin/env bash
# Print the crawler's progress. By default reads the status file the box pushes to the lab every
# cycle (no connection needed); --live asks the box over the tailnet. Runs on the lab.
# Usage: scripts/crawler_status.sh [--live] [--name rl-crawler]
# shellcheck source=crawler_lib.sh
. "$(dirname "$0")/crawler_lib.sh"
require LAB_ROOT

LIVE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --live) LIVE=1; shift ;;
        --name) CRAWLER_NAME="$2"; shift 2 ;;
        *) die "unknown argument $1" ;;
    esac
done

if [[ $LIVE -eq 1 ]]; then
    need_cmd ssh
    box_ssh 'systemctl is-active rlreplay-crawler' || true
    box_ssh "$BOX_BATCH status"
    exit 0
fi

STATUS="$LAB_ROOT/boxes/$CRAWLER_NAME/status.json"
[[ -f "$STATUS" ]] || die "no status yet at $STATUS (is the box up?)"
if command -v hcloud >/dev/null 2>&1 && [[ -n "${HCLOUD_TOKEN:-}" ]]; then
    if server_exists; then echo "server: $CRAWLER_NAME exists"; else echo "server: $CRAWLER_NAME not running"; fi
fi
python3 - "$STATUS" <<'PY'
import json, sys, time
s = json.load(open(sys.argv[1]))
print(f"box {s['box']}  phase: {s['phase']}  command: {s['command']}  updated {s['updated']}")
print(f"saved this run {s['saved_this_run']}  ({s['per_minute']}/min)  current batch {s['current']['count']}/{s['batch_size']}"
      f"  failed decodes {s['current']['failed']}")
d = s["disk"]; print(f"disk free {d['free']/1e9:.0f} GB of {d['total']/1e9:.0f} GB  unvalidated batches: {s['unvalidated'] or 'none'}")
for b in s["sealed"]:
    mark = "ok" if b["validated"] else ("BAD" if b["bad"] else "pending")
    print(f"  batch {b['name']}  {b['count']:>6} replays  {b['bytes']/1e9:5.2f} GB  {mark}  attempts {b['attempts']}"
          f"{'  cleaned' if b['cleaned'] else ''}")
done = sum(w["done"] for w in s["windows"]); complete = sum(w["listing_complete"] for w in s["windows"])
print(f"windows {complete}/{len(s['windows'])} complete, {done} replays in the ledger")
for w in s["windows"]:
    if w["done"] or not w["listing_complete"]:
        print(f"  {w['key']:<40} {w['done']:>7} {'complete' if w['listing_complete'] else ''}")
print(s["crawler"])
PY
