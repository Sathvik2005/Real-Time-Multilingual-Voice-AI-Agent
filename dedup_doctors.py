"""One-time script: remove duplicate doctor rows (seeded twice)."""
import asyncio
import sys

sys.path.insert(0, ".")

from backend.database.connection import get_session_factory
from sqlalchemy import text


async def clean():
    factory = get_session_factory()
    async with factory() as db:
        result = await db.execute(text("SELECT COUNT(*) FROM doctors"))
        before = result.scalar()
        await db.execute(
            text(
                """
                DELETE FROM doctors WHERE doctor_id NOT IN (
                    SELECT MIN(doctor_id) FROM doctors GROUP BY name, specialization
                )
                """
            )
        )
        await db.commit()
        result = await db.execute(text("SELECT COUNT(*) FROM doctors"))
        after = result.scalar()
        print(f"Doctors before: {before}, after: {after} (removed {before - after} duplicates)")


asyncio.run(clean())
