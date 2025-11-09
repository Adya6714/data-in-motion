import os
import asyncio
import json
import random
import math
from uuid import uuid4
from aiokafka import AIOKafkaProducer
from ..common.config import KAFKA_BOOTSTRAP
from ..common.db import SessionLocal, FileMeta

TOPIC = os.getenv("ACCESS_TOPIC", "file_access")


def pick_weighted(keys, skew: float = 0.7):
    n = len(keys)
    if n == 0:
        return None
    weights = [math.pow(1.0 - i / n, 3) for i in range(n)]
    weights = [w ** (1 + 3 * skew) for w in weights]
    total = sum(weights)
    weights = [w / total for w in weights]
    return random.choices(keys, weights=weights, k=1)[0]


def fetch_keys() -> list[str]:
    with SessionLocal() as s:
        return [k for (k,) in s.query(FileMeta.key)]


def new_key(prefix: str = "stream") -> str:
    return f"{prefix}/{uuid4().hex[:10]}.obj"


async def main():
    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        acks="all",
        enable_idempotence=True,
        transactional_id="dim-producer",
    )
    await producer.start()
    try:
        keys = fetch_keys()
        if not keys:
            keys = [new_key()]
        await producer.begin_transaction()
        while True:
            if random.random() < 0.20:
                keys = fetch_keys() or keys
            if random.random() < 0.15 or not keys:
                key = new_key()
                keys.append(key)
            else:
                key = pick_weighted(keys, skew=0.7) or new_key()
            payload = json.dumps({"key": key, "action": "read"}).encode()
            await producer.send_and_wait(TOPIC, payload)
            await producer.commit_transaction()
            await producer.begin_transaction()
            await asyncio.sleep(random.uniform(0.1, 2.0))
    finally:
        try:
            await producer.abort_transaction()
        except Exception:
            pass
        await producer.stop()


if __name__ == "__main__":
    asyncio.run(main())
