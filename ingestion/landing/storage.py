"""
landing/storage.py

Where landed files physically go. The writer and backfill only deal in
keys like `bill/dt=2026-11-07/congress=119/119-hr-1.json`; a storage
object turns those into files on local disk or objects in an S3 bucket.

Both backends offer the same two operations:
  - write_text(key, text): write one file, all-or-nothing
  - list_keys(prefix):     every key that starts with `prefix`

storage_from_env() picks one: S3 if LANDING_BUCKET is set, otherwise
local disk under raw_data/.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Protocol

from dotenv import load_dotenv

DEFAULT_ROOT = Path("raw_data")


class Storage(Protocol):
    def write_text(self, key: str, text: str) -> None: ...
    def list_keys(self, prefix: str) -> set[str]: ...


class LocalStorage:
    def __init__(self, root: Path = DEFAULT_ROOT):
        self.root = Path(root)

    def __str__(self) -> str:
        return str(self.root)

    def write_text(self, key: str, text: str) -> None:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file first, then rename it into place. The rename is
        # all-or-nothing, so a crash mid-write can't leave a half-written .json
        # that looks finished.
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.replace(path)

    def list_keys(self, prefix: str) -> set[str]:
        # S3 prefixes are plain string prefixes, not folders; match that here so
        # both backends answer the same question. Start from the deepest folder
        # the prefix names, so we don't walk the whole landing zone.
        base = self.root / prefix.rsplit("/", 1)[0] if "/" in prefix else self.root
        if not base.is_dir():
            return set()
        keys = (p.relative_to(self.root).as_posix() for p in base.rglob("*") if p.is_file())
        return {k for k in keys if k.startswith(prefix) and not k.endswith(".tmp")}


class S3Storage:
    def __init__(self, bucket: str, client=None):
        if client is None:
            import boto3  # imported here so local-only runs and tests don't need it

            client = boto3.client("s3")  # credentials come from `aws login` / the usual AWS config
        self.bucket = bucket
        self._s3 = client

    def __str__(self) -> str:
        return f"s3://{self.bucket}"

    def write_text(self, key: str, text: str) -> None:
        # A PUT is already all-or-nothing: the object appears complete or not at all.
        self._s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=text.encode("utf-8"),
            ContentType="application/json",
        )

    def list_keys(self, prefix: str) -> set[str]:
        # One LIST returns up to 1,000 keys, so this is far cheaper than
        # checking objects one at a time.
        keys = set()
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.update(obj["Key"] for obj in page.get("Contents", []))  # an empty page has no "Contents"
        return keys


def storage_from_env(bucket: Optional[str] = None) -> Storage:
    """S3 if a bucket is given or LANDING_BUCKET is set, otherwise local disk."""
    load_dotenv()
    bucket = bucket or os.environ.get("LANDING_BUCKET")
    return S3Storage(bucket) if bucket else LocalStorage()
