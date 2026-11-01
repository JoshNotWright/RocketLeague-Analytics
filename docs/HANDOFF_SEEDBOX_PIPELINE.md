# Handoff: seedbox crawl, decode, push to homelab

Agreed design for moving replay gathering off the laptop. Written in a side session on
2026-11-01; the primary session on `v2-replay-features` implements it once the current
band player-stage crawl is done.

## Decisions

- **Seedbox** runs the crawler and the Rust decoder at the full GC tier rate (2 files/s,
  roughly 170k replays/day on a good uplink). Every band, monthly windows, resumable.
- **Decode wide.** Before the big crawl starts, add every cheap field boxcars exposes that
  we currently drop (boost pickups per pad, car body, camera settings, anything else that
  is free to read), so the decoded arrays are a superset of what any current or likely
  model needs. This is the one irreversible choice: raws are not kept long term.
- **Retention on the seedbox.** Raw and decoded files stay for the day. After the homelab
  validates the day's push, the seedbox deletes that batch's raws and decoded files and
  keeps only the metadata jsonl files and crawl state. A failed validation leaves everything
  in place and the next night retries. Nothing is deleted that the lab has not confirmed.
- **Ledger** = the metadata jsonl files (a few hundred bytes per replay). They are the
  authority on what has been fetched; the crawler must not need the raw file or the lab to
  decide what to skip. Back them up in git or with the nightly job; losing them is the only
  way to refetch.
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
4. New `scripts/seedbox_day.sh` (or `.py`): crawl window -> decode batch -> write a batch
   manifest (`<yyyy-mm-dd>.manifest.json`: ids, npz sha256, sizes) -> rclone decoded + meta +
   manifest to the lab.
5. New `scripts/lab_validate.py`: for a manifest, check every npz exists on the lab with
   matching size and sha256; write `<yyyy-mm-dd>.ok` next to it on success.
6. Seedbox cleanup step: delete raws and npz for a batch only when its `.ok` is present on
   the lab (or mirrored back); never touch metadata or crawl state.
7. Stay inside ballchasing's GC tier limits and terms; at scale, email the ballchasing
   author about the project.

## Done looks like

- A seedbox run of one monthly window for one band ends with the lab holding the npz and
  metadata, the manifest validated, the seedbox holding only metadata and state for that
  batch, and a second run of the same window fetching nothing.
- `rlreplay extract` / `touches` / `decisions-build` run on the lab's decoded folder and
  produce the same tables as the local pipeline.

## Secrets

`BALLCHASING_API_KEY` is set in the seedbox environment only, never in a script, the repo
or a log. Same rules as the laptop.
