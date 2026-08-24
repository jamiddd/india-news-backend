"""
One-off lookup: lists users (id, email, display_name), most recently
created first. Read-only. Companion to find_user_by_firebase_uid.py for
when you don't have a Firebase UID handy and just want to grab a user_id
to test against (e.g. GET /users/{user_id}/games/stats).

Usage (inside the app container):
    python3 scripts/list_users.py [limit]
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from app.database import engine


async def main(limit: int):
    async with engine.begin() as conn:
        result = await conn.execute(
            text("SELECT id, email, display_name, created_at FROM users ORDER BY created_at DESC LIMIT :limit"),
            {"limit": limit},
        )
        rows = result.all()
        if not rows:
            print("No users found.")
            return
        for row in rows:
            print(f"{row.id}\t{row.email}\t{row.display_name}\t{row.created_at}")


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    asyncio.run(main(limit))
