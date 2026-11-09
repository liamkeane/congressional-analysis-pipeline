"""
backfill/run_backfill.py

One-off historical load of the congresses in scope into the raw landing
zone. The daily DAG takes over from there, watermarking on updateDate.

Runs in two phases:
  1. List every bill in the Congress and land each list item under
     `bill_list`. This takes ~80 requests, so it finishes before the
     list has time to shift under us.
  2. Walk the landed list and land each bill's detail, actions,
     cosponsors and subjects. This is the hours-long part.

The run is resumable. Its batch_ts is saved in a state file the first
time, so a resumed run (even on a later day) writes into the same `dt=`
folder, and bills whose files already exist there are skipped. Bills
that fail are logged and left unlanded, so running again retries them.

Run from the repo root:
    python3 -m ingestion.backfill.run_backfill 119
    python3 -m ingestion.backfill.run_backfill 119 --limit 20   # try it on a few bills
    python3 -m ingestion.backfill.run_backfill 119 --fresh      # start a new backfill
"""

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from ingestion.congress_client import CongressAPIError, CongressClient, endpoints
from ingestion.landing.writer import DEFAULT_ROOT, land_raw, landing_dir, landing_path

logger = logging.getLogger(__name__)

CONGRESS_NUMBER = 119  # Starting with current Congress. Can backfill earlier ones too.

STATE_DIR = Path(".backfill_state")

# Every source land_bill writes. A bill counts as landed only when all of them exist.
BILL_SOURCES = ("bill", "bill_actions", "bill_cosponsors", "bill_subjects")

# One failed bill is a blip; this many in a row means the API is down, and
# carrying on would just spend ~4 minutes of retries failing each remaining bill.
MAX_CONSECUTIVE_FAILURES = 5


def bill_entity_id(congress: int, bill_type: str, bill_number) -> str:
    return f"{congress}-{bill_type.lower()}-{bill_number}"


def land_bill(
    client: CongressClient,
    congress: int,
    bill_type: str,
    bill_number: int,
    batch_ts: datetime,
    root: Path = DEFAULT_ROOT,
) -> list[Path]:
    """
    Fetch one bill's detail, actions, cosponsors, and subjects and land each as its own file.
    Returns the paths written.
    """
    bill_type = bill_type.lower()  # the API lists types as "HR", but endpoints expect "hr"
    entity_id = bill_entity_id(congress, bill_type, bill_number)

    detail = endpoints.get_bill_detail(client, congress, bill_type, bill_number)
    actions = list(endpoints.get_bill_actions(client, congress, bill_type, bill_number))    # get_bill_actions returns a lazy iterator that pages through the API; list() forces it to read all pages and return a list of all actions.
    cosponsors = list(endpoints.get_bill_cosponsors(client, congress, bill_type, bill_number))
    subjects = list(endpoints.get_bill_subjects(client, congress, bill_type, bill_number))

    return [
        land_raw(detail, "bill", congress, entity_id, batch_ts, root),
        land_raw(actions, "bill_actions", congress, entity_id, batch_ts, root),
        land_raw(cosponsors, "bill_cosponsors", congress, entity_id, batch_ts, root),
        land_raw(subjects, "bill_subjects", congress, entity_id, batch_ts, root),
    ]


def already_landed(congress: int, entity_id: str, batch_ts: datetime, root: Path = DEFAULT_ROOT) -> bool:
    # land_raw renames files into place, so a file that exists is complete.
    return all(
        landing_path(source, congress, entity_id, batch_ts, root).exists()
        for source in BILL_SOURCES
    )


# ---------------------------------------------------------------------------
# State: remembers batch_ts and whether phase 1 finished, across restarts
# ---------------------------------------------------------------------------

def state_path(congress: int, state_dir: Path = STATE_DIR) -> Path:
    return Path(state_dir) / f"backfill-congress={congress}.json"


