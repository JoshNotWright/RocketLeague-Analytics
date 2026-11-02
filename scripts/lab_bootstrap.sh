#!/usr/bin/env bash
# Lab bootstrap: one command from a bare Ubuntu/Debian box (Tailscale already joined) to a
# running lab UI. Installs packages and the hcloud CLI, clones or updates the repository,
# runs scripts/lab_setup.sh, writes any secrets you passed into /etc/rlreplay/lab.env,
# registers the lab's SSH key with Hetzner when it can, and starts the UI. Safe to re-run:
# a second run updates the checkout and restarts the UI.
#
#   curl -fsSL https://raw.githubusercontent.com/JoshNotWright/RocketLeague-Analytics/v2-replay-features/scripts/lab_bootstrap.sh | sudo bash
#
# Environment (all optional):
#   GH_TOKEN             only if the repository is ever made private again: a fine-grained
#                        token, Contents read-only. Stored only in lab.env as GITHUB_TOKEN;
#                        git reads it from there through a credential helper.
#   HCLOUD_TOKEN, BALLCHASING_API_KEY, TS_AUTHKEY
#                        written into lab.env if given; otherwise fill the file afterwards.
#   RL_REPO_URL          default https://github.com/JoshNotWright/RocketLeague-Analytics.git
#   RL_BRANCH            default v2-replay-features
#   RL_DEST              default /opt/rlreplay
#   RL_ROOT              the data tree. Default: /media/rocketleague/rl when that NAS mount is
#                        present, else /tank/rl (a local ZFS/btrfs mirror).
#   RL_USER              default rlreplay
#   RL_LOCK_SSHD=1       also bind sshd to the tailnet IP and disable password login.
#                        Off by default: it would cut a session that came in over the LAN.
set -euo pipefail

REPO_URL="${RL_REPO_URL:-https://github.com/JoshNotWright/RocketLeague-Analytics.git}"
BRANCH="${RL_BRANCH:-v2-replay-features}"
DEST="${RL_DEST:-/opt/rlreplay}"
if [[ -n "${RL_ROOT:-}" ]]; then ROOT="$RL_ROOT"
elif mountpoint -q /media/rocketleague 2>/dev/null; then ROOT=/media/rocketleague/rl
else ROOT=/tank/rl; fi
USER_NAME="${RL_USER:-rlreplay}"
ENV_FILE=/etc/rlreplay/lab.env

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root (sudo)"
[[ -r /etc/os-release ]] && . /etc/os-release
case "${ID:-}:${ID_LIKE:-}" in
    *debian*|*ubuntu*) ;;
    *) die "this script expects Ubuntu or Debian (found ${PRETTY_NAME:-unknown})" ;;
esac
export DEBIAN_FRONTEND=noninteractive

# ---------------------------------------------------------------- packages
say "packages"
apt-get update -qq
apt-get install -y -qq git python3 python3-venv rclone curl ca-certificates openssh-server >/dev/null

if ! command -v tailscale >/dev/null; then
    say "tailscale (not installed yet)"
    curl -fsSL https://tailscale.com/install.sh | sh
fi
TS_IP="$(tailscale ip -4 2>/dev/null | head -n1 || true)"
if [[ -z "$TS_IP" ]]; then
    warn "this machine is not on a tailnet yet. Run 'tailscale up --ssh=false', then re-run this script."
    warn "continuing; LAB_TS_HOST and LAB_UI_BIND in $ENV_FILE will need filling by hand."
fi

if ! command -v hcloud >/dev/null; then
    say "hcloud CLI"
    curl -fsSL https://github.com/hetznercloud/cli/releases/latest/download/hcloud-linux-amd64.tar.gz \
        | tar -xz -C /usr/local/bin hcloud
    chmod 755 /usr/local/bin/hcloud
fi

# ---------------------------------------------------------------- secrets file (early: git needs it)
say "secrets file"
install -d -m 750 /etc/rlreplay
if [[ ! -f "$ENV_FILE" ]]; then
    # lab_setup.sh copies the full template later; until then a stub holds what git needs.
    install -m 640 /dev/null "$ENV_FILE"
fi
set_env() {  # set_env KEY VALUE : replace or append KEY=VALUE in the env file, never echoing the value
    local key="$1" val="$2" tmp
    tmp="$(mktemp)"
    { grep -v "^${key}=" "$ENV_FILE" || true; printf '%s=%s\n' "$key" "$val"; } >"$tmp"
    cat "$tmp" >"$ENV_FILE"; rm -f "$tmp"
}
[[ -n "${GH_TOKEN:-}" ]] && set_env GITHUB_TOKEN "$GH_TOKEN"
[[ -n "${HCLOUD_TOKEN:-}" ]] && set_env HCLOUD_TOKEN "$HCLOUD_TOKEN"
[[ -n "${BALLCHASING_API_KEY:-}" ]] && set_env BALLCHASING_API_KEY "$BALLCHASING_API_KEY"
[[ -n "${TS_AUTHKEY:-}" ]] && set_env TS_AUTHKEY "$TS_AUTHKEY"

