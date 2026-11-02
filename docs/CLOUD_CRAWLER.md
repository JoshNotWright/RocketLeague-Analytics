# Cloud crawler: ephemeral box, lab as the control plane

Implements [HANDOFF_SEEDBOX_PIPELINE.md](HANDOFF_SEEDBOX_PIPELINE.md) with one change agreed
afterwards: the **lab is the control plane**. Every secret lives in one root-only file on the
lab, the up/down scripts run on the lab, and the lab's web UI holds **jobs** (`lab/jobs.py`):
a job is a saved set of crawl parameters with a lifecycle, and starting, pausing, stopping and
continuing one is how boxes come and go. The operator PC only talks to the lab.

```
operator PC  --tailnet-->  lab (UI, secrets, data tree, validator)  <--tailnet-->  Hetzner box (crawl + decode)
                                                                              box --> ballchasing.com (only outbound)
```

## Pieces

| piece | where | what |
| --- | --- | --- |
| `rlreplay/cloud.py`, `scripts/crawler_batch.py` | box | the service loop: crawl a page, decode, seal a batch, push, trigger validation, clean up |
| `scripts/lab_validate.py` | lab | hashes every pushed `.npz`, checks the ledger holds every id, writes `<batch>.ok` or `.bad` |
| `scripts/crawler_up.sh`, `crawler_down.sh`, `crawler_status.sh`, `crawler_image.sh` | lab | server lifecycle via `hcloud`; secrets piped over SSH stdin, never on a command line |
| `scripts/lab_setup.sh`, `scripts/lab_cmd.sh` | lab | one-time setup; the forced command that limits the box's key to SFTP + the validator |
| `lab/server.py`, `lab/static/index.html`, `lab/jobs.py` | lab | the web UI and its jobs (standard library, bound to the tailnet address) |
| `lab/build.py`, `scripts/lab_build.sh`, `rlreplay/fast.py` | lab | build jobs: the model tables from decoded windows, incrementally; the decoded-input extractors |
| `deploy/rlreplay-crawler.service`, `deploy/rlreplay-lab-ui.service`, `deploy/lab.env.example` | | systemd units and the env template |
| `rlreplay/crawl.py --ledger-only`, `--known-from` | box | a metadata line alone counts as done; ids from other folders are never fetched |

## Layouts

Box (`/var/lib/rlreplay`, disposable):

```
crawl/<playlist>/<band>/<yyyy-mm>/   metadata.json, crawl_state.json, <id>.replay, decoded/<id>.npz
batches/<batch>.manifest.json        sealed batches; <batch>.ok / .bad mirrored back from the lab
batches/current.json                 decoded but not yet sealed
status.json, control.json            pushed to / pulled from the lab every cycle
```

Lab (`LAB_ROOT`, the master copy):

```
decoded/<playlist>/<band>/<yyyy-mm>/<id>.npz
meta/<playlist>/<band>/<yyyy-mm>.jsonl          the ledger; losing it is the only way to refetch
meta/<playlist>/<band>/<yyyy-mm>.state.json     the crawl cursor per window
batches/<batch>.manifest.json, .ok, .bad
boxes/<box>/status.json, control.json, tailnet_ip, history.jsonl
jobs/<id>.json                                  a job: name, kind (crawl | build), state, params, runs
builds/<id>.json                                a build job's progress, written after every table
features/<table>/<playlist>/<band>/<month>...   the model tables (frame, touches, decisions/, table)
features/build_state.json                       what each window's tables were built from
ops/<timestamp>-<action>.log                    logs of scripts the UI launched
```

## The loop on the box

Each cycle: pull `control.json`; mirror `.ok`/`.bad` marks; delete raws and decoded files of
every validated batch (metadata and state are never touched); re-push and re-validate stale
unvalidated batches (after `retry_after`, default 10 min); then, unless paused, over the
`max_unvalidated` limit (default 4) or under `min_free_gb` (default 25), crawl one page (200
replays) of the next incomplete window, decode what landed with `tools/rldecode`, and seal a
batch when `batch_size` (default 20,000) is reached. Sealing writes a manifest with every id,
size and sha256; a background thread pushes the batch's `.npz` files, the ledger files it
touched and the manifest with rclone over SFTP, then runs the validator over SSH. Windows go
months outer, bands inner, so every band advances together and an early stop is balanced.

