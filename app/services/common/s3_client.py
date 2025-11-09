from typing import Dict
import boto3, botocore
from botocore.exceptions import ClientError
from .config import S3_ENDPOINTS, ENCRYPTION_ENABLED
from cryptography.fernet import Fernet
from ..policy import chaos
_clients: Dict[str, boto3.client] = {}
_keys: Dict[str, bytes] = {}

def client_for(name: str):
    if name in chaos.get_failed_endpoints():
        raise RuntimeError(f"Endpoint {name} is in chaos fail list")
    if name in _clients: return _clients[name]
    ep = next(e for e in S3_ENDPOINTS if e.name==name)
    c = boto3.client("s3",
        aws_access_key_id=ep.access_key, aws_secret_access_key=ep.secret_key,
        endpoint_url=ep.url, config=botocore.client.Config(signature_version="s3v4"),
        region_name="us-east-1")
    _clients[name]=c; return c

def get_bucket(name:str)->str:
    ep = next(e for e in S3_ENDPOINTS if e.name==name); return ep.bucket

def ensure_bucket(name: str):
    c = client_for(name)
    b = get_bucket(name)
    try:
        c.head_bucket(Bucket=b)
    except ClientError as e:
        code = int(e.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
        if code == 404:
            c.create_bucket(Bucket=b)
        else:
            raise
