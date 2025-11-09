from __future__ import annotations

from typing import Iterable, List, Tuple

from ..common.s3_client import client_for, get_bucket


def _iter_objects(client, bucket: str, prefix: str | None = None) -> Iterable[Tuple[str, int]]:
    paginator = client.get_paginator("list_objects_v2")
    kwargs = {"Bucket": bucket}
    if prefix:
        kwargs["Prefix"] = prefix
    for page in paginator.paginate(**kwargs):
        for entry in page.get("Contents", []):
            yield entry["Key"], entry.get("Size", 0)


def _copy_object(src_client, dst_client, src_bucket, dst_bucket, key: str) -> int:
    obj = src_client.get_object(Bucket=src_bucket, Key=key)
    body = obj["Body"].read()
    dst_client.put_object(Bucket=dst_bucket, Key=key, Body=body)
    return len(body)


def rclone_sync(src: str, dst: str, prefix: str | None = None) -> dict:
    src_client = client_for(src)
    dst_client = client_for(dst)
    src_bucket = get_bucket(src)
    dst_bucket = get_bucket(dst)

    copied = 0
    bytes_total = 0
    for key, size in _iter_objects(src_client, src_bucket, prefix=prefix):
        bytes_total += _copy_object(src_client, dst_client, src_bucket, dst_bucket, key)
        copied += 1
    return {"copied": copied, "bytes": bytes_total, "prefix": prefix}


def s5cmd_copy(src: str, dst: str, prefix: str | None = None) -> dict:
    return rclone_sync(src, dst, prefix=prefix)
