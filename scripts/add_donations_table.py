"""
One-off migration for existing deployments: creates the `donations` table and
adds `read_events.event_type`, both introduced by the monetization-v0 work.

`donations` records captured external (Razorpay/UPI) payments for demand
measurement only — see app/models.py's Donation for why it deliberately grants
no entitlement. `read_events.event_type` distinguishes plain story reads from
framing-panel opens and summary expansions, so we can tell "nobody values the
framing angle" apart from "people value it but won't pay".

Safe to run multiple times (IF NOT EXISTS throughout). The event_type backfill
is handled by the column default: existing rows all predate framing
instrumentation and are by definition plain reads.

Usage:
    python3 scripts/add_donations_table.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from app.database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS donations (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(64) REFERENCES users(id) ON DELETE SET NULL,
                amount_paise INTEGER NOT NULL,
                currency VARCHAR(8) NOT NULL DEFAULT 'INR',
                provider VARCHAR(32) NOT NULL DEFAULT 'razorpay',
                provider_payment_id VARCHAR(128) NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'captured',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_donations_user_id ON donations (user_id)"
        ))
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_donations_provider_payment_id "
            "ON donations (provider_payment_id)"
        ))
        logger.info("donations table + indexes are present.")

        await conn.execute(text(
            "ALTER TABLE read_events ADD COLUMN IF NOT EXISTS "
            "event_type VARCHAR(32) NOT NULL DEFAULT 'read'"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_read_events_event_type ON read_events (event_type)"
        ))
        logger.info("read_events.event_type is present.")


if __name__ == "__main__":
    asyncio.run(main())
