"""
landing/writer.py

Saves one raw API response to the landing zone as a JSON file.

Files are laid out the way they will be in blob storage later:

    {root}/{source}/dt=YYYY-MM-DD/congress=NNN/{entity_id}.json

`dt` is the date the data was pulled (from batch_ts), not a legislative date!
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DEFAULT_ROOT = Path("raw_data")


def landing_path(
    source: str,
    congress: Optional[int],
    entity_id: str,
    batch_ts: datetime,
    root: Path = DEFAULT_ROOT,
) -> Path:
    """Where a response for this entity lands. Doesn't touch the disk."""
    if batch_ts.tzinfo is None:
        raise ValueError("batch_ts must be timezone-aware, e.g. datetime.now(timezone.utc)")
    batch_date = batch_ts.astimezone(timezone.utc).date()

    return Path(root) / source / f"dt={batch_date.isoformat()}" / f"congress={congress}" / f"{entity_id}.json"


def land_raw(
    payload: Any,
    source: str,
    congress: Optional[int],
    entity_id: str,
    batch_ts: datetime,
    root: Path = DEFAULT_ROOT,
) -> Path:
    """
    Write `payload` (the API response, untouched) plus a little metadata.
    Returns the path written. Writing the same entity again overwrites it.
    """
    path = landing_path(source, congress, entity_id, batch_ts, root)
    path.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "source": source,   # ie "bill", "bill_actions", "member", etc.
        "congress": congress,
        "entity_id": entity_id,
        "batch_ts": batch_ts.astimezone(timezone.utc).isoformat(),
        "payload": payload,
    }

    # Write to a temp file first, then rename it into place. The rename is
    # all-or-nothing, so a crash mid-write can't leave a half-written .json
    # that looks finished.
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")   # TODO return to this rename logic
    tmp_path.replace(path)
    return path
