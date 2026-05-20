#!/usr/bin/env python
"""Create the first admin user. Run once against a fresh database.

Usage:
    python scripts/seed-admin.py --email admin@local --password secret
"""

import argparse
import asyncio
import sys


async def main() -> None:
    parser = argparse.ArgumentParser(description="Seed an admin user")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--name", default="Admin")
    args = parser.parse_args()

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from api.config import settings
    from api.models.user import User
    from api.services.auth import hash_password

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        existing = await db.execute(select(User).where(User.email == args.email))
        if existing.scalar_one_or_none():
            print(f"User {args.email!r} already exists.", file=sys.stderr)
            await engine.dispose()
            sys.exit(1)

        user = User(
            email=args.email,
            password_hash=hash_password(args.password),
            name=args.name,
            role="admin",
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        print(f"Created admin user {user.email!r} (id={user.id})")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
