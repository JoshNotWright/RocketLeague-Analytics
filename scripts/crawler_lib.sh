#!/usr/bin/env bash
# Shared by crawler_image.sh / crawler_up.sh / crawler_down.sh / crawler_status.sh. Runs on the lab.
# Sourcing this loads /etc/rlreplay/lab.env (or $RLREPLAY_LAB_ENV) and defines helpers. Secrets are
# only ever exported into this process and piped over SSH stdin; nothing here echoes them, and
# `set -x` is never used.
set -euo pipefail

LAB_ENV="${RLREPLAY_LAB_ENV:-/etc/rlreplay/lab.env}"
if [[ -f "$LAB_ENV" ]]; then
    set -a
    # shellcheck disable=SC1090
    . "$LAB_ENV"
    set +a
fi

: "${CRAWLER_NAME:=rl-crawler}"
: "${CRAWLER_TYPE:=cpx32}"
: "${CRAWLER_LOCATION:=fsn1}"
: "${CRAWLER_BATCH:=20000}"
: "${CRAWLER_WORKERS:=4}"
: "${CRAWLER_PLAYLIST:=ranked-doubles}"
: "${CRAWLER_BANDS:=diamond:diamond-1:diamond-3,champion:champion-1:champion-3,gc:grand-champion-1:grand-champion-3}"
: "${CRAWLER_MONTHS:=2025-07..2026-10}"
: "${CRAWLER_PER_WINDOW:=}"
: "${LAB_REPO:=/opt/rlreplay}"
: "${LAB_ROOT:=/tank/rl}"
: "${LAB_USER:=rlreplay}"
: "${CRAWLER_KEY:=/etc/rlreplay/crawler_key}"
: "${HCLOUD_SSH_KEY:=rl-lab}"
: "${IMAGE_LABEL:=role=rl-crawler-image}"

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
die() { log "error: $*"; exit 1; }

require() {
    local name
    for name in "$@"; do
        [[ -n "${!name:-}" ]] || die "$name is not set (see deploy/lab.env.example)"
    done
}

need_cmd() {
    local c
    for c in "$@"; do
        command -v "$c" >/dev/null 2>&1 || die "$c is not installed"
    done
}

# ssh to the box over the tailnet. crawler_up.sh records the box's tailnet IP in
# $LAB_ROOT/boxes/<name>/tailnet_ip; the MagicDNS name is only a fallback, because a deleted
# ephemeral node can linger for a while and the new box then comes up as <name>-1. Host keys of
# ephemeral boxes change every time, so they are accepted on first use and not pinned.
box_ip_file() { echo "$LAB_ROOT/boxes/$CRAWLER_NAME/tailnet_ip"; }
box_host() {
    local f; f="$(box_ip_file)"
    if [[ -s "$f" ]]; then tr -d '[:space:]' <"$f"; else echo "$CRAWLER_NAME"; fi
}
box_ssh() {
    ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR         -o ConnectTimeout=15 "root@$(box_host)" "$@"
}
# Leave the tailnet properly so the ephemeral node disappears at once; best effort.
box_tailscale_logout() {
    box_ssh 'tailscale logout' >/dev/null 2>&1 || true
    rm -f "$(box_ip_file)"
}

# ssh to a freshly created box by public IP, before it is on the tailnet.
bootstrap_ssh() {
    local ip="$1"; shift
    ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "root@${ip}" "$@"
}

wait_for_ssh() {
    local ip="$1" tries="${2:-60}"
    local i
    for ((i = 0; i < tries; i++)); do
        if bootstrap_ssh "$ip" true 2>/dev/null; then return 0; fi
        sleep 5
    done
    die "ssh to $ip did not come up"
}

server_exists() { hcloud server describe "$CRAWLER_NAME" >/dev/null 2>&1; }

server_public_ip() { hcloud server ip "$CRAWLER_NAME"; }

latest_image_id() {
    hcloud image list --type snapshot --selector "$IMAGE_LABEL" -o noheader -o columns=id,created \
        | sort -k2 -r | head -n1 | awk '{print $1}'
}

my_public_ip() { curl -fsS https://api.ipify.org || curl -fsS https://ifconfig.me; }

# The box-side command that runs crawler_batch.py with its config and rclone settings.
BOX_BATCH='cd /opt/rlreplay && RLREPLAY_CRAWLER_CONFIG=/etc/rlreplay/crawler.json RCLONE_CONFIG=/etc/rlreplay/rclone.conf .venv/bin/python scripts/crawler_batch.py'

set_control() {
    local command="$1"
    mkdir -p "$LAB_ROOT/boxes/$CRAWLER_NAME"
    printf '{"command": "%s", "set_at": "%s"}\n' "$command" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        >"$LAB_ROOT/boxes/$CRAWLER_NAME/control.json"
}

# Render /etc/rlreplay/crawler.json for the box from the lab.env settings.
# rclone flags: the lab tree may sit on an SMB mount, where a just-written file can report size
# 0 and a just-deleted one refuses re-creation; so no post-copy size check (the lab validates
# every decoded file by sha256), no modtime setstat, and no delete-and-retry on the same path.
render_crawler_config() {
    local first="${CRAWLER_MONTHS%%..*}" last="${CRAWLER_MONTHS##*..}"
    local bands_json="" entry name min max entries
    IFS=',' read -ra entries <<<"$CRAWLER_BANDS"
    for entry in "${entries[@]}"; do
        IFS=':' read -r name min max <<<"$entry"
        bands_json+="{\"name\": \"$name\", \"min\": \"$min\", \"max\": \"$max\"},"
    done
    bands_json="[${bands_json%,}]"
    local per_window="null"
    [[ -n "$CRAWLER_PER_WINDOW" ]] && per_window="$CRAWLER_PER_WINDOW"
    cat <<EOF
{
  "box": "$CRAWLER_NAME",
  "work": "/var/lib/rlreplay",
  "lab": {
    "remote": "lab:$LAB_ROOT",
    "root": "$LAB_ROOT",
    "ssh": "$LAB_USER@$LAB_TS_HOST",
    "validate": "python3 $LAB_REPO/scripts/lab_validate.py"
  },
  "playlist": "$CRAWLER_PLAYLIST",
  "bands": $bands_json,
  "months": {"first": "$first", "last": "$last"},
  "per_window": $per_window,
  "batch_size": $CRAWLER_BATCH,
  "workers": $CRAWLER_WORKERS,
  "tier": "gc",
  "max_unvalidated": 4,
  "min_free_gb": 25,
  "rclone": "rclone --config /etc/rlreplay/rclone.conf --ignore-size --sftp-set-modtime=false --retries 1",
  "ssh": "ssh -i /etc/rlreplay/lab_key -o StrictHostKeyChecking=accept-new"
}
EOF
}
