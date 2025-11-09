import asyncio, math
from app.services.common.db import SessionLocal, FileMeta

DECAY_1H = 0.95
DECAY_24H = 0.98

async def main():
    print("Decayer running")
    while True:
        with SessionLocal() as s:
            for m in s.query(FileMeta).all():
                m.access_1h = int(math.floor(m.access_1h * DECAY_1H))
                m.access_24h = int(math.floor(m.access_24h * DECAY_24H))
            s.commit()
        await asyncio.sleep(5)

if __name__=="__main__":
    asyncio.run(main())


