"""
landing/writer.py

Saves one raw API response to the landing zone as a JSON file.

Files are laid out the same way on local disk and in S3:

    {source}/dt=YYYY-MM-DD/congress=NNN/{entity_id}.json

`dt` is the date the data was pulled (from batch_ts), not a legislative date!
Where those keys physically live is up to the Storage passed in (see storage.py).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from .storage import Storage


def landing_prefix(source: str, congress: Optional[int], batch_ts: datetime) -> str:
    """The key prefix ("folder") one batch of a source lands under, ending in "/"."""
    if batch_ts.tzinfo is None:
        raise ValueError("batch_ts must be timezone-aware, e.g. datetime.now(timezone.utc)")
    batch_date = batch_ts.astimezone(timezone.utc).date()

    return f"{source}/dt={batch_date.isoformat()}/congress={congress}/"


def landing_key(source: str, congress: Optional[int], entity_id: str, batch_ts: datetime) -> str:
    """Where a response for this entity lands."""
    return f"{landing_prefix(source, congress, batch_ts)}{entity_id}.json"


def land_raw(
    payload: Any,
    source: str,
    congress: Optional[int],
    entity_id: str,
    batch_ts: datetime,
    storage: Storage,
) -> str:
    """
    Write `payload` (the API response, untouched) plus a little metadata.
    Returns the key written. Writing the same entity again overwrites it.
    """
    key = landing_key(source, congress, entity_id, batch_ts)

    record = {
        "source": source,   # ie "bill", "bill_actions", "member", etc.
        "congress": congress,
        "entity_id": entity_id,
        "batch_ts": batch_ts.astimezone(timezone.utc).isoformat(),
        "payload": payload,
    }

    storage.write_text(key, json.dumps(record, ensure_ascii=False))
    return key
