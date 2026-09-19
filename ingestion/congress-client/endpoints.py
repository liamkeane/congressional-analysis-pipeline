"""
congress_client/endpoints.py

One function per congress.gov API resource. Every function takes a
CongressClient instance as its first argument and returns either:
  - a Generator[dict] for list/collection endpoints (pagination handled
    internally by client.get_paginated), or
  - a dict for single-resource "detail" endpoints (client.get).

ASSUMED client.py interface (adjust call sites below if yours differs):

    class CongressClient:
        def get(self, path: str, params: dict | None = None) -> dict:
            \"\"\"Single request. Returns the parsed JSON response body.\"\"\"

        def get_paginated(
            self, path: str, params: dict | None = None
        ) -> Iterator[dict]:
            \"\"\"
            Handles limit/offset pagination transparently, following the
            `pagination.next` URL (or incrementing offset) until exhausted.
            Yields individual items already unwrapped from the top-level
            envelope (e.g. yields each bill dict, not the `{"bills": [...]}`
            wrapper).
            \"\"\"

Design notes:
  - Functions are intentionally thin: build the path, pass through params,
    return what the client gives back. No parsing/flattening here -- that
    belongs in staging models downstream of the raw landing layer.
  - `bill_type` is passed lowercase (e.g. "hr", "s", "hjres") to match the
    API's own convention; callers should normalize before calling in.
  - `from_date` / `to_date` map to the API's `fromDateTime` / `toDateTime`
    query params, which filter on `updateDate`, not on any legislative
    action date. Format expected by the API: "YYYY-MM-DDT00:00:00Z".
"""

from typing import Iterator, Optional

from .client import CongressClient


# --------------------------------------------------------------------------
# Bills
# --------------------------------------------------------------------------

