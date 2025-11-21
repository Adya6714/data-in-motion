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

    # Chaos: Latency
    import time
    from ..policy import chaos
    lat = chaos.get_latency()
    if lat > 0:
        time.sleep(lat / 1000.0)

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
    
    # Partial upload handling / Growing file detection
    # If source size is significantly different from expected or if we have some metadata indicating partial
    # For now, we'll assume if size > 0 but we can't read it properly, or if it's growing? 
    # Actually, the requirement is to "cater to all cloud related business that is incase if the file isn't uploaded entirely"
    # We will check if the source object is "partial" (simulated check, maybe based on metadata or naming convention)
    # For this implementation, we'll check if the key ends with ".part" or similar, or just rely on the ML model later.
    # But here in the migrator, we should probably skip if it looks incomplete.
    # Let's assume if the size is 0, it might be a placeholder.
    if sm.get("size") == 0:
         return {"status": "skipped", "reason": "empty_source"}

    # Growing File Detection: Check LastModified
    # If the file was modified very recently (e.g. < 5 seconds ago), it might still be writing.
    # Note: _head_meta doesn't return LastModified currently, we need to fetch it.
    # We'll do a quick check on the source object head again or update _head_meta.
    # For minimal change, let's just do it here.
    try:
        head = s.head_object(Bucket=sb, Key=key)
        last_modified = head.get("LastModified")
        if last_modified:
            import datetime
            # Ensure timezone awareness compatibility
            now = datetime.datetime.now(last_modified.tzinfo)
            if (now - last_modified).total_seconds() < 5:
                return {"status": "skipped", "reason": "file_growing"}
    except Exception:
        pass # Ignore if we can't check, proceed with caution

    # Retry loop for throttling (429/503)
    max_retries = 3
    backoff = 1
    for attempt in range(max_retries + 1):
        try:
            obj = s.get_object(Bucket=sb, Key=key)
            body = obj["Body"].read()
            d.put_object(Bucket=db, Key=key, Body=body)
            return {"status": "copied", "size": sm.get("size", 0), "version_token": uuid4().hex}
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if code in ("429", "503", "Throttling", "TooManyRequests", "SlowDown"):
                if attempt < max_retries:
                    import time
                    time.sleep(backoff)
                    backoff *= 2
                    continue
            raise e
    return {"status": "failed", "error": "max_retries_exceeded"}

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
