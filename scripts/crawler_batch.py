#!/usr/bin/env python3
"""The cloud crawler's service entry point; see rlreplay/cloud.py and docs/CLOUD_CRAWLER.md.

    crawler_batch.py [--config /etc/rlreplay/crawler.json] run | pull-ledger | push | status | unvalidated
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rlreplay.cloud import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
