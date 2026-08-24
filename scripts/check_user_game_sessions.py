"""
One-off diagnostic: lists a user's recent game_sessions rows, most recently
started first. Read-only. game_sessions is one upserted row per (user,
game_type, puzzle_date) — not an event log — with `completed` +
`completed_at` set once the game finishes. Use this to check whether a
client-side trackComplete() call actually reached the backend — the Android
client's completeGameSession call is fire-and-forget with no error surfacing
(see GameStatsTracker.kt), so a row still showing completed=false after the
user finished a game means the POST either failed silently or never landed.

Usage (inside the app container):
    python3 scripts/check_user_game_sessions.py <user_id> [limit]

Get user_id via scripts/list_users.py or scripts/find_user_by_firebase_uid.py.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from app.database import engine


async def main(user_id: str, limit: int):
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "SELECT game_type, puzzle_date, completed, score, completion_time_seconds, "
                "difficulty, started_at, completed_at FROM game_sessions WHERE user_id = :user_id "
                "ORDER BY started_at DESC LIMIT :limit"
            ),
            {"user_id": user_id, "limit": limit},
        )
        rows = result.all()
        if not rows:
            print("No game_sessions rows found for this user.")
            return
        for row in rows:
            print(
                f"{row.puzzle_date}\t{row.game_type}\tcompleted={row.completed}\t"
                f"score={row.score}\ttime={row.completion_time_seconds}\tdifficulty={row.difficulty}\t"
                f"started_at={row.started_at}\tcompleted_at={row.completed_at}"
            )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/check_user_game_sessions.py <user_id> [limit]")
        sys.exit(1)
    user_id = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    asyncio.run(main(user_id, limit))
