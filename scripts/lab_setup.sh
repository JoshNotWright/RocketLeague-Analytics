#!/usr/bin/env bash
# One-time lab setup (run as root on the lab): the push user, the data tree, the key the box uses
# to reach the lab, the lab's own key registered with Hetzner, and the env file skeleton. Safe to
# re-run. Secrets are never written by this script; you fill /etc/rlreplay/lab.env yourself.
# Usage: scripts/lab_setup.sh [--root /tank/rl] [--user rlreplay]
set -euo pipefail
ROOT=/tank/rl; USER_NAME=rlreplay; REPO="$(cd "$(dirname "$0")/.." && pwd)"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --root) ROOT="$2"; shift 2 ;;
        --user) USER_NAME="$2"; shift 2 ;;
        *) echo "unknown argument $1" >&2; exit 1 ;;
    esac
done
[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }
for c in ssh-keygen tailscale rclone python3; do
    command -v "$c" >/dev/null || { echo "$c is not installed" >&2; exit 1; }
done

id -u "$USER_NAME" >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash "$USER_NAME"
mkdir -p "$ROOT"/{decoded,meta,batches,boxes,features}
# On a NAS mount (NFS/SMB) ownership and modes are set by the mount options, not by us; a
# refused chown is fine as long as the push user can write. That is what is tested below.
FSTYPE="$(stat -f -c %T "$ROOT" 2>/dev/null || echo unknown)"
if ! chown -R "$USER_NAME:$USER_NAME" "$ROOT" 2>/dev/null || ! chmod 750 "$ROOT" 2>/dev/null; then
    echo "note: could not chown/chmod $ROOT ($FSTYPE); relying on the mount's own uid/gid mapping" >&2
fi
if ! sudo -u "$USER_NAME" sh -c "touch '$ROOT/batches/.write_test' && rm -f '$ROOT/batches/.write_test'" 2>/dev/null; then
    cat >&2 <<EOF
$USER_NAME cannot write to $ROOT ($FSTYPE).
  NFS: export the share with the uid of $USER_NAME ($(id -u "$USER_NAME")) allowed, or all_squash,anonuid=$(id -u "$USER_NAME"),anongid=$(id -g "$USER_NAME")
  SMB: mount with uid=$USER_NAME,gid=$USER_NAME (in /etc/fstab, with _netdev)
then re-run this script.
EOF
    exit 1
fi
# The UI (and the box's SFTP pushes) must not start before a network mount is there.
if mountpoint -q "$ROOT" || mountpoint -q "$(dirname "$ROOT")"; then
    install -d /etc/systemd/system/rlreplay-lab-ui.service.d
    printf '[Unit]\nRequiresMountsFor=%s\n' "$ROOT" >/etc/systemd/system/rlreplay-lab-ui.service.d/mount.conf
fi

mkdir -p /etc/rlreplay; chmod 750 /etc/rlreplay; chgrp "$USER_NAME" /etc/rlreplay
if [[ ! -f /etc/rlreplay/crawler_key ]]; then
    ssh-keygen -t ed25519 -N "" -C "rl-crawler box -> lab" -f /etc/rlreplay/crawler_key >/dev/null
    chmod 600 /etc/rlreplay/crawler_key
fi
# The box may only SFTP into the tree and run the validator: no shell, no forwarding, tailnet only.
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" "$HOME_DIR/.ssh"
AUTH="$HOME_DIR/.ssh/authorized_keys"
touch "$AUTH"; chown "$USER_NAME:$USER_NAME" "$AUTH"; chmod 600 "$AUTH"
PUB="$(cat /etc/rlreplay/crawler_key.pub)"
if ! grep -qF "$PUB" "$AUTH"; then
    echo "from=\"100.64.0.0/10\",no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-pty,command=\"$REPO/scripts/lab_cmd.sh\" $PUB" >>"$AUTH"
fi
install -m 755 "$REPO/scripts/lab_cmd.sh" "$REPO/scripts/lab_cmd.sh" 2>/dev/null || true

if [[ ! -f /root/.ssh/id_ed25519 ]]; then
    ssh-keygen -t ed25519 -N "" -C "rl-lab" -f /root/.ssh/id_ed25519 >/dev/null
fi
if [[ ! -f /etc/rlreplay/lab.env ]]; then
    install -m 640 -o root -g "$USER_NAME" "$REPO/deploy/lab.env.example" /etc/rlreplay/lab.env
    sed -i "s#^LAB_ROOT=.*#LAB_ROOT=$ROOT#; s#^LAB_USER=.*#LAB_USER=$USER_NAME#; s#^LAB_REPO=.*#LAB_REPO=$REPO#" /etc/rlreplay/lab.env
    TS_IP="$(tailscale ip -4 2>/dev/null | head -n1 || true)"
    [[ -n "$TS_IP" ]] && sed -i "s#^LAB_TS_HOST=.*#LAB_TS_HOST=$TS_IP#; s#^LAB_UI_BIND=.*#LAB_UI_BIND=$TS_IP#" /etc/rlreplay/lab.env
fi
install -m 644 "$REPO/deploy/rlreplay-lab-ui.service" /etc/systemd/system/rlreplay-lab-ui.service
systemctl daemon-reload

cat <<EOF
lab setup done.
  data tree:        $ROOT  (owner $USER_NAME)
  box -> lab key:   /etc/rlreplay/crawler_key  (public key restricted to sftp + validate in $AUTH)
  lab -> box key:   /root/.ssh/id_ed25519.pub  -> register once:  hcloud ssh-key create --name rl-lab --public-key-from-file /root/.ssh/id_ed25519.pub
  env file:         /etc/rlreplay/lab.env  (fill HCLOUD_TOKEN, BALLCHASING_API_KEY, TS_AUTHKEY; nothing else holds them)
  sshd:             bind it to the tailnet only (ListenAddress $(tailscale ip -4 2>/dev/null | head -n1 || echo '<tailnet ip>')) and keep PasswordAuthentication no
  UI:               systemctl enable --now rlreplay-lab-ui   (listens on LAB_UI_BIND:LAB_UI_PORT from lab.env)
EOF