Replays both decoders reject are listed in the manifest's `failed` and their raws deleted; the
ledger line stays, so they are never fetched again. SIGTERM, the `stop` command and the disk
guard are all polled between pages: a stop seals, pushes, waits for validation and exits 0.

## Setup, once

The full walk-through, from accounts to the first run, is [DEPLOYMENT.md](DEPLOYMENT.md).

1. Lab: install Tailscale, `hcloud`, `rclone`, Python 3; clone this repo to `/opt/rlreplay`; run
   `sudo scripts/lab_setup.sh --root /tank/rl`. It creates the `rlreplay` user and the tree,
   generates the box-to-lab key (restricted in `authorized_keys` to the tailnet, SFTP and the
   validator through `lab_cmd.sh`), the lab's own key, and `/etc/rlreplay/lab.env` from the
   template. Bind `sshd` to the tailnet address. Register the lab key with Hetzner:
   `hcloud ssh-key create --name rl-lab --public-key-from-file /root/.ssh/id_ed25519.pub`.
2. Fill `HCLOUD_TOKEN`, `BALLCHASING_API_KEY` and `TS_AUTHKEY` (ephemeral, pre-authorised,
   reusable, tagged) in `/etc/rlreplay/lab.env`. Nothing else ever holds them.
3. `scripts/crawler_image.sh`: a throwaway server gets the repo, a venv, the Rust decoder,
   rclone and tailscale, is wiped of identity and snapshotted. Rebuild when the pipeline changes.
4. `systemctl enable --now rlreplay-lab-ui` and open `http://<lab tailnet ip>:8731/`.
5. Smoke test with `CRAWLER_MONTHS=2025-07..2025-07` and `CRAWLER_BATCH=500`: "Create box and
   start", watch one batch validate, "Stop, validate, delete box", confirm the box is gone and a
   second up fetches nothing.

## Lifecycle

- **Jobs** (`lab/jobs.py`, `jobs/<id>.json` on the lab): draft → running → paused / stopping →
  stopped → (continue) running … → complete. *Start* writes `run` to the control file and runs
  `crawler_up.sh --job <id>`, which takes the crawl parameters from the job file; *pause* and
  *resume* write the control file; *stop* runs `crawler_down.sh` (push, validate, delete) and
  *force stop* adds `--force`; *continue* is start again on a new box, which pulls the ledger;
  *cancel* removes the job. When the box reports "all windows complete" for the running job,
  the lab runs the stop itself and the job becomes complete. One job may be active at a time.
  The box's status and every manifest carry the job id, so progress, batches and rate history
  are shown per job.
- **Up** (`crawler_up.sh`): firewall allowing port 22 from the lab's public IP only, server from
  the snapshot, secrets over SSH stdin into `/etc/rlreplay` (mode 600), `tailscale up` with the
  auth key read from a file that is shredded right after, the box's tailnet address recorded in
  `boxes/<box>/tailnet_ip` (the lab talks to it by address, since a deleted ephemeral node's
  name can linger), the public rule removed once the box answers over the tailnet, ledger
  pulled, service started. If any step after creation fails the server is deleted again
  (`--keep-on-failure` to inspect). With `--job` it refuses to adopt an existing box.
- **Pause / resume / stop**: the UI (or `set_control` in `crawler_lib.sh`) writes
  `boxes/<box>/control.json`; the box honours it within a page.
- **Down** (`crawler_down.sh`): writes `stop`, stops the service, runs the final push and waits
  for validation, logs the box out of the tailnet, deletes the server only when every manifest
  has its `.ok`. Otherwise it lists what is unvalidated and leaves the server running;
  `--force` deletes it anyway.
- **Status**: the UI, or `crawler_status.sh` (reads the status file; `--live` asks the box).

## Secrets

`HCLOUD_TOKEN`, `BALLCHASING_API_KEY`, `TS_AUTHKEY` and the box-to-lab private key live in
`/etc/rlreplay` on the lab and, for the box's lifetime, in `/etc/rlreplay` on the box. They are
never in a script, the snapshot, cloud-init, a command line, the repo, a log or the UI (which
only reports whether each is set). `set -x` is never used in the scripts.

## Training from the operator PC

Unchanged from the handoff: build tables on the lab with the usual `rlreplay` commands over
the decoded tree, sync parquet to the GPU box with rclone, pull decoded arrays locally only to
prototype on one band and month.
