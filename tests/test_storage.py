import pytest

from ingestion.landing import storage as storage_module
from ingestion.landing.storage import LocalStorage, S3Storage, storage_from_env


class FakeS3:
    """
    Stands in for a boto3 S3 client: keeps objects in a dict and pages
    list_objects_v2 results `page_size` keys at a time, like the real one does at 1,000.
    """

    def __init__(self, page_size=2):
        self.objects = {}
        self.page_size = page_size
        self.puts = []

    def put_object(self, Bucket, Key, Body, ContentType):
        self.puts.append({"Bucket": Bucket, "Key": Key, "ContentType": ContentType})
        self.objects[Key] = Body

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, Bucket, Prefix):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        if not keys:
            yield {"KeyCount": 0}  # the real API leaves out "Contents" when nothing matches
        for i in range(0, len(keys), self.page_size):
            yield {"Contents": [{"Key": k} for k in keys[i:i + self.page_size]]}


# ---------------------------------------------------------------------------
# Both backends answer the same questions the same way
# ---------------------------------------------------------------------------

@pytest.fixture(params=["local", "s3"])
def storage(request, tmp_path):
    if request.param == "local":
        return LocalStorage(tmp_path)
    return S3Storage("test-bucket", client=FakeS3())


def test_lists_keys_under_a_prefix(storage):
    for key in ("bill/dt=1/a.json", "bill/dt=1/b.json", "bill/dt=1/c.json", "bill/dt=2/d.json", "bill_actions/dt=1/a.json"):
        storage.write_text(key, "{}")

    assert storage.list_keys("bill/dt=1/") == {"bill/dt=1/a.json", "bill/dt=1/b.json", "bill/dt=1/c.json"}


def test_prefix_is_a_string_prefix_not_a_folder(storage):
    storage.write_text("bill/a.json", "{}")
    storage.write_text("bill_actions/a.json", "{}")

    assert storage.list_keys("bill") == {"bill/a.json", "bill_actions/a.json"}


def test_missing_prefix_lists_nothing(storage):
    assert storage.list_keys("nothing/here/") == set()


# ---------------------------------------------------------------------------
# Backend specifics
# ---------------------------------------------------------------------------

def test_local_write_leaves_no_temp_file(tmp_path):
    LocalStorage(tmp_path).write_text("bill/a.json", "{}")

    assert [p.name for p in (tmp_path / "bill").iterdir()] == ["a.json"]
    assert (tmp_path / "bill" / "a.json").read_text() == "{}"


def test_s3_write_puts_utf8_json_object():
    s3 = FakeS3()

    S3Storage("test-bucket", client=s3).write_text("bill/a.json", '{"title": "Café"}')

    assert s3.puts == [{"Bucket": "test-bucket", "Key": "bill/a.json", "ContentType": "application/json"}]
    assert s3.objects["bill/a.json"] == '{"title": "Café"}'.encode("utf-8")


# ---------------------------------------------------------------------------
# storage_from_env
# ---------------------------------------------------------------------------

def test_defaults_to_local_when_no_bucket(monkeypatch):
    monkeypatch.setattr(storage_module, "load_dotenv", lambda: None)
    monkeypatch.delenv("LANDING_BUCKET", raising=False)

    assert isinstance(storage_from_env(), LocalStorage)


def test_uses_s3_when_bucket_is_set(monkeypatch):
    monkeypatch.setattr(storage_module, "load_dotenv", lambda: None)
    monkeypatch.setenv("LANDING_BUCKET", "my-bucket")
    monkeypatch.setattr(storage_module, "S3Storage", lambda bucket: ("s3", bucket))

    assert storage_from_env() == ("s3", "my-bucket")
