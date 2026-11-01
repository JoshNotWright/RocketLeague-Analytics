#!/usr/bin/env python3
"""Validate one pushed batch on the lab: `lab_validate.py <lab_root> <batch>`.

Standard library only, so it runs with the lab's system Python. Reads
``<lab_root>/batches/<batch>.manifest.json``, hashes every decoded file named in it on the
lab's own disk, checks size and sha256, checks that each window's ledger file on the lab holds
every id of the batch, and writes ``<batch>.ok`` next to the manifest only when everything
matches. On any mismatch it writes ``<batch>.bad`` with the details and exits 1; the box then
pushes the batch again and asks for another validation. The box is triggered over SSH and
never deletes anything until the ``.ok`` exists.

Idempotent: a batch with an existing ``.ok`` is re-checked only with ``--force``.
"""
import hashlib
import json
import sys
import time
from pathlib import Path


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ledger_ids(path: Path) -> set:
    ids = set()
    if not path.exists():
        return ids
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    ids.add(json.loads(line)["id"])
                except (ValueError, KeyError, TypeError):
                    continue
    return ids


def validate(lab_root, batch: str, *, force: bool = False) -> dict:
    root = Path(lab_root)
    manifest_path = root / "batches" / f"{batch}.manifest.json"
    ok_path = root / "batches" / f"{batch}.ok"
    bad_path = root / "batches" / f"{batch}.bad"
    if ok_path.exists() and not force:
        return {"ok": True, "already": True}
    if not manifest_path.exists():
        return {"ok": False, "problems": [{"manifest": str(manifest_path), "error": "missing"}]}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems = []
    total = 0
    for item in manifest.get("items", []):
        path = root / "decoded" / item["playlist"] / item["band"] / item["month"] / f"{item['id']}.npz"
        if not path.exists():
            problems.append({"id": item["id"], "error": "missing"})
            continue
        size = path.stat().st_size
        if size != item["size"]:
            problems.append({"id": item["id"], "error": "size", "expected": item["size"], "got": size})
            continue
        digest = sha256_of(path)
        if digest != item["sha256"]:
            problems.append({"id": item["id"], "error": "sha256"})
            continue
        total += size
    by_window = {}
    for item in manifest.get("items", []):
        by_window.setdefault((item["playlist"], item["band"], item["month"]), set()).add(item["id"])
    for (playlist, band, month), ids in by_window.items():
        meta = root / "meta" / playlist / band / f"{month}.jsonl"
        missing = ids - ledger_ids(meta)
        if missing:
            problems.append({"ledger": str(meta), "error": "ids missing from ledger", "count": len(missing),
                             "sample": sorted(missing)[:5]})
    result = {"ok": not problems, "batch": batch, "count": len(manifest.get("items", [])), "bytes": total,
              "validated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if problems:
        result["problems"] = problems
        bad_path.write_text(json.dumps(result, indent=1), encoding="utf-8")
        if ok_path.exists():
            ok_path.unlink()
    else:
        ok_path.write_text(json.dumps(result, indent=1), encoding="utf-8")
        if bad_path.exists():
            bad_path.unlink()
    return result


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    force = "--force" in argv
    argv = [a for a in argv if a != "--force"]
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        return 2
    result = validate(argv[0], argv[1], force=force)
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