def save_state(state: dict, state_dir: Path = STATE_DIR) -> None:
    path = state_path(state["congress"], state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def load_or_create_state(congress: int, state_dir: Path = STATE_DIR, fresh: bool = False) -> dict:
    """
    Resume the saved backfill for this Congress, or start a new one if
    there isn't one (or `fresh` is set).
    """
    path = state_path(congress, state_dir)
    if path.exists() and not fresh:
        state = json.loads(path.read_text(encoding="utf-8"))
        logger.info("Resuming backfill for Congress %d started at %s", congress, state["batch_ts"])
        return state

    state = {
        "congress": congress,
        "batch_ts": datetime.now(timezone.utc).isoformat(),
        "list_complete": False,
    }
    save_state(state, state_dir)
    logger.info("Starting new backfill for Congress %d at %s", congress, state["batch_ts"])
    return state


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------

def land_bill_list(client: CongressClient, congress: int, batch_ts: datetime, root: Path = DEFAULT_ROOT) -> int:
    """Phase 1: land every item from the bill list endpoint. Returns how many were landed."""
    count = 0
    for bill in endpoints.get_bills(client, congress):
        land_raw(bill, "bill_list", congress, bill_entity_id(congress, bill["type"], bill["number"]), batch_ts, root)
        count += 1
    logger.info("Listed %d bills for Congress %d", count, congress)
    return count


def land_bills(
    client: CongressClient,
    congress: int,
    batch_ts: datetime,
    root: Path = DEFAULT_ROOT,
    limit: Optional[int] = None,
    max_consecutive_failures: int = MAX_CONSECUTIVE_FAILURES,
) -> list[str]:
    """
    Phase 2: land each listed bill's detail, actions, cosponsors and subjects,
    skipping bills already landed in this batch. Returns the entity ids that failed.
    Raises RuntimeError if `max_consecutive_failures` bills in a row fail.
    """
    list_files = sorted(landing_dir("bill_list", congress, batch_ts, root).glob("*.json"))
    if limit is not None:
        list_files = list_files[:limit]
    total = len(list_files)

    landed = skipped = 0
    failed = []
    consecutive_failures = 0

    for i, f in enumerate(list_files, start=1):
        item = json.loads(f.read_text(encoding="utf-8"))["payload"]
        bill_type, bill_number = item["type"], item["number"]
        entity_id = bill_entity_id(congress, bill_type, bill_number)

        if already_landed(congress, entity_id, batch_ts, root):
            skipped += 1
        else:
            # Only API and network errors are caught: a KeyError etc. is a bug in
            # this code, and should stop the run rather than be logged for every bill.
            try:
                land_bill(client, congress, bill_type, bill_number, batch_ts, root)
                landed += 1
                consecutive_failures = 0
            except (CongressAPIError, requests.RequestException) as exc:
                logger.warning("Failed to land %s: %s", entity_id, exc)
                failed.append(entity_id)
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures:
                    raise RuntimeError(
                        f"{consecutive_failures} bills in a row failed, so the API is probably down. "
                        "Stopping; run again later to resume."
                    ) from exc

        if i % 100 == 0 or i == total:
            logger.info(
                "Progress %d/%d: %d landed, %d skipped, %d failed",
                i, total, landed, skipped, len(failed),
            )

    return failed


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_backfill(
    congress: int,
    root: Path = DEFAULT_ROOT,
    state_dir: Path = STATE_DIR,
    fresh: bool = False,
    limit: Optional[int] = None,
) -> list[str]:
    """
    Run (or resume) a backfill of the raw landing zone for the specified
    Congress number. Returns the entity ids of bills that failed to land.
    """
    state = load_or_create_state(congress, state_dir, fresh)
    batch_ts = datetime.fromisoformat(state["batch_ts"])

    with CongressClient() as client:
        if state["list_complete"]:
            logger.info("Bill list already landed; skipping phase 1")
        else:
            land_bill_list(client, congress, batch_ts, root)
            state["list_complete"] = True
            save_state(state, state_dir)

        failed = land_bills(client, congress, batch_ts, root, limit)

    if failed:
        logger.warning("%d bills failed; run again to retry them", len(failed))
    else:
        logger.info("Backfill for Congress %d complete", congress)
    return failed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("congress", type=int, nargs="?", default=CONGRESS_NUMBER)
    parser.add_argument("--fresh", action="store_true", help="discard saved state and start a new backfill")
    parser.add_argument("--limit", type=int, help="only process the first N listed bills in phase 2")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_backfill(args.congress, fresh=args.fresh, limit=args.limit)


if __name__ == "__main__":
    main()
