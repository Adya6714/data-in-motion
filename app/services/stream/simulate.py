import asyncio, random, json, argparse, time
from datetime import datetime
from aiokafka import AIOKafkaProducer

TOPIC = "file_access"
BOOT = "redpanda:9092"

def pick_weighted(keys, skew):
    weights = [pow(1.0 - i/len(keys), 3) for i,_ in enumerate(keys)]
    if skew is not None:  
        s = sum(weights); weights = [w/s for w in weights]
        weights = [w**(1+3*skew) for w in weights]
        s = sum(weights); weights = [w/s for w in weights]
    return weights

async def main(seed, events, rate, skew):
    random.seed(seed)
    import requests
    files = requests.get("http://localhost:8000/files").json()
    keys = [f["key"] for f in files] or ["media/cat.jpg"]

    weights = pick_weighted(keys, skew)
    p = AIOKafkaProducer(bootstrap_servers=BOOT)
    await p.start()
    try:
        for i in range(events):
            k = random.choices(keys, weights=weights, k=1)[0]
            evt = {"key": k, "action": "read", "ts": int(time.time()*1000)}
            await p.send_and_wait(TOPIC, json.dumps(evt).encode("utf-8"))
            await asyncio.sleep(1.0/rate if rate > 0 else 0.01)
    finally:
        await p.stop()

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--events", type=int, default=3000)
    ap.add_argument("--rate", type=float, default=5.0, help="events/sec")
    ap.add_argument("--skew", type=float, default=0.7, help="0..1 bias to few keys")
    args = ap.parse_args()
    asyncio.run(main(**vars(args)))


