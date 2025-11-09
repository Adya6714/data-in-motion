import hashlib, time
from app.services.common.storage import get_client 

def sha256_stream(stream, chunk=1024*1024):
    h=hashlib.sha256()
    while True:
        b = stream.read(chunk)
        if not b: break
        h.update(b)
    return h.hexdigest()

def copy_idempotent(src, dst, key):
    scli = get_client(src) ; dcli = get_client(dst)
    sinfo = scli.stat(key)
    try:
        dinfo = dcli.stat(key)
        if dinfo.size==sinfo.size and dinfo.etag==sinfo.etag:
            return {"status":"noop"}
    except FileNotFoundError:
        pass
   
    r = scli.get(key)
    tmp = key+".part"
    dcli.put_stream(tmp, r)
    dinfo = dcli.stat(tmp)
    if dinfo.size != sinfo.size:
        dcli.delete(tmp); raise RuntimeError("size mismatch")
    dcli.rename(tmp, key)
    return {"status":"copied", "size": dinfo.size}


