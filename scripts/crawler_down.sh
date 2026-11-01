#!/usr/bin/env bash
# Stop the crawler, push and validate everything it holds, and delete the server only when every
# batch has its .ok on the lab. Otherwise it prints what is unvalidated and leaves the server
# running (exit 1). A missing server is a no-op. Runs on the lab.
# Usage: scripts/crawler_down.sh [--name rl-crawler]
# shellcheck source=crawler_lib.sh
. "$(dirname "$0")/crawler_lib.sh"
need_cmd hcloud ssh
require HCLOUD_TOKEN LAB_ROOT

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name) CRAWLER_NAME="$2"; shift 2 ;;
        *) die "unknown argument $1" ;;
    esac
done

if ! server_exists; then
    log "$CRAWLER_NAME does not exist; nothing to do"
    exit 0
fi

set_control stop
log "stopping the service (it seals, pushes and validates on the way out)"
box_ssh 'systemctl stop rlreplay-crawler' || log "service stop failed or the box is unreachable; trying a final push anyway"
log "final push"
if box_ssh "$BOX_BATCH push"; then
    log "everything validated; deleting $CRAWLER_NAME"
    hcloud server delete "$CRAWLER_NAME" >/dev/null
    hcloud firewall delete "${CRAWLER_NAME}-boot" >/dev/null 2>&1 || true
    log "deleted"
    exit 0
fi
log "unvalidated batches remain; the server stays. Re-run once the lab has validated them:"
box_ssh "$BOX_BATCH unvalidated" || true
exit 1
