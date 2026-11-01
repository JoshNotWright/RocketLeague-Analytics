#!/usr/bin/env bash
# Forced command for the box's key on the lab (see lab_setup.sh): the box may run the SFTP
# subsystem (rclone) or the validator, nothing else. SSH_ORIGINAL_COMMAND is what the box asked for.
set -euo pipefail
cmd="${SSH_ORIGINAL_COMMAND:-}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
case "$cmd" in
    "internal-sftp"|"/usr/lib/openssh/sftp-server"|"/usr/libexec/openssh/sftp-server"|"sftp-server"|"/usr/lib/ssh/sftp-server")
        exec /usr/lib/openssh/sftp-server ;;
    "python3 $REPO/scripts/lab_validate.py "*)
        # shellcheck disable=SC2086
        set -- $cmd
        shift 2
        exec python3 "$REPO/scripts/lab_validate.py" "$@" ;;
    *)
        echo "refused: $cmd" >&2
        exit 1 ;;
esac
