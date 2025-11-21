import os, json, asyncio
from datetime import datetime, timezone
from aiokafka import AIOKafkaConsumer
from sqlalchemy import text
from ..common.db import SessionLocal, FileMeta
from ..optimizer.service import evaluate_and_queue

BOOT = os.getenv("KAFKA_BOOTSTRAP", "redpanda:9092")
TOPIC = os.getenv("ACCESS_TOPIC", "access_events")
GROUP_ID = os.getenv("CONSUMER_GROUP", "dim-consumer")


async def consume():
    consumer = AIOKafkaConsumer(
        TOPIC,
        bootstrap_servers=BOOT,
        value_deserializer=lambda v: json.loads(v.decode()),
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        isolation_level="read_committed",
        group_id=GROUP_ID,
    )
    await consumer.start()
    try:
        async for msg in consumer:
            rec = msg.value
            key = rec.get("key")
            now = datetime.fromtimestamp(
            rec.get("ts", datetime.now().timestamp()) / 1000.0, tz=timezone.utc
            )
            if not key:
                await consumer.commit()
                continue
            with SessionLocal() as s:
                fm = s.query(FileMeta).filter_by(key=key).first()
                if not fm:
                    from ..common.config import DEFAULT_PRIMARY

                    fm = FileMeta(
                        key=key,
                        size_bytes=0,
                        content_type="",
                        checksum="",
                        tier="cold",
                        location_primary=DEFAULT_PRIMARY,
                        location_replicas="",
                        last_access_ts=now,
                        access_1h=0,
                        access_24h=0,
                    )
                    s.add(fm)
                    s.flush()
                fm.access_1h += 1
                fm.access_24h += 1
                fm.last_access_ts = now
                s.execute(
                    text(
                        "INSERT INTO access_event(key, ts, action) VALUES (:k, :ts, :act)"
                    ),
                    {"k": key, "ts": now, "act": rec.get("action", "read")},
                )
                s.commit()
            evaluate_and_queue(key)
            await consumer.commit()
    finally:
        await consumer.stop()


def run():
    asyncio.run(consume())


if __name__ == "__main__":
    run()
