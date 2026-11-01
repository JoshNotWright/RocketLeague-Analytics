# Handoff: ephemeral cloud crawler, decode, push to homelab

Agreed design for moving replay gathering off the laptop. Written in a side session on
2026-11-01; the primary session on `v2-replay-features` implements it once the current
band player-stage crawl is done.

## Decisions

- **Ephemeral cloud box, not a standing seedbox.** A Hetzner Cloud server (hourly billed,
  no volume) is created by one command, runs the crawler and the Rust decoder at the full
  GC tier rate (2 files/s, roughly 170k replays/day), pushes to the homelab, and is deleted
  by one command. The server holds no state that matters: the ledger lives on the homelab
  and is pulled on boot, pushed on shutdown. Every band, monthly windows, resumable.
- **Disk is the server's local NVMe, no attached volume.** A batch is pushed and validated
  every N replays (default 20,000: about 22 GB raw + 6 GB decoded), not once a day, so the
  box never holds more than a batch or two. A full day at rate would be ~190 GB raw, which
  is why the batch, not the day, is the unit. Pick a plan whose disk comfortably holds two
  batches; the cleanup frees each batch as soon as the lab validates it.
- **Decode wide.** Before the big crawl starts, add every cheap field boxcars exposes that
  we currently drop (boost pickups per pad, car body, camera settings, anything else that
  is free to read), so the decoded arrays are a superset of what any current or likely
  model needs. This is the one irreversible choice: raws are not kept long term.
- **Retention on the box.** Raw and decoded files stay until their batch is pushed and the
  homelab validates it; then the box deletes that batch's raws and decoded files and keeps
  only the metadata jsonl files and crawl state, which are pushed too. A failed validation
  leaves the batch in place and it is retried after the next batch; the crawler pauses if
  unvalidated batches would exceed the disk. The teardown command refuses to
  delete the server while any batch is unvalidated. Nothing is deleted that the lab has
  not confirmed.
- **Ledger** = the metadata jsonl files (a few hundred bytes per replay). They are the
  authority on what has been fetched; the crawler must not need the raw file to decide
  what to skip. The homelab copy is the master; the box pulls it on boot and pushes it with
  every batch. Losing them is the only way to refetch.
- **Homelab** holds decoded arrays and metadata as flat files on a mirrored, checksummed
  filesystem (ZFS/Btrfs), shared over NFS/SMB. Feature tables are regenerated from the
  arrays in minutes and copied locally to the GPU box for training. No database, no object
  store, no off-site copy for now.

## Layout on the homelab

```
data/
  decoded/<playlist>/<band>/<yyyy-mm>/<replay_id>.npz
  meta/<playlist>/<band>/<yyyy-mm>.jsonl
  features/<table>/<playlist>/<band>/<yyyy-mm>.parquet   (disposable, regenerated)
```

## Lifecycle: two commands

```
scripts/crawler_up.sh     # create server, install, pull ledger, start crawling
scripts/crawler_down.sh   # push, validate, delete server (refuses if a batch is unvalidated)
```

`crawler_up.sh` (runs on the laptop or the homelab):
1. `hcloud server create` from a snapshot that already has Python, the venv, rclone and
   `tools/rldecode` (build the snapshot once with `scripts/crawler_image.sh`; rebuild when
   the repo's pipeline changes). Server type with 4+ vCPU; no volume.
2. Waits for SSH, then pipes `BALLCHASING_API_KEY` from the local environment over SSH into
   `/etc/rlreplay/env` (root, mode 600). Never cloud-init, never a command-line argument,
   never echoed. A systemd unit uses it as `EnvironmentFile`.
3. Pulls the ledger (`meta/` and `crawl_state.json` files) from the homelab with rclone.
4. Starts `rlreplay-crawler.service`, which loops: crawl window -> decode batch -> manifest
   -> push -> (lab validates) -> cleanup, in batches of N replays, until the configured
   window list is exhausted or the service is stopped. A `crawler_status.sh` prints
   progress over SSH.

`crawler_down.sh`:
1. Stops the service, runs one final push and waits for validation.
2. If every manifest has its `.ok`, `hcloud server delete`. Otherwise prints what is
   unvalidated and exits non-zero; the server keeps running.

Both scripts are idempotent: `up` on an existing server reattaches, `down` on a missing
server is a no-op. The hcloud token lives in the local environment (`HCLOUD_TOKEN`) with
the same rules as the ballchasing key.

## Code changes

1. `rlreplay/crawl.py` done-set: today an id is done only if its metadata line exists and
   its `.replay` file is non-empty (`Crawler._load_state`, around the `self.out.glob`
   loop). Add a flag (e.g. `--ledger-only` / `ledger_only=True`) under which a metadata
   line alone counts as done. Default off so the local folders that keep raws behave as
   now. Tests in `tests/test_rlreplay_crawl.py`.
2. Every server-side crawl passes the union of all metadata ids across folders as
   `known_ids` (the player stage already does this; make the window stage do it too).
3. `rlreplay/decode.py` / `rust/rldecode`: the wide decode. List the dropped fields first,
   add the cheap ones, keep exact parity on the existing arrays
   (`tests/test_rlreplay_rldecode.py`).
4. New `scripts/crawler_batch.py` (what the systemd unit runs): crawl window -> decode
   batch -> write a batch manifest (`<yyyy-mm-dd>-<n>.manifest.json`: ids, npz sha256,
   sizes) -> rclone decoded + meta + manifest to the lab.
5. New `scripts/lab_validate.py`: for a manifest, check every npz exists on the lab with
   matching size and sha256; write `<yyyy-mm-dd>.ok` next to it on success.
6. Cleanup step on the box: delete raws and npz for a batch only when its `.ok` is present
   on the lab (mirrored back by rclone); never touch metadata or crawl state.
8. `scripts/crawler_image.sh`, `crawler_up.sh`, `crawler_down.sh`, `crawler_status.sh` as
   above, plus the systemd unit under `deploy/`.
7. Stay inside ballchasing's GC tier limits and terms; at scale, email the ballchasing
   author about the project.

## Done looks like

- `crawler_up.sh` on a clean account produces a crawling server in under five minutes
  with no key visible in cloud-init, the server's metadata, shell history or logs.
- One monthly window for one band ends with the lab holding the npz and metadata, the
  manifest validated, the box holding only metadata and state for that batch, and a
  second run of the same window fetching nothing.
- `crawler_down.sh` deletes the server only once everything is validated; the next `up`
  resumes from the lab's ledger with nothing refetched.
- `rlreplay extract` / `touches` / `decisions-build` run on the lab's decoded folder and
  produce the same tables as the local pipeline.

## Secrets

`BALLCHASING_API_KEY` and `HCLOUD_TOKEN` live in the local environment and, on the box,
in a root-only env file written over SSH at boot. Never in a script, cloud-init, the
snapshot, the repo or a log. Same rules as the laptop.
