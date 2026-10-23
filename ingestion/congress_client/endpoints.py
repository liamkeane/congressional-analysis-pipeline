"""
congress_client/endpoints.py

One function per congress.gov API resource. Every function takes a
CongressClient instance as its first argument and returns either:
  - an Iterator[dict] for list/collection endpoints (pagination handled
    internally by client.get_paginated), or
  - a dict for single-resource "detail" endpoints (client.get_congress).

client.py interface these are built on:

    class CongressClient:
        def get_congress(self, path: str, params: dict | None = None) -> dict:
            \"\"\"Single request. Returns the parsed JSON response body.\"\"\"

        def get_paginated(
            self, path: str, params: dict | None = None
        ) -> Iterator[dict]:
            \"\"\"
            Follows `pagination.next` until exhausted. Yields individual
            items already unwrapped from the top-level envelope (e.g. yields
            each bill dict, not the `{"bills": [...]}` wrapper).
            \"\"\"

Design notes:
  - Functions are intentionally thin: build the path, pass through params,
    return what the client gives back. No parsing/flattening here -- that
    belongs in staging models downstream of the raw landing layer.
  - Detail endpoints return the full envelope (e.g. `{"bill": {...},
    "request": {...}}`), so the raw landing layer keeps everything.
  - `bill_type` is passed lowercase (e.g. "hr", "s", "hjres") to match the
    API's own convention; callers should normalize before calling in.
  - `from_date` / `to_date` map to the API's `fromDateTime` / `toDateTime`
    query params, which filter on `updateDate`, not on any legislative
    action date. Format expected by the API: "YYYY-MM-DDT00:00:00Z".
"""

from typing import Iterator, Optional

from .client import CongressClient


def _date_params(from_date: Optional[str], to_date: Optional[str]) -> dict:
    params = {}
    if from_date:
        params["fromDateTime"] = from_date
    if to_date:
        params["toDateTime"] = to_date
    return params


# --------------------------------------------------------------------------
# Congresses
# --------------------------------------------------------------------------

def get_congresses(client: CongressClient) -> Iterator[dict]:
    """All congresses, with their sessions and start/end dates."""
    return client.get_paginated("/congress")


def get_congress_detail(client: CongressClient, congress: int) -> dict:
    return client.get_congress(f"/congress/{congress}")


def get_current_congress(client: CongressClient) -> dict:
    """Useful for defaulting scope without hardcoding a congress number."""
    return client.get_congress("/congress/current")


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
    if bill_type is not None and congress is None:
        raise ValueError("bill_type requires congress")

    if congress is not None and bill_type is not None:
        path = f"/bill/{congress}/{bill_type}"
    elif congress is not None:
        path = f"/bill/{congress}"
    else:
        path = "/bill"

    yield from client.get_paginated(path, params=_date_params(from_date, to_date))


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


def get_bill_committees(
    client: CongressClient, congress: int, bill_type: str, bill_number: int
) -> Iterator[dict]:
    """
    Committees a bill was referred to, each with its own `activities`
    (Referred To, Markup By, Reported By, ...) -- feeds the bill-to-committee
    bridge and the committee-bottleneck analysis.
    """
    return client.get_paginated(
        f"/bill/{congress}/{bill_type}/{bill_number}/committees"
    )


def get_bill_cosponsors(
    client: CongressClient, congress: int, bill_type: str, bill_number: int
) -> Iterator[dict]:
    """
    Includes withdrawn cosponsors (they carry `sponsorshipWithdrawnDate`) --
    don't treat this as append-only downstream.
    """
    return client.get_paginated(
        f"/bill/{congress}/{bill_type}/{bill_number}/cosponsors"
    )


def get_bill_subjects(
    client: CongressClient, congress: int, bill_type: str, bill_number: int
) -> Iterator[dict]:
    """
    Yields legislative subject terms only. The response nests them under
    `subjects.legislativeSubjects`; the sibling `policyArea` is dropped by
    the client's unwrap, but the same value is on get_bill_detail().
    """
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
# Summaries
# (Collection-level CRS summaries, filterable by updateDate -- cheaper than
# re-pulling per-bill summaries for every updated bill.)
# --------------------------------------------------------------------------

