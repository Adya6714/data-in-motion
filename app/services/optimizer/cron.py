import time
import logging
import os

from ..common.db import SessionLocal, FileMeta
from .service import evaluate_and_queue

log = logging.getLogger("optimizer-cron")


def run(interval_sec: int = None):
    if interval_sec is None:
        try:
            interval_sec = int(os.getenv("OPTIMIZER_INTERVAL_S", "10"))
        except Exception:
            interval_sec = 60
    while True:
        try:
            with SessionLocal() as s:
                keys = [k for (k,) in s.query(FileMeta.key)]
            log.info("Re-optimizing %d keys", len(keys))

            for k in keys:
                try:
                    evaluate_and_queue(k)
                except Exception:
                    log.exception("evaluate_and_queue failed for key=%s", k)

        except Exception:
            log.exception("optimizer cron iteration failed")

        time.sleep(interval_sec)


if __name__ == "__main__":
    run()
