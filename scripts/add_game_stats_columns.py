"""
One-off migration for existing deployments: adds `score`,
`completion_time_seconds`, and `difficulty` columns (all nullable) to
game_sessions, backing the real streak/level/score stats feature (see
app/models.py's GameSession and GET /users/{user_id}/games/stats).

Safe to run multiple times (IF NOT EXISTS).

Usage:
    python3 scripts/add_game_stats_columns.py
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
        await conn.execute(text(
            "ALTER TABLE game_sessions ADD COLUMN IF NOT EXISTS score INTEGER"
        ))
        await conn.execute(text(
            "ALTER TABLE game_sessions ADD COLUMN IF NOT EXISTS completion_time_seconds INTEGER"
        ))
        await conn.execute(text(
            "ALTER TABLE game_sessions ADD COLUMN IF NOT EXISTS difficulty VARCHAR(32)"
        ))
        logger.info("game_sessions.score/completion_time_seconds/difficulty columns are present.")


if __name__ == "__main__":
    asyncio.run(main())