def get_summaries(
    client: CongressClient,
    congress: Optional[int] = None,
    bill_type: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> Iterator[dict]:
    if bill_type is not None and congress is None:
        raise ValueError("bill_type requires congress")

    if congress is not None and bill_type is not None:
        path = f"/summaries/{congress}/{bill_type}"
    elif congress is not None:
        path = f"/summaries/{congress}"
    else:
        path = "/summaries"

    yield from client.get_paginated(path, params=_date_params(from_date, to_date))


# --------------------------------------------------------------------------
# Members
# --------------------------------------------------------------------------

def get_members(
    client: CongressClient,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    current_member: Optional[bool] = None,
) -> Iterator[dict]:
    """
    List members. Members change rarely relative to bills, so in practice
    you'll likely call this without date filters on a periodic (e.g. weekly)
    full pull rather than watermark-filtering it like bills.
    """
    params = _date_params(from_date, to_date)
    if current_member is not None:
        params["currentMember"] = str(current_member).lower()
    yield from client.get_paginated("/member", params=params)


def get_members_by_congress(
    client: CongressClient, congress: int, current_member: Optional[bool] = None
) -> Iterator[dict]:
    """
    Members who served in a given congress -- the natural scope if you're
    only modeling recent congresses. Note that by default the API returns
    only members still serving; pass current_member=False to include those
    who left mid-congress.
    """
    params = {}
    if current_member is not None:
        params["currentMember"] = str(current_member).lower()
    return client.get_paginated(f"/member/congress/{congress}", params=params)


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
    client: CongressClient,
    chamber: Optional[str] = None,
    congress: Optional[int] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> Iterator[dict]:
    """
    chamber: 'house' | 'senate' | 'joint', or None for all.
    congress narrows to committees active in that congress.
    """
    segments = ["/committee"]
    if congress is not None:
        segments.append(str(congress))
    if chamber:
        segments.append(chamber)
    path = "/".join(segments)

    yield from client.get_paginated(path, params=_date_params(from_date, to_date))


def get_committee_detail(
    client: CongressClient, chamber: str, committee_code: str
) -> dict:
    return client.get_congress(f"/committee/{chamber}/{committee_code}")


def get_committee_bills(
    client: CongressClient,
    chamber: str,
    committee_code: str,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> Iterator[dict]:
    """
    Bills associated with a committee, each with its `relationshipType`
    (e.g. "Referred to", "Reported by") -- the committee-side view of
    get_bill_committees.
    """
    return client.get_paginated(
        f"/committee/{chamber}/{committee_code}/bills",
        params=_date_params(from_date, to_date),
    )


# --------------------------------------------------------------------------
# Amendments
# --------------------------------------------------------------------------

def get_amendments(
    client: CongressClient,
    congress: Optional[int] = None,
    amendment_type: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> Iterator[dict]:
    """Same scoping rules as get_bills; amendment_type is 'hamdt' | 'samdt'."""
    if amendment_type is not None and congress is None:
        raise ValueError("amendment_type requires congress")

    if congress is not None and amendment_type is not None:
        path = f"/amendment/{congress}/{amendment_type}"
    elif congress is not None:
        path = f"/amendment/{congress}"
    else:
        path = "/amendment"

    yield from client.get_paginated(path, params=_date_params(from_date, to_date))


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


def get_amendment_amendments(
    client: CongressClient, congress: int, amendment_type: str, amendment_number: int
) -> Iterator[dict]:
    """Amendments to this amendment (second-degree amendments)."""
    return client.get_paginated(
        f"/amendment/{congress}/{amendment_type}/{amendment_number}/amendments"
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
    if session is not None and congress is None:
        raise ValueError("session requires congress")

    if congress is not None and session is not None:
        path = f"/house-vote/{congress}/{session}"
    elif congress is not None:
        path = f"/house-vote/{congress}"
    else:
        path = "/house-vote"

    yield from client.get_paginated(path, params=_date_params(from_date, to_date))


def get_house_vote_detail(
    client: CongressClient, congress: int, session: int, roll_call_number: int
) -> dict:
    return client.get_congress(f"/house-vote/{congress}/{session}/{roll_call_number}")


def get_house_vote_members(
    client: CongressClient, congress: int, session: int, roll_call_number: int
) -> Iterator[dict]:
    """
    Per-member positions for a given roll call -- feeds fct_votes. The
    response nests them under `houseRollCallVoteMemberVotes.results`; vote-
    level fields (question, result, date) come from get_house_vote_detail.
    """
    return client.get_paginated(
        f"/house-vote/{congress}/{session}/{roll_call_number}/members"
    )