def get_bills(
    client: CongressClient,
    congress: Optional[int] = None,
    bill_type: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> Iterator[dict]:
    """
    List bills. Scope narrows as args are provided:
      - no congress: all bills across all congresses (rarely what you want)
      - congress only: all bills in that congress
      - congress + bill_type: all bills of that type in that congress

    from_date/to_date filter on updateDate -- this is the primary mechanism
    for incremental pulls (see watermark.py).
    """
    if congress is not None and bill_type is not None:
        path = f"/bill/{congress}/{bill_type}"
    elif congress is not None:
        path = f"/bill/{congress}"
    else:
        path = "/bill"

    params = {}
    if from_date:
        params["fromDateTime"] = from_date
    if to_date:
        params["toDateTime"] = to_date

    yield from client.get_paginated(path, params=params)


def get_bill_detail(
    client: CongressClient, congress: int, bill_type: str, bill_number: int
) -> dict:
    """Single bill detail (the endpoint you queried manually for S.5)."""
    return client.get_congress(f"/bill/{congress}/{bill_type}/{bill_number}")


def get_bill_actions(
    client: CongressClient, congress: int, bill_type: str, bill_number: int
) -> Iterator[dict]:
    return client.get_paginated(
        f"/bill/{congress}/{bill_type}/{bill_number}/actions"
    )


def get_bill_amendments(
    client: CongressClient, congress: int, bill_type: str, bill_number: int
) -> Iterator[dict]:
    return client.get_paginated(
        f"/bill/{congress}/{bill_type}/{bill_number}/amendments"
    )


def get_bill_cosponsors(
    client: CongressClient, congress: int, bill_type: str, bill_number: int
) -> Iterator[dict]:
    return client.get_paginated(
        f"/bill/{congress}/{bill_type}/{bill_number}/cosponsors"
    )


def get_bill_subjects(
    client: CongressClient, congress: int, bill_type: str, bill_number: int
) -> Iterator[dict]:
    return client.get_paginated(
        f"/bill/{congress}/{bill_type}/{bill_number}/subjects"
    )


def get_bill_summaries(
    client: CongressClient, congress: int, bill_type: str, bill_number: int
) -> Iterator[dict]:
    return client.get_paginated(
        f"/bill/{congress}/{bill_type}/{bill_number}/summaries"
    )


def get_bill_titles(
    client: CongressClient, congress: int, bill_type: str, bill_number: int
) -> Iterator[dict]:
    return client.get_paginated(
        f"/bill/{congress}/{bill_type}/{bill_number}/titles"
    )


def get_bill_text_versions(
    client: CongressClient, congress: int, bill_type: str, bill_number: int
) -> Iterator[dict]:
    return client.get_paginated(
        f"/bill/{congress}/{bill_type}/{bill_number}/text"
    )


def get_bill_related_bills(
    client: CongressClient, congress: int, bill_type: str, bill_number: int
) -> Iterator[dict]:
    return client.get_paginated(
        f"/bill/{congress}/{bill_type}/{bill_number}/relatedbills"
    )


# --------------------------------------------------------------------------
# Members
# --------------------------------------------------------------------------

def get_members(
    client: CongressClient,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> Iterator[dict]:
    """
    List members. Members change rarely relative to bills, so in practice
    you'll likely call this without date filters on a periodic (e.g. weekly)
    full pull rather than watermark-filtering it like bills.
    """
    params = {}
    if from_date:
        params["fromDateTime"] = from_date
    if to_date:
        params["toDateTime"] = to_date
    yield from client.get_paginated("/member", params=params)


def get_member_detail(client: CongressClient, bioguide_id: str) -> dict:
    return client.get_congress(f"/member/{bioguide_id}")


def get_member_sponsored_legislation(
    client: CongressClient, bioguide_id: str
) -> Iterator[dict]:
    return client.get_paginated(f"/member/{bioguide_id}/sponsored-legislation")


def get_member_cosponsored_legislation(
    client: CongressClient, bioguide_id: str
) -> Iterator[dict]:
    return client.get_paginated(f"/member/{bioguide_id}/cosponsored-legislation")


# --------------------------------------------------------------------------
# Committees
# --------------------------------------------------------------------------

def get_committees(
    client: CongressClient, chamber: Optional[str] = None
) -> Iterator[dict]:
    """chamber: 'house' | 'senate' | 'joint', or None for all."""
    path = f"/committee/{chamber}" if chamber else "/committee"
    yield from client.get_paginated(path)


def get_committee_detail(
    client: CongressClient, chamber: str, committee_code: str
) -> dict:
    return client.get_congress(f"/committee/{chamber}/{committee_code}")


# --------------------------------------------------------------------------
# Amendments
# --------------------------------------------------------------------------

def get_amendment_detail(
    client: CongressClient, congress: int, amendment_type: str, amendment_number: int
) -> dict:
    """amendment_type: 'hamdt' | 'samdt' (lowercase, per API convention)."""
    return client.get_congress(f"/amendment/{congress}/{amendment_type}/{amendment_number}")


def get_amendment_actions(
    client: CongressClient, congress: int, amendment_type: str, amendment_number: int
) -> Iterator[dict]:
    return client.get_paginated(
        f"/amendment/{congress}/{amendment_type}/{amendment_number}/actions"
    )


def get_amendment_cosponsors(
    client: CongressClient, congress: int, amendment_type: str, amendment_number: int
) -> Iterator[dict]:
    return client.get_paginated(
        f"/amendment/{congress}/{amendment_type}/{amendment_number}/cosponsors"
    )


def get_amendment_text_versions(
    client: CongressClient, congress: int, amendment_type: str, amendment_number: int
) -> Iterator[dict]:
    return client.get_paginated(
        f"/amendment/{congress}/{amendment_type}/{amendment_number}/text"
    )


# --------------------------------------------------------------------------
# House roll call votes
# (No Senate equivalent exists in the API as of this writing -- see prior
# discussion. Keep this isolated so adding a second, structurally different
# vote source later -- e.g. a Senate scraper -- doesn't require touching
# these functions.)
# --------------------------------------------------------------------------

def get_house_votes(
    client: CongressClient,
    congress: Optional[int] = None,
    session: Optional[int] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> Iterator[dict]:
    if congress is not None and session is not None:
        path = f"/house-vote/{congress}/{session}"
    elif congress is not None:
        path = f"/house-vote/{congress}"
    else:
        path = "/house-vote"

    params = {}
    if from_date:
        params["fromDateTime"] = from_date
    if to_date:
        params["toDateTime"] = to_date

    yield from client.get_paginated(path, params=params)


def get_house_vote_detail(
    client: CongressClient, congress: int, session: int, roll_call_number: int
) -> dict:
    return client.get_congress(f"/house-vote/{congress}/{session}/{roll_call_number}")


def get_house_vote_members(
    client: CongressClient, congress: int, session: int, roll_call_number: int
) -> Iterator[dict]:
    """Per-member positions for a given roll call -- feeds fct_votes."""
    return client.get_paginated(
        f"/house-vote/{congress}/{session}/{roll_call_number}/members"
    )