"""
backfill/run_backfill.py

One-off historical load of the congresses in scope into the raw landing
zone. The daily DAG takes over from there, watermarking on updateDate.

- Specify Congress number in cmd line args

Run from the repo root:
    PYTHONPATH=ingestion venv/bin/python -m backfill.run_backfill 119

"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from ingestion.congress_client import CongressClient, endpoints
from ingestion.landing.writer import DEFAULT_ROOT, land_raw

logger = logging.getLogger(__name__)

CONGRESS_NUMBER = 119  # Starting with current Congress; can backfill earlier ones too.





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




if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_backfill(CONGRESS_NUMBER)
