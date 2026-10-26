"""
backfill/run_backfill.py

One-off historical load of the congresses in scope into the raw landing
zone. The daily DAG takes over from there, watermarking on updateDate.

- Specify Congress number in cmd line args
-

Run from the repo root:
    PYTHONPATH=ingestion venv/bin/python -m backfill.run_backfill

"""
import logging
from datetime import datetime, timezone
from pathlib import Path

from ingestion.congress_client import CongressClient, endpoints
from ingestion.landing.writer import DEFAULT_ROOT, land_raw

logger = logging.getLogger(__name__)


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


def run_backfill():
    """
    Run a backfill of the raw landing zone for the specified Congress number.
    """
    # Hard-coded to one bill for now; listing a whole Congress comes next.
    batch_ts = datetime.now(timezone.utc)
    with CongressClient() as client:
        paths = land_bill(client, 119, "hr", 134, batch_ts)
    for path in paths:  
        logger.info("Landed %s", path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_backfill()
