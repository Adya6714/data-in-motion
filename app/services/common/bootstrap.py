from .db import init_db, SessionLocal, FileMeta
from .config import S3_ENDPOINTS
from .s3_client import client_for, get_bucket
from botocore.exceptions import ClientError

def main():
    init_db()
    for ep in S3_ENDPOINTS:
        c = client_for(ep.name)
        try:
            c.create_bucket(Bucket=ep.bucket)
        except ClientError as e:
            if e.response['Error']['Code'] not in ('BucketAlreadyOwnedByYou','BucketAlreadyExists'):
                raise
    with SessionLocal() as s:
        if s.query(FileMeta).count()==0:
            s.add_all([
                FileMeta(key="logs/2025-11-06/app.log", size_bytes=2_000_000, content_type="text/plain",
                         tier="warm", location_primary="aws", location_replicas="azure"),
                FileMeta(key="media/cat.jpg", size_bytes=500_000, content_type="image/jpeg",
                         tier="hot", location_primary="gcp", location_replicas="aws"),
                FileMeta(key="archive/2023/10/report.parquet", size_bytes=50_000_000, content_type="application/octet-stream",
                         tier="cold", location_primary="azure", location_replicas="gcp"),
            ]); s.commit()
    print("DB & buckets ready.")
if __name__=="__main__": main()
