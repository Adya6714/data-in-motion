from sqlalchemy import func
from ..common.db import SessionLocal, MigrationTask, FileMeta
from ..common.s3_client import client_for, get_bucket, ensure_bucket
from ..observability.metrics import migration_jobs_total, migration_queue_gauge
from ..observability import alerts
from ..policy import security
from botocore.exceptions import ClientError

MAX_ATTEMPTS = 5
_QUEUE_STATUSES = ["queued", "running", "done", "failed", "cleanup"]


def _update_queue_metrics(session):
    counts = {status: 0 for status in _QUEUE_STATUSES}
    rows = (
        session.query(MigrationTask.status, func.count())
        .group_by(MigrationTask.status)
        .all()
    )
    for status, count in rows:
        counts[status] = count
    for status, count in counts.items():
        migration_queue_gauge.labels(status=status).set(count)
    queued = counts.get("queued", 0)
    if queued > 20:
        alerts.create_alert(
            "migration_backlog",
            "warning",
            f"{queued} migration tasks queued",
            {"queued": queued},
        )

def _head_meta(client, bucket: str, key: str):
    try:
        r = client.head_object(Bucket=bucket, Key=key)
        return {"etag": r.get("ETag", "").strip('"'), "size": int(r.get("ContentLength", 0))}
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in ("404", "NoSuchKey", "NotFound"):
            return None
        raise

from uuid import uuid4

def _ensure_and_copy_once(key: str, src: str, dst: str):
    s = client_for(src); d = client_for(dst)
    sb = get_bucket(src); db = get_bucket(dst)

    if security.is_encryption_enforced() and not security.endpoint_is_encrypted(dst):
        return {"status": "blocked", "reason": "destination_not_encrypted"}

    ensure_bucket(src)
    ensure_bucket(dst)

    sm = _head_meta(s, sb, key)
    dm = _head_meta(d, db, key)

    if sm and dm and sm.get("etag") == dm.get("etag") and sm.get("size") == dm.get("size"):
        return {"status": "noop"}

    if not sm:
        if dm:
            return {"status": "noop"}
        return {"status": "missing_source"}

    obj = s.get_object(Bucket=sb, Key=key)
    body = obj["Body"].read()
    d.put_object(Bucket=db, Key=key, Body=body)
    return {"status": "copied", "size": sm.get("size", 0), "version_token": uuid4().hex}

def _cleanup_once(key: str, src: str):
    s = client_for(src)
    sb = get_bucket(src)
    try:
        s.delete_object(Bucket=sb, Key=key)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in ("404", "NoSuchKey", "NotFound"):
            return {"status": "noop"}
        raise
    return {"status": "deleted"}

def process_queue_once():
    with SessionLocal() as s:
        t = (
            s.query(MigrationTask)
            .filter(MigrationTask.status.in_(["queued", "cleanup", "failed"]))
            .order_by(MigrationTask.created_at.asc())
            .first()
        )
        if not t:
            _update_queue_metrics(s)
            return False

        if t.status in ("queued", "failed"):
            t.status = "running"; s.commit()
            try:
                r = _ensure_and_copy_once(t.key, t.src, t.dst)
                if r["status"] in ("copied", "noop"):
                    t.status = "done"; t.error = ""
                    migration_jobs_total.labels(result=r["status"]).inc()
                    if r["status"] == "copied" and "version_token" in r:
                        with SessionLocal() as inner:
                            fm = inner.query(FileMeta).filter_by(key=t.key).first()
                            if fm:
                                fm.version_token = r["version_token"]
                                inner.commit()
                elif r["status"] == "missing_source":
                    t.status = "failed"; t.error = "missing_source"
                    migration_jobs_total.labels(result="missing_source").inc()
                elif r["status"] == "blocked":
                    t.status = "failed"; t.error = r.get("reason", "blocked")
                    migration_jobs_total.labels(result="blocked").inc()
                else:
                    t.status = "failed"; t.error = str(r)
            except Exception as e:
                t.status = "failed"; t.error = str(e)
                migration_jobs_total.labels(result="error").inc()
            if t.status == "failed":
                t.attempts = (t.attempts or 0) + 1
                if t.attempts >= MAX_ATTEMPTS:
                    s.delete(t)
                else:
                    t.status = "queued"
            s.commit()
            _update_queue_metrics(s)
            return True

        if t.status == "cleanup":
            try:
                r = _cleanup_once(t.key, t.src)
                t.status = "done"; t.error = ""
                migration_jobs_total.labels(result=r["status"]).inc()
            except Exception as e:
                t.status = "failed"; t.error = str(e)
                migration_jobs_total.labels(result="cleanup_error").inc()
            if t.status == "failed":
                t.attempts = (t.attempts or 0) + 1
                if t.attempts >= MAX_ATTEMPTS:
                    s.delete(t)
                else:
                    t.status = "cleanup"
            s.commit()
            _update_queue_metrics(s)
            return True

        return False
