import pytest

from congress_client import endpoints


class RecordingClient:
    """Records the path/params each endpoint hands to the client."""

    def __init__(self):
        self.calls = []

    def get_paginated(self, path, params=None):
        self.calls.append(("paginated", path, params))
        return iter([{"path": path}])

    def get_congress(self, path, params=None):
        self.calls.append(("single", path, params))
        return {"path": path}


@pytest.fixture
def client():
    return RecordingClient()


# ---------------------------------------------------------------------------
# Argument validation is eager (raises at call time, not on first iteration)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "func, kwargs, message",
    [
        (endpoints.get_bills, {"bill_type": "hr"}, "bill_type requires congress"),
        (endpoints.get_summaries, {"bill_type": "s"}, "bill_type requires congress"),
        (endpoints.get_amendments, {"amendment_type": "samdt"}, "amendment_type requires congress"),
        (endpoints.get_house_votes, {"session": 1}, "session requires congress"),
    ],
)
def test_invalid_scope_raises_without_iterating(client, func, kwargs, message):
    with pytest.raises(ValueError, match=message):
        func(client, **kwargs)  # deliberately not wrapped in list()
    assert client.calls == []


@pytest.mark.parametrize(
    "func",
    [
        endpoints.get_bills,
        endpoints.get_summaries,
        endpoints.get_members,
        endpoints.get_committees,
        endpoints.get_amendments,
        endpoints.get_house_votes,
    ],
)
def test_list_functions_call_client_immediately(client, func):
    # Returning (not yielding from) the client's iterator means the endpoint
    # function runs to completion as soon as it's called.
    result = func(client)
    assert len(client.calls) == 1
    assert list(result) == [{"path": client.calls[0][1]}]


# ---------------------------------------------------------------------------
# Path scoping for nested collection endpoints
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "func, kwargs, expected_path",
    [
        (endpoints.get_bills, {}, "/bill"),
        (endpoints.get_bills, {"congress": 119}, "/bill/119"),
        (endpoints.get_bills, {"congress": 119, "bill_type": "hr"}, "/bill/119/hr"),
        (endpoints.get_summaries, {}, "/summaries"),
        (endpoints.get_summaries, {"congress": 118}, "/summaries/118"),
        (endpoints.get_summaries, {"congress": 118, "bill_type": "s"}, "/summaries/118/s"),
        (endpoints.get_amendments, {}, "/amendment"),
        (endpoints.get_amendments, {"congress": 117}, "/amendment/117"),
        (endpoints.get_amendments, {"congress": 117, "amendment_type": "hamdt"}, "/amendment/117/hamdt"),
        (endpoints.get_house_votes, {}, "/house-vote"),
        (endpoints.get_house_votes, {"congress": 119}, "/house-vote/119"),
        (endpoints.get_house_votes, {"congress": 119, "session": 2}, "/house-vote/119/2"),
        (endpoints.get_committees, {}, "/committee"),
        (endpoints.get_committees, {"chamber": "house"}, "/committee/house"),
        (endpoints.get_committees, {"congress": 119}, "/committee/119"),
        (endpoints.get_committees, {"congress": 119, "chamber": "senate"}, "/committee/119/senate"),
    ],
)
def test_collection_paths(client, func, kwargs, expected_path):
    func(client, **kwargs)
    assert client.calls[0][1] == expected_path


# ---------------------------------------------------------------------------
# Query params
# ---------------------------------------------------------------------------

def test_date_filters_map_to_api_names(client):
    endpoints.get_bills(
        client, congress=119, from_date="2025-01-01T00:00:00Z", to_date="2025-02-01T00:00:00Z"
    )
    assert client.calls[0][2] == {
        "fromDateTime": "2025-01-01T00:00:00Z",
        "toDateTime": "2025-02-01T00:00:00Z",
    }


def test_no_date_filters_sends_empty_params(client):
    endpoints.get_bills(client, congress=119)
    assert client.calls[0][2] == {}


