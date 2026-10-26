import json
from datetime import datetime, timedelta, timezone

import pytest

from ingestion.landing.writer import land_raw, landing_path

BATCH_TS = datetime(2026, 10, 25, 15, 30, tzinfo=timezone.utc)

# Trimmed-down copy of a real /bill/119/hr/134 response.
FAKE_BILL_RESPONSE = {
    "bill": {
        "congress": 119,
        "type": "HR",
        "number": "134",
        "title": "Protecting our Communities from Sexual Predators Act",
        "introducedDate": "2025-01-03",
        "policyArea": {"name": "Immigration"},
        "sponsors": [{"bioguideId": "B001260", "party": "R", "state": "FL"}],
    },
    "request": {"congress": "119", "billType": "hr", "billNumber": "134"},
}


def test_writes_file_at_partitioned_path(tmp_path):
    path = land_raw(FAKE_BILL_RESPONSE, "bill", 119, "119-hr-134", BATCH_TS, root=tmp_path)

    assert path == tmp_path / "bill" / "dt=2026-10-25" / "congress=119" / "119-hr-134.json"
    assert path.exists()


def test_file_holds_metadata_and_untouched_payload(tmp_path):
    path = land_raw(FAKE_BILL_RESPONSE, "bill", 119, "119-hr-134", BATCH_TS, root=tmp_path)

    record = json.loads(path.read_text())
    assert record == {
        "source": "bill",
        "congress": 119,
        "entity_id": "119-hr-134",
        "batch_ts": "2026-10-25T15:30:00+00:00",
        "payload": FAKE_BILL_RESPONSE,
    }


def test_list_payloads_are_saved_too(tmp_path):
    actions = [{"actionDate": "2025-01-03", "type": "IntroReferral"}]
    path = land_raw(actions, "bill_actions", 119, "119-hr-134", BATCH_TS, root=tmp_path)

    assert json.loads(path.read_text())["payload"] == actions


def test_dt_uses_the_utc_date(tmp_path):
    # 10pm in US Central on Oct 25 is already Oct 26 in UTC.
    central = timezone(timedelta(hours=-5))
    path = landing_path("bill", 119, "x", datetime(2026, 10, 25, 22, 0, tzinfo=central), root=tmp_path)

    assert path.parent.parent.name == "dt=2026-10-26"


def test_naive_batch_ts_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="timezone-aware"):
        land_raw({}, "bill", 119, "x", datetime(2026, 10, 25), root=tmp_path)


def test_rewriting_overwrites_and_leaves_no_temp_file(tmp_path):
    land_raw({"version": 1}, "bill", 119, "x", BATCH_TS, root=tmp_path)
    path = land_raw({"version": 2}, "bill", 119, "x", BATCH_TS, root=tmp_path)

    assert json.loads(path.read_text())["payload"] == {"version": 2}
    assert [p.name for p in path.parent.iterdir()] == ["x.json"]
