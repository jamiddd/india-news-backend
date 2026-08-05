import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.app.database import AsyncSessionLocal
from backend.app.services.poller import poll_all_sources

async def main():
    print("Starting background RSS polling sweep into PostgreSQL database...")
    async with AsyncSessionLocal() as session:
        count = await poll_all_sources(session)
        print(f"✅ Ingested and clustered {count} new stories!")

if __name__ == "__main__":
    asyncio.run(main())
