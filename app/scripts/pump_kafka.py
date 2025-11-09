import os, json, time, random
from aiokafka import AIOKafkaProducer
import asyncio

BOOT=os.getenv("KAFKA_BOOTSTRAP","redpanda:9092")
TOPIC=os.getenv("ACCESS_TOPIC","access_events")

async def main():
    p = AIOKafkaProducer(bootstrap_servers=BOOT,
                         value_serializer=lambda v: json.dumps(v).encode())
    await p.start()
    try:
        keys = [f"hot/{i:04d}.obj" for i in range(5)] + \
               [f"warm/{i:04d}.obj" for i in range(10)] + \
               [f"cold/{i:04d}.obj" for i in range(50)]
        while True:
            now=time.time()
            for k in keys:
                base = 0.8 if k.startswith("hot/") else (0.12 if k.startswith("warm/") else 0.01)
                n = 1 if random.random() < base else 0
                for _ in range(n):
                    await p.send_and_wait(TOPIC, {"key":k, "ts":now, "action":"read"})
            await asyncio.sleep(0.2)
    finally:
        await p.stop()

if __name__ == "__main__":
    asyncio.run(main())
