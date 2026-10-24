"""
landing/writer.py

Writes raw API payloads to the landing zone, untouched apart from a small
metadata envelope. Local disk for now, laid out the way the blob container
will be, so Phase 2 swaps the write target rather than the layout:

    {root}/{source}/dt=YYYY-MM-DD/congress=NNN/{entity_id}.json
    {root}/{source}/dt=YYYY-MM-DD/{entity_id}.json      (no congress, e.g. members)

`dt` is the batch date (when the data was pulled), not any legislative
date -- re-pulls of the same entity land side by side in new partitions,
and staging picks the latest.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DEFAULT_LANDING_ROOT = "raw_data"

# Anything outside this set is replaced in path segments, so ids that come
# from the API can't escape the landing root or produce awkward blob names.
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def landing_root() -> Path:
    return Path(os.environ.get("LANDING_ROOT", DEFAULT_LANDING_ROOT))


def landing_path(
    source: str,
    congress: Optional[int],
    entity_id: str,
    batch_ts: datetime,
    root: Optional[Path] = None,
) -> Path:
    path = Path(root or landing_root()) / _safe(source) / f"dt={_utc(batch_ts):%Y-%m-%d}"
    if congress is not None:
        path = path / f"congress={int(congress)}"
    return path / f"{_safe(entity_id)}.json"


def land_raw(
    payload: Any,
    source: str,
    congress: Optional[int],
    entity_id: str,
    batch_ts: datetime,
    root: Optional[Path] = None,
) -> Path:
    """Write one raw payload; returns the path written. Overwrites in place."""
    path = landing_path(source, congress, entity_id, batch_ts, root)
    path.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "source": source,
        "congress": congress,
        "entity_id": entity_id,
        "batch_ts": _utc(batch_ts).isoformat(),
        "payload": payload,
    }

    # Write to a temp file and rename, so a crash mid-write never leaves a
    # truncated file that looks landed.
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False)
        os.replace(tmp_name, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise
    return path


def _safe(segment: str) -> str:
    return _UNSAFE_CHARS.sub("_", str(segment))


def _utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise ValueError("batch_ts must be timezone-aware")
    return ts.astimezone(timezone.utc)
