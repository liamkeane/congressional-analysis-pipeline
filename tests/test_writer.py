import json
from datetime import datetime, timedelta, timezone

import pytest

from landing.writer import land_raw, landing_path

BATCH_TS = datetime(2026, 10, 24, 15, 30, tzinfo=timezone.utc)


def test_land_raw_writes_envelope_under_partitioned_path(tmp_path):
    path = land_raw({"bill": {"number": "1"}}, "bill", 118, "118-hr-1", BATCH_TS, root=tmp_path)

    assert path == tmp_path / "bill" / "dt=2026-10-24" / "congress=118" / "118-hr-1.json"
    record = json.loads(path.read_text())
    assert record == {
        "source": "bill",
        "congress": 118,
        "entity_id": "118-hr-1",
        "batch_ts": "2026-10-24T15:30:00+00:00",
        "payload": {"bill": {"number": "1"}},
    }


def test_no_congress_partition_when_congress_is_none(tmp_path):
    path = land_raw({}, "member", None, "A000001", BATCH_TS, root=tmp_path)
    assert path == tmp_path / "member" / "dt=2026-10-24" / "A000001.json"


def test_dt_partition_uses_utc_date(tmp_path):
    late_evening_eastern = datetime(2026, 10, 24, 22, 0, tzinfo=timezone(timedelta(hours=-5)))
    path = landing_path("bill", 118, "x", late_evening_eastern, root=tmp_path)
    assert path.parent.parent.name == "dt=2026-10-25"


def test_unsafe_characters_in_ids_are_neutralized(tmp_path):
    path = landing_path("bill", None, "../../etc/passwd", BATCH_TS, root=tmp_path)
    assert path.parent == tmp_path / "bill" / "dt=2026-10-24"
    assert "/" not in path.name


def test_naive_batch_ts_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="timezone-aware"):
        land_raw({}, "bill", 118, "x", datetime(2026, 10, 24), root=tmp_path)


def test_overwrite_leaves_no_temp_files(tmp_path):
    land_raw({"v": 1}, "bill", 118, "x", BATCH_TS, root=tmp_path)
    path = land_raw({"v": 2}, "bill", 118, "x", BATCH_TS, root=tmp_path)

    assert json.loads(path.read_text())["payload"] == {"v": 2}
    assert [p.name for p in path.parent.iterdir()] == ["x.json"]
