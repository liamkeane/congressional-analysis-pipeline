"""
backfill/run_backfill.py

One-off historical load of the congresses in scope into the raw landing
zone. The daily DAG takes over from there, watermarking on updateDate.

- Specify Congress number in cmd line args

Run from the repo root:
    PYTHONPATH=ingestion venv/bin/python -m backfill.run_backfill 119

"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from ingestion.congress_client import CongressClient, endpoints
from ingestion.landing.writer import DEFAULT_ROOT, land_raw, landing_path

logger = logging.getLogger(__name__)

CONGRESS_NUMBER = 119  # Starting with current Congress; can backfill earlier ones too.


def land_bill(
    client: CongressClient,
    congress: int,
    bill_type: str,
    bill_number: int,
    batch_ts: datetime,
    root: Path = DEFAULT_ROOT,
) -> list[Path]:
    """
    Fetch one bill's detail and actions and land each as its own file.
    Returns the paths written.
    """
    bill_type = bill_type.lower()  # the API lists types as "HR", but endpoints expect "hr"
    entity_id = f"{congress}-{bill_type}-{bill_number}"

    detail = endpoints.get_bill_detail(client, congress, bill_type, bill_number)
    # get_bill_actions returns a lazy iterator that pages through the API;
    # list() reads every page so the whole action history lands in one file.
    actions = list(endpoints.get_bill_actions(client, congress, bill_type, bill_number))

    return [
        land_raw(detail, "bill", congress, entity_id, batch_ts, root),
        land_raw(actions, "bill_actions", congress, entity_id, batch_ts, root),
    ]


def run_backfill(congress: int): 
    """
    Run a backfill of the raw landing zone for the specified Congress number.
    """
    logger.info("Starting backfill for Congress %d", congress)

    batch_ts = datetime.now(timezone.utc)
    
    with CongressClient() as client:
        # Fetch all bills in specified Congress
        count = 0
        for bill in endpoints.get_bills(client, congress):
            land_raw(bill, "bill_list", congress, f"{congress}-{bill['type'].lower()}-{bill['number']}", batch_ts)
            # logger.info("Landed bill %s-%s", bill['type'].lower(), bill['number'])
            count += 1

        logger.info("Landed %d bills for Congress %d", count, congress)

        # Fetch bill details, actions, cosponsors, and subjects for each bill
        list_dir = landing_path("bill_list", congress, "x", batch_ts, DEFAULT_ROOT).parent      # TODO: This function is not being used for its intended purpose- just a convenient way to locate the directory containing the landed bill list files.
        for f in sorted(list_dir.glob("*.json")):
            item = json.loads(f.read_text())["payload"]
            bill_type = item["type"]
            bill_number = item["number"]
            land_bill(client, congress, bill_type, bill_number, batch_ts, DEFAULT_ROOT)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_backfill(CONGRESS_NUMBER)
