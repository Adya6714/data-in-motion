from dataclasses import dataclass
from typing import BinaryIO
from .s3_client import client_for, get_bucket

@dataclass
class ObjInfo:
    size: int
    etag: str

class S3Wrapper:
    def __init__(self, name: str):
        self.name = name
        self.client = client_for(name)
        self.bucket = get_bucket(name)

    def stat(self, key: str) -> ObjInfo:
        try:
            r = self.client.head_object(Bucket=self.bucket, Key=key)
            return ObjInfo(size=int(r['ContentLength']), etag=r['ETag'].strip('"'))
        except self.client.exceptions.NoSuchKey:
            raise FileNotFoundError(key)

    def get(self, key: str) -> BinaryIO:
        r = self.client.get_object(Bucket=self.bucket, Key=key)
        return r['Body']

    def put_stream(self, key: str, stream: BinaryIO):
        self.client.upload_fileobj(stream, Bucket=self.bucket, Key=key)

    def delete(self, key: str):
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def rename(self, src: str, dst: str):
        self.client.copy_object(Bucket=self.bucket, CopySource={'Bucket': self.bucket, 'Key': src}, Key=dst)
        self.delete(src)

def get_client(name: str) -> S3Wrapper:
    return S3Wrapper(name)


