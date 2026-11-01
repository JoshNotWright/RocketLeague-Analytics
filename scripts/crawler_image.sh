#!/usr/bin/env bash
# Build the crawler snapshot once (rebuild when the pipeline changes): a throwaway server gets
# this repository, a venv, the Rust decoder, rclone and tailscale, then is snapshotted and deleted.
# No secret touches the image. Runs on the lab. Usage: scripts/crawler_image.sh [server-type]
# shellcheck source=crawler_lib.sh
. "$(dirname "$0")/crawler_lib.sh"
need_cmd hcloud rsync ssh
require HCLOUD_TOKEN HCLOUD_SSH_KEY

BUILD_NAME="${CRAWLER_NAME}-image-build"
BUILD_TYPE="${1:-cpx22}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cleanup() {
    if hcloud server describe "$BUILD_NAME" >/dev/null 2>&1; then
        log "deleting build server $BUILD_NAME"
        hcloud server delete "$BUILD_NAME" >/dev/null
    fi
}
trap cleanup EXIT

log "creating build server $BUILD_NAME ($BUILD_TYPE, ubuntu-24.04, $CRAWLER_LOCATION)"
hcloud server create --name "$BUILD_NAME" --type "$BUILD_TYPE" --image ubuntu-24.04 \
    --location "$CRAWLER_LOCATION" --ssh-key "$HCLOUD_SSH_KEY" --label role=rl-crawler-build >/dev/null
IP="$(hcloud server ip "$BUILD_NAME")"
wait_for_ssh "$IP"

log "installing packages"
bootstrap_ssh "$IP" 'bash -s' <<'REMOTE'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3 python3-venv python3-pip rclone curl build-essential pkg-config rsync
curl -fsSL https://tailscale.com/install.sh | sh
curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal
mkdir -p /opt/rlreplay /var/lib/rlreplay /etc/rlreplay
chmod 700 /etc/rlreplay
REMOTE

log "copying the repository (no data, no secrets, no build output)"
rsync -az --delete -e "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new" \
    --exclude '.git' --exclude 'data' --exclude '.venv' --exclude 'reports' --exclude 'tools' \
    --exclude 'rust/rldecode/target' --exclude '__pycache__' --exclude '*.egg-info' --exclude 'build' \
    --exclude 'demo-output' --exclude '.env*' \
    "$REPO_DIR/" "root@${IP}:/opt/rlreplay/"

log "building the venv and the Rust decoder"
bootstrap_ssh "$IP" 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/rlreplay
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q numpy
# shellcheck disable=SC1091
. "$HOME/.cargo/env"
(cd rust/rldecode && cargo build --release -q)
mkdir -p tools
cp rust/rldecode/target/release/rldecode tools/rldecode
rm -rf rust/rldecode/target "$HOME/.cargo/registry"
install -m 644 deploy/rlreplay-crawler.service /etc/systemd/system/rlreplay-crawler.service
systemctl daemon-reload
systemctl disable rlreplay-crawler >/dev/null 2>&1 || true
# the image must carry no identity: tailscale state, ssh host keys and shell history are regenerated
systemctl stop tailscaled || true
rm -f /var/lib/tailscale/tailscaled.state
rm -f /etc/ssh/ssh_host_*
rm -f /root/.bash_history
apt-get clean
sync
REMOTE

log "snapshotting"
hcloud server poweroff "$BUILD_NAME" >/dev/null
hcloud server create-image --type snapshot --description "rl-crawler $(date -u +%Y-%m-%dT%H:%M)" \
    --label "${IMAGE_LABEL}" "$BUILD_NAME" >/dev/null
log "image ready: $(latest_image_id)"
