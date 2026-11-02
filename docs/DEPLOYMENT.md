# Deployment and setup: lab, Hetzner crawler, web UI

Step-by-step, from empty accounts to a running crawl. Companion to
[CLOUD_CRAWLER.md](CLOUD_CRAWLER.md) (how it works) and
[HANDOFF_SEEDBOX_PIPELINE.md](HANDOFF_SEEDBOX_PIPELINE.md) (why). Written 2026-11-01 before
the first real run: the scripts are tested against a local fake of the lab, not yet against
Hetzner, so expect to fix small things on the first pass and keep this file honest as you go.

The shape of it:

```
your PC ──tailnet──▶ lab (Linux box at home: UI, secrets, archive, validator)
                       │
                       └──tailnet──▶ Hetzner box (ephemeral: crawls + decodes, pushes batches)
                                       └──▶ ballchasing.com (outbound only)
```

Everything you type happens on the lab. Your PC only opens the UI in a browser.

---

## 0. Accounts and keys (about 30 minutes)

### 0.1 Hetzner Cloud
1. Create an account at console.hetzner.cloud and a **project** (for example `rl-crawler`).
2. In the project: Security → API tokens → Generate, **Read & Write**. Copy it once; this is
   `HCLOUD_TOKEN`. You cannot view it again.
3. Nothing else is needed in the console. Servers, snapshots and firewalls are all created by
   the scripts.

### 0.2 Tailscale
1. Sign up at tailscale.com and install Tailscale on your PC and (later) on the lab.
2. Admin console → Access controls. Add a tag for the crawler and make yourself its owner.
   Minimal policy, if you have not edited yours:
   ```json
   {
     "tagOwners": { "tag:rl-crawler": ["autogroup:admin"] },
     "acls": [ { "action": "accept", "src": ["*"], "dst": ["*:*"] } ]
   }
   ```
3. Admin console → Settings → Keys → Generate auth key: **Reusable**, **Ephemeral**,
   **Pre-authorized**, tag `tag:rl-crawler`, expiry 90 days. Copy it; this is `TS_AUTHKEY`.
   Ephemeral means a deleted box disappears from your tailnet on its own.
4. Enable MagicDNS (DNS → MagicDNS) so the box is reachable as `rl-crawler` by name.

### 0.3 ballchasing
Your GC-tier key from ballchasing.com/upload is `BALLCHASING_API_KEY`. The crawler stays
inside the published GC limits (2 files a second). Before a million-replay run, email the
site's author about the project.

---

## 1. The lab (about an hour)

Any always-on Linux machine with a mirrored, checksummed filesystem. Ubuntu Server 24.04 is
what the scripts assume (paths like `/usr/lib/openssh/sftp-server`); Debian is identical.

### 1.0 The short way: one command

If Tailscale is already joined on the lab, `scripts/lab_bootstrap.sh` does 1.2 to 1.7 in one go.
On the lab:

```bash
curl -fsSL https://raw.githubusercontent.com/JoshNotWright/RocketLeague-Analytics/v2-replay-features/scripts/lab_bootstrap.sh | sudo bash
```

