"""
congress_client.py

Minimal, safe starter client for the Congress.gov API (api.congress.gov, v3).

SAFE KEY HANDLING:
  - Your real API key lives in a local ".env" file (never committed to git).
  - ".env" is already listed in .gitignore.
  - This script loads the key at runtime via python-dotenv + os.environ.
  - Never hardcode the key in this file, never print it, never log it.

SETUP:
  1. pip install -r requirements.txt
  2. cp .env.example .env
  3. Edit .env and paste your real key in place of "your_key_here"
  4. Sign up for a free key at: https://api.congress.gov/sign-up/

USAGE:
  python congress_client.py
"""

import os
import time
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

load_dotenv()  # reads .env into environment variables, if present

API_KEY = os.environ.get("CONGRESS_API_KEY")
if not API_KEY:
    raise RuntimeError(
        "CONGRESS_API_KEY not found in .env file."
    )

BASE_URL = "https://api.congress.gov/v3"
RAW_DATA_DIR = Path("raw-data")  # will eventually be an S3 landing zone
RAW_DATA_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core request wrapper
# ---------------------------------------------------------------------------

def congress_get(path: str, params: dict | None = None, max_retries: int = 5) -> dict:
    """
    GET a Congress.gov API endpoint with the key injected safely as a query
    param (never in the URL you log or print), basic retry/backoff on
    rate-limit (429) and transient server errors, and JSON parsing.

    path: e.g. "/bill/119" or "/bill/119/hr/1/actions"
    """
    url = f"{BASE_URL}{path}"
    params = dict(params or {})
    params["api_key"] = API_KEY
    params.setdefault("format", "json")

    for attempt in range(1, max_retries + 1):
        response = requests.get(url, params=params, timeout=30)

        if response.status_code == 200:
            return response.json()

        if response.status_code == 429:
            wait = 2 ** attempt  # exponential backoff: 2, 4, 8, 16, 32s
            log.warning(f"Rate limited on {path}. Retrying in {wait}s (attempt {attempt}).")
            time.sleep(wait)
            continue

        if 500 <= response.status_code < 600:
            wait = 2 ** attempt
            log.warning(f"Server error {response.status_code} on {path}. Retrying in {wait}s.")
            time.sleep(wait)
            continue

        # Non-retryable error (bad request, bad key, not found, etc.)
        response.raise_for_status()

    raise RuntimeError(f"Failed to fetch {path} after {max_retries} retries.")


def paginate(path: str, params: dict | None = None, limit: int = 250):
    """
    Generator that walks all pages of a Congress.gov list endpoint.
    The API caps 'limit' at 250 per page and exposes pagination via
    a 'next' URL in the response.
    """
    params = dict(params or {})
    params["limit"] = limit
    offset = 0

    while True:
        params["offset"] = offset
        data = congress_get(path, params)

        # The list key name varies by endpoint (e.g. "bills", "members")
        list_key = next((k for k in data.keys() if isinstance(data[k], list)), None)
        if not list_key:
            break

        items = data[list_key]
        if not items:
            break

        yield from items

        pagination = data.get("pagination", {})
        if not pagination.get("next"):
            break
        offset += limit


def save_raw(data, filename: str):
    """Land raw JSON to disk, mirroring how you'd write to Blob/S3 later."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    day_dir = RAW_DATA_DIR / today
    day_dir.mkdir(parents=True, exist_ok=True)
    out_path = day_dir / filename
    out_path.write_text(json.dumps(data, indent=2))
    log.info(f"Wrote {out_path}")


# ---------------------------------------------------------------------------
# Example extract functions (map to your fact/dim tables)
# ---------------------------------------------------------------------------

def get_updated_bills(congress: int, since_days: int = 1) -> list[dict]:
    """
    Pull bills updated in the last `since_days` days for a given Congress.
    Feeds dim_bill.
    """
    from_dt = (datetime.utcnow() - timedelta(days=since_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    bills = list(
        paginate(f"/bill/{congress}", params={"fromDateTime": from_dt})
    )
    log.info(f"Fetched {len(bills)} updated bills for Congress {congress}.")
    return bills


def get_bill_actions(congress: int, bill_type: str, bill_number: int) -> list[dict]:
    """Feeds fct_bill_actions."""
    data = congress_get(f"/bill/{congress}/{bill_type}/{bill_number}/actions")
    return data.get("actions", [])


def get_bill_cosponsors(congress: int, bill_type: str, bill_number: int) -> list[dict]:
    """Feeds fct_bill_cosponsors."""
    data = congress_get(f"/bill/{congress}/{bill_type}/{bill_number}/cosponsors")
    return data.get("cosponsors", [])


def get_bill_subjects(congress: int, bill_type: str, bill_number: int) -> dict:
    """Feeds bridge_bill_subject."""
    data = congress_get(f"/bill/{congress}/{bill_type}/{bill_number}/subjects")
    return data.get("subjects", {})


# ---------------------------------------------------------------------------
# Example run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    CONGRESS = 119

    updated_bills = get_updated_bills(CONGRESS, since_days=1)
    save_raw(updated_bills, "bills_updated.json")

    # For each updated bill, pull the related sub-resources.
    # (In your Airflow DAG, this loop becomes a set of mapped/dynamic tasks.)
    for bill in updated_bills[:5]:  # small slice for a first test run
        bill_type = bill["type"].lower()
        bill_number = bill["number"]

        actions = get_bill_actions(CONGRESS, bill_type, bill_number)
        cosponsors = get_bill_cosponsors(CONGRESS, bill_type, bill_number)
        subjects = get_bill_subjects(CONGRESS, bill_type, bill_number)

        save_raw(actions, f"actions_{bill_type}{bill_number}.json")
        save_raw(cosponsors, f"cosponsors_{bill_type}{bill_number}.json")
        save_raw(subjects, f"subjects_{bill_type}{bill_number}.json")

    log.info("Done.")