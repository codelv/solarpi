import os
import shutil
import argparse
import aiosqlite
import asyncio

from .db import State


async def migrate(path: str):
    new_db_path = f"{path}.part"
    async with aiosqlite.connect(path) as old_db:
        columns = []
        async with old_db.execute("PRAGMA table_info(solar);") as cursor:
            async for row in cursor:
                # cid, name, type, notnull, dfit_value, pk
                columns.append(row[1])
        assert "solar_panel_voltage" in columns # sanity check
        if "solar_panel_current" in columns:
            print("Up to date")
            return # Already migrated

        async with aiosqlite.connect(new_db_path) as new_db:
            await new_db.execute(State.create_table_sql())
            await new_db.commit()

            async with old_db.execute("SELECT * FROM solar") as cursor:
                async for row in cursor:
                    kwargs = {k: v for k, v in zip(columns, row)}
                    if pv := kwargs["solar_panel_voltage"]:
                        solar_panel_current = round(0.95*kwargs["charger_voltage"]/pv*kwargs["charger_current"], 2)
                    else:
                        solar_panel_current = 0
                    kwargs["solar_panel_current"] = solar_panel_current
                    state = State(**kwargs)
                    await new_db.execute(state.insert_values_sql())
            await new_db.commit()
    shutil.move(path, f"{path}.bak")
    shutil.move(new_db_path, path)


async def main():
    parser = argparse.ArgumentParser(
        prog="solarpi-migrate", description="Migrate a solarpi to the latest format"
    )
    parser.add_argument("db")
    args = parser.parse_args()
    if not os.path.exists(args.db):
        raise ValueError("DB path does not exist")
    await migrate(args.db)

if __name__ == "__main__":
    asyncio.run(main())