The script installs the packages and the hcloud CLI, clones the branch to `/opt/rlreplay`,
runs `lab_setup.sh`, starts the UI, and prints what is still empty in the env file. Options
go between `sudo` and `bash`, for example `| sudo RL_ROOT=/pool/rl bash` for a different data
tree, or the three secrets `HCLOUD_TOKEN=… BALLCHASING_API_KEY=… TS_AUTHKEY=…` if you want
them written now (then it also registers the lab's SSH key with Hetzner). Otherwise fill them with
`sudo nano /etc/rlreplay/lab.env`, restart `rlreplay-lab-ui`, and re-run the same command
once so the Hetzner key gets registered.

Re-running the command later updates the checkout to the branch tip and restarts the UI.
It does not touch sshd unless you pass `RL_LOCK_SSHD=1` (do that from a Tailscale session,
not a LAN one, or it cuts you off). Sections 1.1 to 1.7 below are what it does, by hand.

### 1.1 Storage
**NAS mount (the current lab).** The archive lives on the NAS mounted at `/media/rocketleague`;
the tree is `/media/rocketleague/rl` and the bootstrap picks that by itself when the mount is
present. Two things the mount must satisfy, both checked by `lab_setup.sh`:
- the `rlreplay` user must be able to write there. Over NFS, export the share so that uid is
  allowed (or `all_squash,anonuid=<uid>,anongid=<gid>`); over SMB, mount with
  `uid=rlreplay,gid=rlreplay`. Ownership cannot be changed from the lab side, so the script
  only warns when chown is refused and then tests a write as that user.
- it must be in `/etc/fstab` with `_netdev`, so it comes up at boot before the UI. The setup
  script adds `RequiresMountsFor=` to the UI service for the same reason.

- SMB reports odd sizes for a moment after a write and refuses to recreate a file it has just
  deleted. The box's rclone therefore runs with `--ignore-size --sftp-set-modtime=false`
  (set in `render_crawler_config`); integrity comes from the validator's sha256 of every file,
  not from rclone's size check. If pushes still fail with "sizes differ" or "permission denied",
  add `actimeo=0` to the mount options.

Throughput is not a concern: a batch is pushed at tailnet speed and read once by the validator.
Keep the NAS's own snapshots or checksumming on for that share; the lab does no mirroring of
its own.

**Local mirror (alternative).** A ZFS mirror is the recommended tree. Example with two disks:
```bash
sudo apt install -y zfsutils-linux
sudo zpool create -o ashift=12 tank mirror /dev/disk/by-id/DISK_A /dev/disk/by-id/DISK_B
sudo zfs create -o compression=lz4 -o atime=off tank/rl
```
Budget: about 0.8 MB per replay decoded (wide layout) plus the ledger. A million replays is
roughly 0.8 TB. Btrfs RAID1 is fine too; the scripts only need a directory.

### 1.2 Packages
```bash
sudo apt update && sudo apt install -y git python3 python3-venv rclone curl openssh-server
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh=false          # log in with your account in the browser it opens
tailscale ip -4                        # note this: it is LAB_TS_HOST
```
hcloud CLI (the scripts drive Hetzner with it):
```bash
curl -fsSL https://github.com/hetznercloud/cli/releases/latest/download/hcloud-linux-amd64.tar.gz | sudo tar -xz -C /usr/local/bin hcloud
hcloud version
```

### 1.3 The repository
```bash
sudo git clone <your remote> /opt/rlreplay
cd /opt/rlreplay && sudo git checkout v2-replay-features
```
The lab needs no venv: the validator and the UI are standard-library Python. (Feature building
on the lab later needs the v2 requirements in a venv; not needed for crawling.)

### 1.4 One-time setup script
```bash
sudo /opt/rlreplay/scripts/lab_setup.sh --root /media/rocketleague/rl --user rlreplay
```
It creates the `rlreplay` system user and the tree (`decoded/ meta/ batches/ boxes/ features/`),
generates two keys, writes `/etc/rlreplay/lab.env` from the template, installs the UI service,
and prints what to do next. The keys:
- `/etc/rlreplay/crawler_key` — the box uses this to reach the lab. Its public half is added to
  `~rlreplay/.ssh/authorized_keys` restricted to the tailnet range, no shell, no forwarding,
  forced through `scripts/lab_cmd.sh` (SFTP or the validator, nothing else).
- `/root/.ssh/id_ed25519` — the lab uses this to reach `root@box`. Register it with Hetzner:
  ```bash
  hcloud ssh-key create --name rl-lab --public-key-from-file /root/.ssh/id_ed25519.pub
  ```
  (`hcloud` reads `HCLOUD_TOKEN` from the environment; `export HCLOUD_TOKEN=...` for this one
  command, then `unset` it. From then on the scripts load it from the env file.)

### 1.5 Fill the env file
```bash
sudo nano /etc/rlreplay/lab.env
```
Set the three secrets and check the rest:

| key | value |
| --- | --- |
| `HCLOUD_TOKEN` | from 0.1 |
| `BALLCHASING_API_KEY` | from 0.3 |
| `TS_AUTHKEY` | from 0.2 |
| `LAB_ROOT` | `/media/rocketleague/rl` (NAS) or `/tank/rl` (local mirror) |
| `LAB_USER` | `rlreplay` |
| `LAB_TS_HOST` | the lab's tailnet IP from 1.2 (an IP, not the MagicDNS name: the box resolves it through rclone) |
| `LAB_REPO` | `/opt/rlreplay` |
| `CRAWLER_KEY` | `/etc/rlreplay/crawler_key` |
| `HCLOUD_SSH_KEY` | `rl-lab` |
| `LAB_UI_BIND` | the same tailnet IP |
| `CRAWLER_*` | leave the defaults for now; the UI edits these |

The file is `root:rlreplay`, mode 640. Nothing else ever holds the secrets.

### 1.6 Lock sshd to the tailnet
The box, and you, reach the lab only over Tailscale. In `/etc/ssh/sshd_config`:
```
ListenAddress 100.x.y.z        # the lab's tailnet IP
PasswordAuthentication no
```
then `sudo systemctl restart ssh`. If the lab also serves SMB/NFS to your PC, bind those to the
tailnet interface or your LAN only. No port forwarding on your router, ever.

### 1.7 Start the UI
```bash
sudo systemctl enable --now rlreplay-lab-ui
sudo systemctl status rlreplay-lab-ui
```
Open `http://<lab tailnet ip>:8731/` from your PC. You should see the Overview with an empty
archive, "no crawler box", and on Settings the three secrets marked **set**. The UI has no login
because only tailnet devices can reach it; keep it that way.

---

## 2. The crawler image (about 10 minutes, once)

```bash
sudo /opt/rlreplay/scripts/crawler_image.sh
```
or press **Rebuild image** on the Crawler page. It creates a throwaway cpx22, installs Python,
rclone, Tailscale and Rust, copies this repository (no data, no secrets), builds the Rust
decoder, installs the service unit disabled, strips the machine's identity, snapshots it with
the label `role=rl-crawler-image`, and deletes the throwaway. Re-run whenever the pipeline
code changes; `crawler_up.sh` always uses the newest snapshot.

Cost: a snapshot is billed by size, about EUR 0.01 per GB per month, so well under a euro.

---

## 3. Smoke test (about 20 minutes)

Everything you run is a **job**: a saved set of crawl parameters (playlist, rank bands, months,
cap per window, batch size, server) with a lifecycle. Start a job and a box is created for it;
stop it and the box pushes what it holds, the lab validates it, and the server is deleted.
Pause leaves the box running but idle. A stopped job is continued on a new box, which pulls
the ledger and carries on; cancel removes the job (the archive keeps what it collected). One
job runs at a time, because there is one box and one ballchasing key.

Do this once before the real run. On the **Jobs** page press **New job**, pick the
**Smoke test** preset (one month, 300 replays per window, batches of 500, three bands, nine
hundred replays in all) and press **Create and start**. The job page opens; its Runs card
shows the script log:
1. firewall allows port 22 from the lab's public IP only, server created from the snapshot
2. secrets written over SSH into `/etc/rlreplay` on the box (mode 600)
3. box joins the tailnet as `rl-crawler`, the public rule is removed
4. ledger pulled, service started

Within a minute the Overview shows "Crawling Diamond · Jul 2025". Three windows of 300 is 900
replays, so after a few minutes the first 500-replay batch seals, pushes and validates: the
Transfers page shows it **✓ validated** and the box's "batches on the box" shows it cleaned.
Check on the lab:
```bash
ls /media/rocketleague/rl/decoded/ranked-doubles/diamond/2025-07 | head
wc -l /media/rocketleague/rl/meta/ranked-doubles/*/2025-07.jsonl
cat /media/rocketleague/rl/batches/*.ok
```
Because the job has a cap, it finishes on its own: when the box reports every window complete
the lab runs the stop for you, the last batch is pushed and validated, the server is deleted,
and the job shows **Complete**. Confirm with `hcloud server list` (empty) and in the Tailscale
admin console (the ephemeral node is gone). To check resumption, press **Continue** on the
completed job: a box comes up, pulls the ledger, reports every window complete without
fetching anything, and is deleted again.

If any step fails the job shows **Failed** with the reason, and the run's log is one click
away under Runs. **Stop box** retries a clean stop; **Force stop** deletes the box even when
its last push failed (whatever it still held is fetched again next time). The box's own log is
`journalctl -u rlreplay-crawler` over `ssh root@<its tailnet address>`; the address is in
`boxes/rl-crawler/tailnet_ip` on the lab.

---

## 4. The real run

**New job** → the **Full crawl** preset: every month from July 2025 to the current one, no
cap, batches of 20,000, cpx32. Press **Create and start**. At the GC rate the box does about
170,000 replays a day; the three bands of ranked doubles over sixteen months are a few days.
Cost is about EUR 1.40 a day for the server plus nothing for traffic (20 TB included). The
job page's estimate says "open-ended" because without a cap the size depends on what
ballchasing has listed.

Day to day:
- **Overview** is the only page you need. Its headline is the running job and what the box
  is doing; the rate chart flattens to zero when ballchasing throttles or the box waits for
  validation; "suggested next steps" appears when something needs you.
- **Pause** holds fetching without losing the page cursor and keeps the box; **Resume**
  continues. Use it if you want the bandwidth for something else.
- **Stop** is the normal way to finish early: push, validate, delete. The job can be
  **Continued** later on a fresh box from exactly where the ledger stands, which is also the
  answer to anything going wrong on the box: stop, fix, continue.
- A batch marked **✕ failed** retries on its own every ten minutes. If it keeps failing, the
  reason is in the Transfers row; a sha mismatch usually means the lab disk, not the box.
- The box pauses itself when four batches await validation or free disk drops under 25 GB.
  Both clear on their own once the lab validates.
- If the box's decoder fails on everything (a bad image), the box parks itself and the job
  page says so; nothing is written off. Stop, **Rebuild image** on Settings, Continue.

An uncapped job completes when every window's listing is exhausted; the lab then stops the
box for you. The archive stays on the lab; a later job (more months, another playlist)
starts from the ledger and fetches only what is new.

---

## 5. Using the archive

Build tables on the lab, train on your PC. On the lab, once, a venv with the v2 requirements:
```bash
cd /opt/rlreplay && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-v2.txt
```
Then per band and month (the decoded folder is the input; no raws are needed):
```bash
.venv/bin/python -m rlreplay decisions-build --decoded /media/rocketleague/rl/decoded/ranked-doubles/diamond/2025-07 --out /media/rocketleague/rl/features/decisions/diamond/2025-07 --horizon 2.5
```
and `extract` / `touches` likewise. Sync `/media/rocketleague/rl/features` to your PC with rclone over the
tailnet before a refit. Pull a decoded folder locally only to prototype a feature.

---

## 6. Updating

Code change → on the lab `cd /opt/rlreplay && sudo git pull`, restart the UI
(`sudo systemctl restart rlreplay-lab-ui`), rebuild the image, and the next box uses it. A
running box keeps the code it started with; stop and delete it, then create a new one.

---

## 7. Known gaps on the first pass

- The box carries only the Rust decoder. A replay it cannot parse is recorded under the
  batch's `failed` list (visible on Transfers as "failed decodes") and its raw is deleted; the
  Python fallback that exists on the PC needs rrrocket, which is not on the box. Expect a
  fraction of a percent. The ids are in the manifests if you ever want to refetch them.
- `crawler_up.sh` assumes the lab's public IP (as seen by ipify) is the one Hetzner will see
  for the bootstrap SSH. Behind CGNAT or a VPN, set the rule by hand with
  `hcloud firewall add-rule` and comment out the `my_public_ip` line.
- The UI's "run script" refuses a second operation while one is running, and trusts the
  tailnet entirely: anyone on your tailnet can press its buttons.
- Nothing here has run against Hetzner yet. The first smoke test is the real test; fix what
  breaks and update this file.

## 8. Secrets, restated

| secret | lives in | reaches the box as | never |
| --- | --- | --- | --- |
| `HCLOUD_TOKEN` | `/etc/rlreplay/lab.env` | nothing (lab only) | a command line, the repo, a log |
| `BALLCHASING_API_KEY` | `/etc/rlreplay/lab.env` | `/etc/rlreplay/env` over SSH stdin, mode 600 | cloud-init, the snapshot |
| `TS_AUTHKEY` | `/etc/rlreplay/lab.env` | a file read by `tailscale up`, shredded right after | a command line |
| box → lab key | `/etc/rlreplay/crawler_key` | `/etc/rlreplay/lab_key` over SSH stdin | authorized for anything but SFTP + validate |

Rotate any of the three by editing the env file; the next box gets the new value.
