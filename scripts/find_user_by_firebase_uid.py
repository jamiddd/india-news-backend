"""
One-off lookup: given a Firebase UID (users.provider_uid), prints our own
internal User.id (the "usr_xxx" id used in every /users/{user_id}/... path,
distinct from the Firebase UID). Read-only.

Usage (inside the app container):
    python3 scripts/find_user_by_firebase_uid.py <firebase_uid>
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from app.database import engine


async def main(firebase_uid: str):
    async with engine.begin() as conn:
        result = await conn.execute(
            text("SELECT id, email, display_name FROM users WHERE provider_uid = :uid"),
            {"uid": firebase_uid},
        )
        row = result.first()
        if row is None:
            print(f"No user found with provider_uid = {firebase_uid}")
        else:
            print(f"user_id: {row.id}")
            print(f"email: {row.email}")
            print(f"display_name: {row.display_name}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 scripts/find_user_by_firebase_uid.py <firebase_uid>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