@pytest.mark.parametrize("value, expected", [(True, "true"), (False, "false")])
def test_current_member_is_lowercase_string(client, value, expected):
    endpoints.get_members(client, current_member=value)
    endpoints.get_members_by_congress(client, 119, current_member=value)
    assert client.calls[0][2] == {"currentMember": expected}
    assert client.calls[1] == ("paginated", "/member/congress/119", {"currentMember": expected})


def test_current_member_omitted_by_default(client):
    endpoints.get_members(client)
    assert "currentMember" not in client.calls[0][2]


def test_committee_bills_passes_date_filters(client):
    endpoints.get_committee_bills(client, "house", "hsag00", from_date="2025-01-01T00:00:00Z")
    assert client.calls[0] == (
        "paginated",
        "/committee/house/hsag00/bills",
        {"fromDateTime": "2025-01-01T00:00:00Z"},
    )


# ---------------------------------------------------------------------------
# Detail and sub-resource endpoints route to the right client method
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "func, args, expected",
    [
        (endpoints.get_congresses, (), ("paginated", "/congress")),
        (endpoints.get_congress_detail, (119,), ("single", "/congress/119")),
        (endpoints.get_current_congress, (), ("single", "/congress/current")),
        (endpoints.get_bill_detail, (119, "s", 5), ("single", "/bill/119/s/5")),
        (endpoints.get_bill_actions, (119, "s", 5), ("paginated", "/bill/119/s/5/actions")),
        (endpoints.get_bill_amendments, (119, "s", 5), ("paginated", "/bill/119/s/5/amendments")),
        (endpoints.get_bill_committees, (119, "s", 5), ("paginated", "/bill/119/s/5/committees")),
        (endpoints.get_bill_cosponsors, (119, "s", 5), ("paginated", "/bill/119/s/5/cosponsors")),
        (endpoints.get_bill_subjects, (119, "s", 5), ("paginated", "/bill/119/s/5/subjects")),
        (endpoints.get_bill_summaries, (119, "s", 5), ("paginated", "/bill/119/s/5/summaries")),
        (endpoints.get_bill_titles, (119, "s", 5), ("paginated", "/bill/119/s/5/titles")),
        (endpoints.get_bill_text_versions, (119, "s", 5), ("paginated", "/bill/119/s/5/text")),
        (endpoints.get_bill_related_bills, (119, "s", 5), ("paginated", "/bill/119/s/5/relatedbills")),
        (endpoints.get_member_detail, ("A000001",), ("single", "/member/A000001")),
        (endpoints.get_member_sponsored_legislation, ("A000001",), ("paginated", "/member/A000001/sponsored-legislation")),
        (endpoints.get_member_cosponsored_legislation, ("A000001",), ("paginated", "/member/A000001/cosponsored-legislation")),
        (endpoints.get_committee_detail, ("senate", "ssfi00"), ("single", "/committee/senate/ssfi00")),
        (endpoints.get_amendment_detail, (119, "samdt", 10), ("single", "/amendment/119/samdt/10")),
        (endpoints.get_amendment_actions, (119, "samdt", 10), ("paginated", "/amendment/119/samdt/10/actions")),
        (endpoints.get_amendment_amendments, (119, "samdt", 10), ("paginated", "/amendment/119/samdt/10/amendments")),
        (endpoints.get_amendment_cosponsors, (119, "samdt", 10), ("paginated", "/amendment/119/samdt/10/cosponsors")),
        (endpoints.get_amendment_text_versions, (119, "samdt", 10), ("paginated", "/amendment/119/samdt/10/text")),
        (endpoints.get_house_vote_detail, (119, 1, 42), ("single", "/house-vote/119/1/42")),
        (endpoints.get_house_vote_members, (119, 1, 42), ("paginated", "/house-vote/119/1/42/members")),
    ],
)
def test_endpoint_routing(client, func, args, expected):
    func(client, *args)
    method, path, _ = client.calls[0]
    assert (method, path) == expected
