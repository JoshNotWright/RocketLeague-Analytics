#!/usr/bin/env bash
# Create the crawler box from the snapshot, hand it its secrets over SSH, join it to the tailnet,
# close its public port, and start the service. Idempotent: on an existing server it only makes
# sure the service is running. If any step after the server is created fails, the server and its
# boot firewall are deleted again (nothing of value is on a box that never started crawling);
# --keep-on-failure leaves it for inspection. Runs on the lab.
# Usage: scripts/crawler_up.sh [--job <id>] [--type cpx32] [--name rl-crawler] [--keep-on-failure]
# --job takes the crawl parameters from <LAB_ROOT>/jobs/<id>.json (the UI's jobs) instead of lab.env.
# shellcheck source=crawler_lib.sh
. "$(dirname "$0")/crawler_lib.sh"
need_cmd hcloud ssh curl
require HCLOUD_TOKEN BALLCHASING_API_KEY TS_AUTHKEY LAB_ROOT LAB_USER LAB_TS_HOST CRAWLER_KEY HCLOUD_SSH_KEY

while [[ $# -gt 0 ]]; do
    case "$1" in
        --type) CRAWLER_TYPE="$2"; shift 2 ;;
        --name) CRAWLER_NAME="$2"; shift 2 ;;
        --keep-on-failure) KEEP_ON_FAILURE=1; shift ;;
        --job) JOB_ID="$2"; shift 2 ;;
        *) die "unknown argument $1" ;;
    esac
done
[[ -r "$CRAWLER_KEY" ]] || die "$CRAWLER_KEY is not readable (run scripts/lab_setup.sh)"
[[ -n "${JOB_ID:-}" ]] && load_job "$JOB_ID"

mkdir -p "$LAB_ROOT/boxes/$CRAWLER_NAME" "$LAB_ROOT/batches" "$LAB_ROOT/meta" "$LAB_ROOT/decoded"
set_control run

if server_exists; then
    [[ -z "${JOB_ID:-}" ]] || die "$CRAWLER_NAME already exists and may be running another job; stop it first"
    log "$CRAWLER_NAME exists; making sure the service runs"
    box_ssh 'systemctl enable --now rlreplay-crawler && systemctl is-active rlreplay-crawler'
    exit 0
fi

IMAGE="$(latest_image_id)"
[[ -n "$IMAGE" ]] || die "no snapshot with label $IMAGE_LABEL; run scripts/crawler_image.sh first"
FW="${CRAWLER_NAME}-boot"
MYIP="$(my_public_ip)"
# The boot firewall is throwaway: a previous run may have left it behind with a rule from an
# older public IP, so start from a clean one (deleting fails only if a server still uses it).
if hcloud firewall describe "$FW" >/dev/null 2>&1; then
    hcloud firewall delete "$FW" >/dev/null 2>&1         || die "firewall $FW is still attached to a server; run scripts/crawler_down.sh --force first"
fi
hcloud firewall create --name "$FW" >/dev/null
log "allowing ssh from $MYIP only, for bootstrap"
hcloud firewall add-rule "$FW" --direction in --protocol tcp --port 22 --source-ips "${MYIP}/32" >/dev/null

STARTED=0
cleanup_on_failure() {
    local rc=$?
    [[ $rc -ne 0 && $STARTED -eq 0 ]] || return 0
    if [[ "${KEEP_ON_FAILURE:-0}" == 1 ]]; then
        log "bring-up failed, exit $rc; $CRAWLER_NAME kept for inspection, port 22 still open from $MYIP"
        return 0
    fi
    log "bring-up failed, exit $rc; deleting $CRAWLER_NAME and its boot firewall"
    box_tailscale_logout
    [[ -n "${IP:-}" ]] && bootstrap_ssh "$IP" 'tailscale logout' >/dev/null 2>&1 || true
    hcloud server delete "$CRAWLER_NAME" >/dev/null 2>&1 || true
    hcloud firewall delete "$FW" >/dev/null 2>&1 || true
    set_control stop 2>/dev/null || true
}
trap cleanup_on_failure EXIT

log "creating $CRAWLER_NAME, $CRAWLER_TYPE in $CRAWLER_LOCATION, from image $IMAGE"
hcloud server create --name "$CRAWLER_NAME" --type "$CRAWLER_TYPE" --image "$IMAGE" --location "$CRAWLER_LOCATION" \
    --ssh-key "$HCLOUD_SSH_KEY" --firewall "$FW" --label role=rl-crawler >/dev/null
IP="$(server_public_ip)"
wait_for_ssh "$IP"

log "writing secrets to /etc/rlreplay over ssh"
bootstrap_ssh "$IP" 'umask 077 && mkdir -p /etc/rlreplay && cat > /etc/rlreplay/env' <<EOF
BALLCHASING_API_KEY=$BALLCHASING_API_KEY
EOF
printf '%s' "$TS_AUTHKEY" | bootstrap_ssh "$IP" 'umask 077 && cat > /etc/rlreplay/ts_authkey'
bootstrap_ssh "$IP" 'umask 077 && cat > /etc/rlreplay/lab_key' <"$CRAWLER_KEY"
bootstrap_ssh "$IP" 'umask 077 && cat > /etc/rlreplay/rclone.conf' <<EOF
[lab]
type = sftp
host = $LAB_TS_HOST
user = $LAB_USER
key_file = /etc/rlreplay/lab_key
shell_type = none
EOF
render_crawler_config | bootstrap_ssh "$IP" 'umask 077 && cat > /etc/rlreplay/crawler.json'

log "joining the tailnet as ephemeral node $CRAWLER_NAME"
bootstrap_ssh "$IP" "systemctl enable --now tailscaled && tailscale up --auth-key file:/etc/rlreplay/ts_authkey --hostname '$CRAWLER_NAME' --accept-dns=true && shred -u /etc/rlreplay/ts_authkey"
BOX_TS_IP="$(bootstrap_ssh "$IP" 'tailscale ip -4' | head -n1)"
[[ -n "$BOX_TS_IP" ]] || die "the box joined but reports no tailnet address"
mkdir -p "$LAB_ROOT/boxes/$CRAWLER_NAME"
printf '%s
' "$BOX_TS_IP" >"$(box_ip_file)"
log "tailnet address $BOX_TS_IP"
for ((i = 0; i < 24; i++)); do
    if box_ssh true 2>/dev/null; then break; fi
    sleep 5
done
box_ssh true || die "cannot reach $CRAWLER_NAME over the tailnet"

log "closing the public port"
hcloud firewall delete-rule "$FW" --direction in --protocol tcp --port 22 --source-ips "${MYIP}/32" >/dev/null

log "pulling the ledger and starting the service"
box_ssh "$BOX_BATCH pull-ledger"
box_ssh 'systemctl enable --now rlreplay-crawler && systemctl is-active rlreplay-crawler'
STARTED=1
log "$CRAWLER_NAME is crawling; status: scripts/crawler_status.sh"