# git takes the GitHub token from lab.env through a credential helper, so the token is in
# exactly one place and 'git pull' keeps working on the lab after this script is gone.
if [[ "$REPO_URL" == https://github.com/* ]]; then
    git config --system credential.https://github.com.useHttpPath false
    git config --system credential.https://github.com.helper \
        '!f(){ t=$(sed -n "s/^GITHUB_TOKEN=//p" /etc/rlreplay/lab.env | tail -n1); [ -n "$t" ] || exit 0; echo username=x-access-token; echo "password=$t"; }; f'
fi

# ---------------------------------------------------------------- repository
say "repository -> $DEST ($BRANCH)"
if [[ -d "$DEST/.git" ]]; then
    git -C "$DEST" remote set-url origin "$REPO_URL"
    git -C "$DEST" fetch -q origin "$BRANCH"
    if ! git -C "$DEST" diff --quiet || ! git -C "$DEST" diff --cached --quiet; then
        die "$DEST has local changes; commit or stash them, then re-run"
    fi
    git -C "$DEST" checkout -q "$BRANCH"
    git -C "$DEST" reset -q --hard "origin/$BRANCH"
else
    git clone -q --branch "$BRANCH" "$REPO_URL" "$DEST" \
        || die "clone failed. Private repository? pass GH_TOKEN=<fine-grained token, Contents read-only>"
fi
git config --system --add safe.directory "$DEST"
printf '   at %s\n' "$(git -C "$DEST" log -1 --format='%h %s')"

# ---------------------------------------------------------------- lab_setup.sh
say "lab setup (user $USER_NAME, tree $ROOT)"
if [[ ! -d "$ROOT" ]]; then
    parent="$(dirname "$ROOT")"
    if ! mountpoint -q "$parent" 2>/dev/null && [[ "$parent" != "/" ]]; then
        warn "$parent is not a mountpoint; $ROOT will live on the root filesystem."
        warn "If the archive belongs on the NAS or a mirror, mount it first (docs/DEPLOYMENT.md 1.1) or pass RL_ROOT."
    fi
fi
# lab_setup.sh only writes the template when lab.env is absent; our stub must not block it.
if [[ -f "$ENV_FILE" ]] && ! grep -q '^LAB_ROOT=' "$ENV_FILE"; then
    stub="$(cat "$ENV_FILE")"
    rm -f "$ENV_FILE"
    bash "$DEST/scripts/lab_setup.sh" --root "$ROOT" --user "$USER_NAME" >/tmp/lab_setup.out
    while IFS='=' read -r k v; do [[ -n "$k" ]] && set_env "$k" "$v"; done <<<"$stub"
else
    bash "$DEST/scripts/lab_setup.sh" --root "$ROOT" --user "$USER_NAME" >/tmp/lab_setup.out
fi
chown "root:$USER_NAME" "$ENV_FILE"; chmod 640 "$ENV_FILE"
set_env LAB_REPO "$DEST"
[[ -n "$TS_IP" ]] && { set_env LAB_TS_HOST "$TS_IP"; set_env LAB_UI_BIND "$TS_IP"; }

# ---------------------------------------------------------------- hetzner ssh key
HC="$(sed -n 's/^HCLOUD_TOKEN=//p' "$ENV_FILE" | tail -n1)"
KEY_NAME="$(sed -n 's/^HCLOUD_SSH_KEY=//p' "$ENV_FILE" | tail -n1)"; KEY_NAME="${KEY_NAME:-rl-lab}"
if [[ -n "$HC" ]]; then
    say "hetzner: ssh key '$KEY_NAME'"
    if HCLOUD_TOKEN="$HC" hcloud ssh-key describe "$KEY_NAME" >/dev/null 2>&1; then
        echo "   already registered"
    else
        HCLOUD_TOKEN="$HC" hcloud ssh-key create --name "$KEY_NAME" \
            --public-key-from-file /root/.ssh/id_ed25519.pub >/dev/null && echo "   registered"
    fi
fi

# ---------------------------------------------------------------- sshd (opt-in)
if [[ "${RL_LOCK_SSHD:-0}" == 1 && -n "$TS_IP" ]]; then
    say "sshd: tailnet only"
    install -m 644 /dev/stdin /etc/ssh/sshd_config.d/10-rlreplay-tailnet.conf <<EOF
ListenAddress $TS_IP
PasswordAuthentication no
EOF
    sshd -t && systemctl restart ssh
fi

# ---------------------------------------------------------------- UI
say "lab UI"
systemctl daemon-reload
systemctl enable -q --now rlreplay-lab-ui
systemctl restart rlreplay-lab-ui
sleep 1
systemctl is-active --quiet rlreplay-lab-ui || { journalctl -u rlreplay-lab-ui -n 20 --no-pager; die "UI did not start"; }
PORT="$(sed -n 's/^LAB_UI_PORT=//p' "$ENV_FILE" | tail -n1)"; PORT="${PORT:-8731}"

# ---------------------------------------------------------------- what is left
missing=()
for k in HCLOUD_TOKEN BALLCHASING_API_KEY TS_AUTHKEY; do
    grep -q "^${k}=.\+" "$ENV_FILE" || missing+=("$k")
done
cat <<EOF

lab bootstrap done.
  repository:  $DEST  ($BRANCH)      re-run this script to update
  data tree:   $ROOT
  secrets:     $ENV_FILE  (root:$USER_NAME 640)
  UI:          http://${TS_IP:-<tailnet ip>}:$PORT/   from any device on your tailnet
EOF
if ((${#missing[@]})); then
    echo "  still empty in $ENV_FILE: ${missing[*]}"
    echo "    sudo nano $ENV_FILE && sudo systemctl restart rlreplay-lab-ui"
    [[ " ${missing[*]} " == *" HCLOUD_TOKEN "* ]] && echo "    then re-run this script once so the lab's SSH key is registered with Hetzner"
fi
[[ "${RL_LOCK_SSHD:-0}" == 1 ]] || echo "  sshd still listens on every interface; when you are connected over Tailscale, re-run with RL_LOCK_SSHD=1"
echo "  next: docs/DEPLOYMENT.md section 2 (build the crawler image), then 3 (smoke test)"
