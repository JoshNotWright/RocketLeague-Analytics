#!/usr/bin/env bash
# Create the crawler box from the snapshot, hand it its secrets over SSH, join it to the tailnet,
# close its public port, and start the service. Idempotent: on an existing server it only makes
# sure the service is running. Runs on the lab. Usage: scripts/crawler_up.sh [--type cpx32] [--name rl-crawler]
# shellcheck source=crawler_lib.sh
. "$(dirname "$0")/crawler_lib.sh"
need_cmd hcloud ssh curl
require HCLOUD_TOKEN BALLCHASING_API_KEY TS_AUTHKEY LAB_ROOT LAB_USER LAB_TS_HOST CRAWLER_KEY HCLOUD_SSH_KEY

while [[ $# -gt 0 ]]; do
    case "$1" in
        --type) CRAWLER_TYPE="$2"; shift 2 ;;
        --name) CRAWLER_NAME="$2"; shift 2 ;;
        *) die "unknown argument $1" ;;
    esac
done
[[ -r "$CRAWLER_KEY" ]] || die "$CRAWLER_KEY is not readable (run scripts/lab_setup.sh)"

mkdir -p "$LAB_ROOT/boxes/$CRAWLER_NAME" "$LAB_ROOT/batches" "$LAB_ROOT/meta" "$LAB_ROOT/decoded"
set_control run

if server_exists; then
    log "$CRAWLER_NAME exists; making sure the service runs"
    box_ssh 'systemctl enable --now rlreplay-crawler && systemctl is-active rlreplay-crawler'
    exit 0
fi

IMAGE="$(latest_image_id)"
[[ -n "$IMAGE" ]] || die "no snapshot with label $IMAGE_LABEL; run scripts/crawler_image.sh first"
FW="${CRAWLER_NAME}-boot"
MYIP="$(my_public_ip)"
if ! hcloud firewall describe "$FW" >/dev/null 2>&1; then
    hcloud firewall create --name "$FW" >/dev/null
fi
log "allowing ssh from $MYIP only, for bootstrap"
hcloud firewall add-rule "$FW" --direction in --protocol tcp --port 22 --source-ips "${MYIP}/32" >/dev/null

log "creating $CRAWLER_NAME ($CRAWLER_TYPE, $CRAWLER_LOCATION) from image $IMAGE"
hcloud server create --name "$CRAWLER_NAME" --type "$CRAWLER_TYPE" --image "$IMAGE" --location "$CRAWLER_LOCATION" \
    --ssh-key "$HCLOUD_SSH_KEY" --firewall "$FW" --label role=rl-crawler >/dev/null
IP="$(server_public_ip)"
wait_for_ssh "$IP"

log "writing secrets to /etc/rlreplay over ssh stdin (root, mode 600)"
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
log "$CRAWLER_NAME is crawling; status: scripts/crawler_status.sh"
